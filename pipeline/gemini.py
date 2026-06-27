import os
import base64
import time
import wave
import urllib.parse
import requests
from pipeline.config import (
    GEMINI_API_KEYS, GEMINI_FLASH, GEMINI_PRO, GEMINI_TTS_MODEL, GEMINI_API_BASE
)

import re as _re

def _clean_json_output(raw: str) -> str:
    """Strip markdown fences and sanitize control chars from Gemini JSON output."""
    t = raw.strip()
    # Remove code fences anywhere in the string (handles multiline responses)
    t = _re.sub(r'^```(?:json)?\s*\n?', '', t, flags=_re.MULTILINE)
    t = _re.sub(r'\n?```\s*$', '', t, flags=_re.MULTILINE)
    t = t.strip()
    # Extract outermost JSON object or array (handles preamble/postamble text)
    obj = t.find('{'); arr = t.find('[')
    if obj >= 0 and (arr < 0 or obj < arr):
        end = t.rfind('}')
        if end >= 0:
            t = t[obj:end + 1]
    elif arr >= 0:
        end = t.rfind(']')
        if end >= 0:
            t = t[arr:end + 1]
    return t

def _robust_json_loads(text: str):
    """Parse JSON with two fallback passes to handle Gemini quirks."""
    import json as _json
    cleaned = _clean_json_output(text)
    # Pass 1: strict=False allows 0x00-0x1f control chars in string values
    try:
        return _json.loads(cleaned, strict=False)
    except _json.JSONDecodeError:
        pass
    # Pass 2: escape any remaining bare control chars inside string values
    fixed = []
    in_str = False
    esc = False
    for ch in cleaned:
        if esc:
            fixed.append(ch); esc = False
        elif ch == '\\':
            fixed.append(ch); esc = True
        elif ch == '"':
            in_str = not in_str; fixed.append(ch)
        elif in_str and ord(ch) < 0x20:
            fixed.append('\\u{:04x}'.format(ord(ch)))
        else:
            fixed.append(ch)
    return _json.loads(''.join(fixed))

class TTSError(Exception):
    pass


STATE_FILE = "gemini_state.json"

def _get_next_daily_reset_time() -> float:
    """Calculate the epoch timestamp for the next Google Gemini daily quota reset.

    Google's daily free-tier quota resets at Midnight Pacific Time.
    Pacific Time is UTC-8 (or UTC-7 during DST). We use a conservative
    08:00 UTC (12:00 AM PST) as the daily boundary.
    """
    import datetime
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    target = now_utc.replace(hour=8, minute=0, second=0, microsecond=0)
    if now_utc >= target:
        target += datetime.timedelta(days=1)
    return target.timestamp()


class _KeyPool:
    """Smart Gemini API key pool with cooldowns and git-persisted state."""

    def __init__(self, keys: list[str]):
        if not keys:
            raise RuntimeError(
                "No Gemini API keys configured. Set GEMINI_API_KEY or GEMINI_API_KEYS."
            )
        self._keys = keys
        self._cooldowns = [0.0] * len(keys)
        self._failures = [0] * len(keys)
        self._statuses = ["active"] * len(keys)  # "active", "daily_exhausted", "disabled"
        self._idx = 0
        self._load_state()

    def _load_state(self):
        import json
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
                now = time.time()
                for idx_str, info in state.items():
                    idx = int(idx_str)
                    if 0 <= idx < len(self._keys):
                        status = info.get("status", "active")
                        self._statuses[idx] = status
                        self._failures[idx] = info.get("failures", 0)
                        
                        cd_until = info.get("cooldown_until", 0.0)
                        if cd_until > now:
                            self._cooldowns[idx] = cd_until
            except Exception as e:
                print(f"Warning: Failed to load key pool state: {e}")

    def _save_state(self):
        import json
        state = {}
        for idx in range(len(self._keys)):
            state[str(idx)] = {
                "cooldown_until": self._cooldowns[idx],
                "failures": self._failures[idx],
                "status": self._statuses[idx]
            }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to save key pool state: {e}")

    def get_available_key(self) -> str | None:
        now = time.time()
        for i in range(len(self._keys)):
            candidate_idx = (self._idx + i) % len(self._keys)
            if now >= self._cooldowns[candidate_idx]:
                self._idx = candidate_idx
                return self._keys[candidate_idx]
        return None

    def mark_failed(self, key: str, status_code: int = 429, transient: bool = True):
        if key not in self._keys:
            return
        idx = self._keys.index(key)
        
        now = time.time()
        if not transient:
            if status_code in (400, 403):
                # Permanent credential or project denied error. Block for 10 years.
                self._failures[idx] = max(4, self._failures[idx] + 1)
                self._statuses[idx] = "disabled"
                cooldown_duration = 315360000.0  # 10 years
                self._cooldowns[idx] = now + cooldown_duration
            else:
                # Permanent daily quota exhaustion (429).
                # Reset automatically at Midnight PT (08:00 UTC).
                self._failures[idx] = max(4, self._failures[idx] + 1)
                self._statuses[idx] = "daily_exhausted"
                reset_time = _get_next_daily_reset_time()
                cooldown_duration = reset_time - now
                self._cooldowns[idx] = reset_time
        else:
            # Transient rate limit (RPM/TPM) or server error
            self._statuses[idx] = "active"
            if status_code in (500, 502, 503, 504):
                cooldown_duration = 10.0  # 10s for temporary server errors
            else:
                cooldown_duration = 60.0  # 60s for RPM limits (resets every minute)
            
            # Reset failure count if it was high, as this is transient
            if self._failures[idx] >= 3:
                self._failures[idx] = 1
            self._cooldowns[idx] = now + cooldown_duration

        slot = idx + 1
        print(f"[KeyPool] Key slot {slot}/{len(self._keys)} marked failed (status {status_code}, status_label={self._statuses[idx]}, transient={transient}). Cooldown for {cooldown_duration:.0f}s (Until: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(self._cooldowns[idx]))})")
        self._save_state()

    def mark_success(self, key: str):
        if key not in self._keys:
            return
        idx = self._keys.index(key)
        # Success should clear failures only if the key is not permanently disabled
        if self._statuses[idx] != "disabled":
            self._statuses[idx] = "active"
            if self._failures[idx] != 0:
                self._failures[idx] = 0
                self._save_state()

    def __len__(self) -> int:
        return len(self._keys)


# One shared pool for all GeminiClient instances that don't pin a key
_shared_pool = _KeyPool(GEMINI_API_KEYS)


def _is_daily_quota_exhausted(resp: requests.Response) -> bool:
    """Detect daily (RPD) quota exhaustion vs transient RPM/TPM limits.

    The Gemini API returns a structured quotaId in the details array:
      - 'GenerateRequestsPerDayPerProjectPerModel' → daily RPD limit
      - 'GenerateRequestsPerMinutePerProjectPerModel' → per-minute RPM limit
    We MUST check quotaId first. Only treat as daily if:
      1) quotaId explicitly contains 'PerDay', OR
      2) details mention 'free_tier_requests' or 'perday' (legacy format)
    If 'perminute' or 'persecond' appears ANYWHERE, it is NOT daily.
    """
    import json as _json
    try:
        data = resp.json()
        error_data = data.get("error", {})
        details = error_data.get("details", [])
        details_str = _json.dumps(details).lower()

        # Explicit per-minute/per-second → definitely NOT daily
        if "perminute" in details_str or "persecond" in details_str:
            return False

        # Check structured quotaId for daily limit
        for detail in details:
            for violation in detail.get("violations", []):
                quota_id = violation.get("quotaId", "").lower()
                if "perday" in quota_id:
                    return True

        # Legacy: free_tier_requests metric or perday in raw details
        if "free_tier_requests" in details_str or "perday" in details_str:
            return True

    except Exception:
        pass

    # Fallback: raw text scan (only if no structured data parsed)
    try:
        text_lower = resp.text.lower()
        # If per-minute appears, not daily
        if "perminute" in text_lower or "per_minute" in text_lower:
            return False
        if "free_tier_requests" in text_lower or "perday" in text_lower:
            return True
        # Only match "daily" if explicitly said (NOT "quota exceeded" which is generic!)
        if ("daily limit" in text_lower or "queries per day" in text_lower):
            return True
    except Exception:
        pass
    return False


def _post_with_rotation(
    url_template: str, payload: dict, timeout: int = 120, quick: bool = False
) -> requests.Response:
    """
    POST using the shared key pool with backoffs and git-persisted cooldowns.
    """
    max_attempts = len(_shared_pool) if quick else len(_shared_pool) * 4
    for attempt in range(max_attempts):
        key = _shared_pool.get_available_key()
        if not key:
            # All keys are on cooldown! Check if ALL are permanently dead
            now = time.time()
            all_permanent = all(
                _shared_pool._cooldowns[i] > now + 3600
                for i in range(len(_shared_pool._keys))
            )
            if all_permanent:
                raise RuntimeError(
                    "Gemini: all keys permanently exhausted (daily quota or disabled). "
                    "Pipeline cannot proceed. Will auto-recover on next cron slot."
                )
            earliest_idx = min(range(len(_shared_pool)), key=lambda idx: _shared_pool._cooldowns[idx])
            wait_time = max(1.0, _shared_pool._cooldowns[earliest_idx] - now)
            wait_time = min(15.0, wait_time)  # cap to 15s max sleep
            print(f"[GeminiClient] All keys on cooldown. Waiting {wait_time:.1f} s for key slot {earliest_idx+1}...")
            time.sleep(wait_time)
            continue

        url = url_template.format(key=key)
        slot = _shared_pool._keys.index(key) + 1
        
        # Try up to 3 times with the same key for transient issues
        same_key_attempts = 3
        
        for k_attempt in range(same_key_attempts):
            try:
                resp = requests.post(
                    url, json=payload, timeout=timeout,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    _shared_pool.mark_success(key)
                    return resp
                    
                if resp.status_code == 429:
                    # Log the actual quota violation for debugging
                    try:
                        _err = resp.json().get("error", {})
                        _details = _err.get("details", [])
                        _quota_ids = [
                            v.get("quotaId", "?")
                            for d in _details
                            for v in d.get("violations", [])
                        ]
                        print(f"[GeminiClient] 429 on slot {slot}: quotaIds={_quota_ids}, msg={_err.get('message', '?')[:80]}")
                    except Exception:
                        print(f"[GeminiClient] 429 on slot {slot}: raw={resp.text[:200]}")

                    if _is_daily_quota_exhausted(resp):
                        print(f"[GeminiClient] → Classified as DAILY quota (RPD). Disabling key slot {slot} for 24h.")
                        _shared_pool.mark_failed(key, 429, transient=False)
                        _shared_pool._idx += 1
                        break  # Break inner loop to rotate key
                    else:
                        # RPM limit: retry with backoff or rotate if out of attempts
                        if k_attempt < same_key_attempts - 1:
                            wait_s = (k_attempt + 1) * 3
                            print(f"[GeminiClient] 429 RPM limit on key slot {slot} (attempt {k_attempt+1}/{same_key_attempts}). Waiting {wait_s}s...")
                            time.sleep(wait_s)
                            continue
                        else:
                            print(f"[GeminiClient] 429 RPM limit persisted on key slot {slot}. Rotating…")
                            _shared_pool.mark_failed(key, 429, transient=True)
                            _shared_pool._idx += 1
                            break
                            
                elif resp.status_code in (500, 502, 503, 504):
                    if k_attempt < same_key_attempts - 1:
                        wait_s = (k_attempt + 1) * 2
                        print(f"[GeminiClient] {resp.status_code} server error on key slot {slot} (attempt {k_attempt+1}/{same_key_attempts}). Waiting {wait_s}s...")
                        time.sleep(wait_s)
                        continue
                    else:
                        print(f"[GeminiClient] {resp.status_code} persisted on key slot {slot}. Rotating…")
                        _shared_pool.mark_failed(key, resp.status_code, transient=True)
                        _shared_pool._idx += 1
                        break
                        
                elif resp.status_code in (400, 403):
                    # Check if it is a credential error vs safety block
                    err_msg = ""
                    try:
                        err_msg = resp.json().get("error", {}).get("message", "")
                    except Exception:
                        err_msg = resp.text
                        
                    is_cred_err = any(word in err_msg.lower() for word in [
                        "valid", "key", "blocked", "unauthorized", "api_key",
                        "denied", "access", "permission", "disabled", "project"
                    ])
                    if is_cred_err or resp.status_code == 403:
                        # ALL 403s are treated as permanent — either credential issue
                        # or project-level block. Don't spin on a dead key.
                        print(f"[GeminiClient] Credential/access error {resp.status_code} on key slot {slot}: {err_msg}. Disabling key permanently…")
                        _shared_pool.mark_failed(key, resp.status_code, transient=False)
                        _shared_pool._idx += 1
                        break
                    else:
                        print(f"[GeminiClient] HTTP {resp.status_code} client error on key slot {slot}: {err_msg}")
                        resp.raise_for_status()
                else:
                    resp.raise_for_status()
                    
            except requests.exceptions.RequestException as exc:
                if k_attempt < same_key_attempts - 1:
                    wait_s = (k_attempt + 1) * 2
                    print(f"[GeminiClient] Network error on key slot {slot} (attempt {k_attempt+1}/{same_key_attempts}): {exc}. Waiting {wait_s}s...")
                    time.sleep(wait_s)
                    continue
                else:
                    print(f"[GeminiClient] Network error persisted on key slot {slot}. Rotating…")
                    _shared_pool.mark_failed(key, 0, transient=True)
                    _shared_pool._idx += 1
                    break
                    
    raise RuntimeError("Gemini: all keys exhausted. Try again later.")


class GeminiClient:
    """
    Thin wrapper around Gemini REST API.
    Pass api_key to pin a specific key (used by Judge).
    Omit api_key to use the shared rotating pool.
    """

    def __init__(self, api_key: str | None = None):
        self._pinned = api_key

    def _post(self, url_tmpl: str, payload: dict, timeout: int = 120, quick: bool = False) -> requests.Response:
        if self._pinned:
            url = url_tmpl.format(key=self._pinned)
            for attempt in range(5):
                try:
                    resp = requests.post(
                        url, json=payload, timeout=timeout,
                        headers={"Content-Type": "application/json"},
                    )
                    if resp.status_code == 429:
                        if _is_daily_quota_exhausted(resp):
                            print("[GeminiClient][pinned] Pinned key daily quota exhausted! Falling back to shared pool.")
                            break
                        wait = (attempt + 1) * 10
                        print(f"[GeminiClient][pinned] 429. Waiting {wait}s…")
                        time.sleep(wait)
                        continue
                    if resp.status_code in (500, 502, 503, 504):
                        wait = (attempt + 1) * 5
                        print(f"[GeminiClient][pinned] {resp.status_code}. Waiting {wait}s…")
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    return resp
                except Exception as e:
                    print(f"[GeminiClient][pinned] Attempt {attempt+1} failed: {e}")
                    if attempt == 4:
                        break
            print("[GeminiClient][pinned] Pinned key failed. Falling back to rotating pool.")
        return _post_with_rotation(url_tmpl, payload, timeout, quick)


    # ── Text generation ──────────────────────────────────────────────────────

    def generate_text(
        self,
        prompt: str,
        use_grounding: bool = False,
        temperature: float = 0.8,
        max_tokens: int = 8192,
        model: str = None
    ) -> str:
        # Default to Flash for everything. Only script generation passes model=GEMINI_PRO.
        model_name = model or GEMINI_FLASH
        url = f"{GEMINI_API_BASE}/models/{model_name}:generateContent?key={{key}}"
        gen_config: dict = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if not use_grounding:
            gen_config["responseMimeType"] = "application/json"
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        if use_grounding:
            payload["tools"] = [{"google_search": {}}]

        # If using Pro, try ONE key only — don't burn all keys on Pro's 5 RPD limit
        if model_name != GEMINI_FLASH:
            key = self.get_available_key()
            if key:
                try:
                    single_url = url.replace("{key}", key)
                    resp = requests.post(single_url, json=payload, timeout=120)
                    if resp.status_code == 200:
                        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                        print(f"[GeminiClient] ✅ {model_name} succeeded on first attempt.")
                        return _clean_json_output(text)
                    else:
                        print(f"[GeminiClient] {model_name} returned {resp.status_code}. Falling back to {GEMINI_FLASH}...")
                except Exception as exc:
                    print(f"[GeminiClient] {model_name} failed: {exc}. Falling back to {GEMINI_FLASH}...")
            else:
                print(f"[GeminiClient] No keys available for {model_name}. Falling back to {GEMINI_FLASH}...")
            # Fall through to Flash
            url = f"{GEMINI_API_BASE}/models/{GEMINI_FLASH}:generateContent?key={{key}}"

        resp = self._post(url, payload)
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        return _clean_json_output(text)

    # ── Image generation (Pollinations – no key needed) ──────────────────────

    def generate_image(self, prompt: str, width: int = 1080, height: int = 1920) -> bytes:
        encoded = urllib.parse.quote(prompt)
        for model in ["flux", "flux-realism", "turbo"]:
            try:
                url = (
                    f"https://image.pollinations.ai/prompt/{encoded}"
                    f"?width={width}&height={height}&model={model}&nologo=true"
                )
                r = requests.get(url, timeout=90)
                if r.status_code == 200 and len(r.content) > 5000:
                    return r.content
            except Exception as e:
                print(f"[GeminiClient] Pollinations {model} failed: {e}")
        raise RuntimeError("All Pollinations models failed")

    # ── TTS ──────────────────────────────────────────────────────────────────

    def generate_tts(self, text: str, voice: str = "Aoede", vocal_tone: str = None) -> tuple[bytes, str]:
        """Returns (audio_bytes, mime_type). Raises TTSError on failure."""
        url = f"{GEMINI_API_BASE}/models/{GEMINI_TTS_MODEL}:generateContent?key={{key}}"
        
        tone_prompts = {
            "dramatic_whisper": (
                "You are narrating a late-night documentary. Speak in a low, intimate, "
                "atmospheric whisper — as if revealing a secret to one person in a dark room. "
                "Every word should feel heavy with meaning. Pause slightly before key reveals. "
                "Clear pronunciation, but the energy is restrained and magnetic"
            ),
            "suspenseful_mystery": (
                "You are narrating a true-crime or unsolved mystery documentary. "
                "Build tension with your pacing — slow down before the twist, speed up during action. "
                "Your tone should make the listener lean in. Use dramatic pauses before shocking facts. "
                "Think of it like whispering a ghost story around a campfire"
            ),
            "energetic_storytelling": (
                "You are an enthusiastic science communicator on stage. Speak with infectious energy, "
                "genuine excitement, and warmth — like you just discovered something incredible "
                "and can't wait to share it. Vary your pitch naturally. Emphasize mind-blowing numbers "
                "and facts with a slight rise in energy. Make the listener feel your passion"
            ),
            "deep_curiosity": (
                "You are a calm, thoughtful narrator exploring a profound mystery of the universe. "
                "Speak with wonder and reverence — as if standing at the edge of a canyon, "
                "contemplating something vast. Your pace is measured, your tone is rich and warm. "
                "Let moments of silence breathe between big ideas"
            ),
        }
        # Validate vocal_tone — if it's not a recognized key, fall back to default
        if vocal_tone and vocal_tone not in tone_prompts:
            print(f"[TTS] Unknown vocal_tone '{vocal_tone}', using default.")
            vocal_tone = None
        prefix = tone_prompts.get(vocal_tone, "Say this clearly with natural, engaging pacing")
        
        payload = {
            "contents": [{"role": "user", "parts": [
                {"text": f"{prefix}: {text}"}
            ]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                },
            },
        }
        try:
            resp = self._post(url, payload, quick=True)
        except Exception as exc:
            raise TTSError(str(exc)) from exc
        try:
            inline = resp.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            return base64.b64decode(inline["data"]), inline["mimeType"]
        except Exception as exc:
            raise TTSError(f"TTS response parse error: {exc}") from exc

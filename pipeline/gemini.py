import os
import base64
import time
import wave
import urllib.parse
import requests
from pipeline.config import (
    GEMINI_API_KEYS, GEMINI_FLASH, GEMINI_TTS_MODEL, GEMINI_API_BASE
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
                        cd_until = info.get("cooldown_until", 0.0)
                        if cd_until > now:
                            self._cooldowns[idx] = cd_until
                        self._failures[idx] = info.get("failures", 0)
            except Exception as e:
                print(f"Warning: Failed to load key pool state: {e}")

    def _save_state(self):
        import json
        state = {}
        for idx in range(len(self._keys)):
            state[str(idx)] = {
                "cooldown_until": self._cooldowns[idx],
                "failures": self._failures[idx]
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
            # Permanent daily quota exhaustion or key invalidation
            self._failures[idx] = max(4, self._failures[idx] + 1)
            cooldown_duration = 86400.0  # 24 hours
        else:
            # Transient rate limit (RPM/TPM) or server error
            if status_code in (500, 502, 503, 504):
                cooldown_duration = 10.0  # 10s for temporary server errors
            else:
                cooldown_duration = 60.0  # 60s for RPM limits (resets every minute)
            
            # Reset failure count if it was high, as this is transient
            if self._failures[idx] >= 3:
                self._failures[idx] = 1

        self._cooldowns[idx] = now + cooldown_duration
        slot = idx + 1
        print(f"[KeyPool] Key slot {slot}/{len(self._keys)} marked failed (status {status_code}, transient={transient}). Cooldown for {cooldown_duration:.0f}s (Until: {time.strftime('%H:%M:%S', time.localtime(self._cooldowns[idx]))})")
        self._save_state()

    def mark_success(self, key: str):
        if key not in self._keys:
            return
        idx = self._keys.index(key)
        if self._failures[idx] != 0:
            self._failures[idx] = 0
            self._save_state()

    def __len__(self) -> int:
        return len(self._keys)


# One shared pool for all GeminiClient instances that don't pin a key
_shared_pool = _KeyPool(GEMINI_API_KEYS)


def _is_daily_quota_exhausted(resp: requests.Response) -> bool:
    try:
        data = resp.json()
        error_data = data.get("error", {})
        msg = error_data.get("message", "").lower()
        import json
        details_str = json.dumps(error_data.get("details", [])).lower()
        if "perday" in details_str or "free_tier_requests" in details_str:
            return True
        if ("day" in msg or "daily" in msg or "free_tier_requests" in msg or "quota exceeded" in msg) and "minute" not in msg and "second" not in msg:
            return True
    except Exception:
        pass
    try:
        text_lower = resp.text.lower()
        if "free_tier_requests" in text_lower or "perday" in text_lower:
            return True
        if ("daily limit" in text_lower or "queries per day" in text_lower) and "minute" not in text_lower and "second" not in text_lower:
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
            # All keys are on cooldown! Find the one that finishes earliest
            now = time.time()
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
                    if _is_daily_quota_exhausted(resp):
                        print(f"[GeminiClient] Daily quota exhausted on key slot {slot}. Rotating…")
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
                        
                    is_cred_err = any(word in err_msg.lower() for word in ["valid", "key", "blocked", "unauthorized", "api_key"])
                    if is_cred_err:
                        print(f"[GeminiClient] Credential error {resp.status_code} on key slot {slot}: {err_msg}. Disabling key…")
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
    ) -> str:
        url = f"{GEMINI_API_BASE}/models/{GEMINI_FLASH}:generateContent?key={{key}}"
        gen_config: dict = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        # JSON mode is incompatible with google_search grounding tool
        if not use_grounding:
            gen_config["responseMimeType"] = "application/json"
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        if use_grounding:
            payload["tools"] = [{"google_search": {}}]

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

    def generate_tts(self, text: str, voice: str = "Aoede") -> tuple[bytes, str]:
        """Returns (audio_bytes, mime_type). Raises TTSError on failure."""
        url = f"{GEMINI_API_BASE}/models/{GEMINI_TTS_MODEL}:generateContent?key={{key}}"
        payload = {
            "contents": [{"role": "user", "parts": [
                {"text": f"Say this clearly with natural pacing: {text}"}
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

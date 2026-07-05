import os
import json
import time
import requests
import mimetypes
from pipeline.config import GEMINI_FLASH, GEMINI_API_BASE
from pipeline.gemini import _clean_json_output, _shared_pool


RETRIABLE_STATUS_CODES = {400, 403, 429, 500, 502, 503, 504}


def _http_status(exc: Exception) -> int:
    response = getattr(exc, "response", None)
    return int(getattr(response, "status_code", 0) or 0)


def _get_judge_key() -> str | None:
    key = _shared_pool.get_available_key()
    if key:
        return key
    now = time.time()
    earliest_idx = min(range(len(_shared_pool)), key=lambda idx: _shared_pool._cooldowns[idx])
    wait_time = max(1.0, _shared_pool._cooldowns[earliest_idx] - now)
    wait_time = min(15.0, wait_time)
    print(f"[JudgeAI] All Gemini keys on cooldown. Waiting {wait_time:.1f}s for key slot {earliest_idx + 1}...")
    time.sleep(wait_time)
    return None

def upload_file_to_gemini(filepath: str, api_key: str) -> dict:
    mime_type, _ = mimetypes.guess_type(filepath)
    if not mime_type:
        mime_type = "video/mp4"
        
    file_size = os.path.getsize(filepath)
    filename = os.path.basename(filepath)
    
    print(f"Uploading file '{filename}' ({file_size / (1024*1024):.2f} MB) to Gemini Files API...")
    
    url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?uploadType=media&key={api_key}"
    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
    }
    
    with open(filepath, "rb") as f:
        file_bytes = f.read()
        
    for attempt in range(4):
        try:
            response = requests.post(url, headers=headers, data=file_bytes, timeout=300)
            if response.status_code == 429:
                from pipeline.gemini import _is_daily_quota_exhausted
                if _is_daily_quota_exhausted(response):
                    print("[JudgeAI] Upload call daily quota exhausted. Rotating immediately.")
                    raise requests.exceptions.HTTPError("Daily quota exhausted during upload", response=response)
                wait_s = (attempt + 1) * 10
                print(f"[JudgeAI] Upload 429 rate limit. Retrying in {wait_s}s...")
                time.sleep(wait_s)
                continue
            if response.status_code in (500, 502, 503, 504):
                wait_s = (attempt + 1) * 5
                print(f"[JudgeAI] Upload {response.status_code} server error. Retrying in {wait_s}s...")
                time.sleep(wait_s)
                continue
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if attempt == 3:
                raise
            wait_s = (attempt + 1) * 5
            print(f"[JudgeAI] Upload network error: {e}. Retrying in {wait_s}s...")
            time.sleep(wait_s)
            
    raise RuntimeError("Failed to upload video file after retries.")


def wait_for_file_active(file_name: str, api_key: str, max_timeout_seconds: int = 180) -> bool:
    url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}"
    print(f"Waiting for Gemini Files API to process video '{file_name}'...")
    
    start_time = time.time()
    while time.time() - start_time < max_timeout_seconds:
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 429:
                from pipeline.gemini import _is_daily_quota_exhausted
                if _is_daily_quota_exhausted(response):
                    print("[JudgeAI] File status call daily quota exhausted. Rotating immediately.")
                    raise requests.exceptions.HTTPError("Daily quota exhausted during file status check", response=response)
                print("[JudgeAI] Polling file status returned 429. Waiting 10 seconds...")
                time.sleep(10)
                continue
            if response.status_code in (500, 502, 503, 504):
                print(f"[JudgeAI] Polling file status returned {response.status_code}. Waiting 5 seconds...")
                time.sleep(5)
                continue
            response.raise_for_status()
            data = response.json()
            state = data.get("state")
            
            if state == "ACTIVE":
                print("Video file is now ACTIVE and ready for query.")
                return True
            elif state == "FAILED":
                raise RuntimeError(f"File processing failed on Gemini Files API: {data}")
            else:
                print(f"Current file state is '{state}'. Retrying in 5 seconds...")
                time.sleep(5)
        except requests.exceptions.RequestException as e:
            print(f"[JudgeAI] Polling status network/HTTP error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
            
    raise TimeoutError("Timeout exceeded waiting for Gemini Files API to activate the file")

def delete_file_from_gemini(file_name: str, api_key: str):
    url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}"
    for attempt in range(3):
        try:
            print(f"Cleaning up temporary file {file_name} from Gemini storage (attempt {attempt+1}/3)...")
            response = requests.delete(url, timeout=30)
            if response.status_code == 429:
                time.sleep(5)
                continue
            response.raise_for_status()
            print("File deleted successfully.")
            return
        except Exception as e:
            if attempt == 2:
                print(f"Warning: Failed to delete temporary file {file_name}: {e}")
            time.sleep(3)

class JudgeClient:
    def __init__(self):
        self.base_url = GEMINI_API_BASE
        
    def review_video(self, video_path: str, metadata: dict) -> dict:
        last_error: Exception | None = None
        max_attempts = max(1, len(_shared_pool) * 2)
        for attempt in range(max_attempts):
            api_key = _get_judge_key()
            if not api_key:
                continue
            slot = _shared_pool._keys.index(api_key) + 1
            try:
                report = self._review_video_with_key(video_path, metadata, api_key)
                _shared_pool.mark_success(api_key)
                return report
            except Exception as exc:
                last_error = exc
                status = _http_status(exc)
                print(f"[JudgeAI] Key slot {slot}/{len(_shared_pool)} failed during review (status {status or 'unknown'}): {exc}")
                if status in RETRIABLE_STATUS_CODES or status == 0:
                    from pipeline.gemini import _is_daily_quota_exhausted
                    response_obj = getattr(exc, "response", None)
                    is_daily = response_obj is not None and _is_daily_quota_exhausted(response_obj)
                    _shared_pool.mark_failed(api_key, status or 0, transient=not is_daily)
                    _shared_pool._idx += 1
                    continue
                raise
        raise RuntimeError("Judge AI: all Gemini keys exhausted during video review.") from last_error

    def _review_video_with_key(self, video_path: str, metadata: dict, api_key: str) -> dict:
        slot = _shared_pool._keys.index(api_key) + 1
        file_name = None
        try:
            # 1. Upload video
            upload_response = upload_file_to_gemini(video_path, api_key)
            file_info = upload_response.get("file", {})
            file_name = file_info.get("name")
            file_uri = file_info.get("uri")
            mime_type = file_info.get("mimeType")
            
            if not file_name or not file_uri:
                raise RuntimeError(f"Unexpected file upload response: {upload_response}")
                
            # 2. Wait for active status
            wait_for_file_active(file_name, api_key)
            
            # 3. Formulate Prompt
            rubric = f"""You are "Judge AI" (an expert viral media director and quality assurance LLM). Your task is to evaluate the generated educational video and ensure it meets our strict viral criteria.

Video Metadata:
{json.dumps(metadata, indent=2)}

Please watch the video and evaluate it against these 5 rubrics:
1. **Cohesiveness & Alignment (CRITICAL)**: Does the voiceover audio match the visual B-roll clips and the text captions shown on screen?
- Check for any mismatch (e.g. if the audio discusses "Quantum Computing" but the text caption or B-roll displays terms like "CRISPR" or "Gene Editing").
   - Look out for generic or symbolic placeholders (e.g. a generic man with glasses looking at a screen, generic office workers) that do not directly represent specific scientific/technical/space concepts described in the audio (like 'asteroid wobble', 'planetary defense', 'Bose-Einstein condensate', etc.).
   - IMPORTANT EXCEPTION (Scientific Abstractions): Stock video libraries DO NOT have specialized animations for exact scientific terms (e.g., specific protein names, TMAO molecules, specific rare fish like Mariana snailfish). You MUST ACCEPT generic scientific abstractions (e.g., "symbolic squishy balls", glowing orbs, fluid dynamics, generic laboratories, generic underwater scenes/bubbles, generic deep sea fish) as VALID matches for specific microscopic, chemical, or biological narration. Do NOT penalize or fail the video for these abstractions.
   - IMPORTANT: Skip false alarms for abstract concepts. When the narration discusses abstract ideas like "profound implications", "mysteries of life", or "time passing", broader thematic visuals (like sunsets, horizons, oceans, or glowing particles) are valid artistic choices and MUST NOT be flagged as generic placeholders.
   - However, you MUST STILL REJECT completely contradictory or jarring mismatches (e.g., showing a cityscape or smokestack when discussing deep sea biology, or showing a desert when discussing ocean water).
   - Check if the SAME visual clip is repeated or looped twice in different parts of the video. Repeating the same B-roll clip is a critical quality failure.
   - If there is any mismatched topic (like a cityscape for the ocean), symbolic placeholder (except for abstract concepts and scientific abstractions as noted above), or repeated clip, you MUST set status="REJECTED" and score below 80, and list the exact segment numbers that failed.
2. **Hook Appeal**: Is the hook in the first 3-5 seconds of the video engaging and curiosity-inducing?
3. **Subtitles/Captions (CRITICAL)**: Are subtitles present, readable, and synchronized with the narration?
   - This video uses modern rapid-fire single-word (karaoke) subtitle style. This is EXPECTED and CORRECT.
   - REJECT if: subtitles are visibly out of sync (words appearing long after they are spoken, or appearing before), if subtitles get "stuck" on one word while narration moves on, or if subtitles disappear mid-video.
   - REJECT if: the title hook text card at the start of the video (first 1-2 seconds) has text that goes outside the frame boundaries or is cut off on either side. Text must be fully visible and centered.
   - ACCEPT if: subtitles are single-word style and appear roughly in sync (within 0.5 seconds of spoken word).
4. **Music & Audio Quality**: Is the background music clean, and is it mixed correctly without overpowering the voiceover?
5. **Retention & Loopability**: Does the video contain a retention element (like a rewatch callout in segment 4)? Does it loop back seamlessly from the last segment to the first segment's narration? Note: Segment 5 echoing Segment 1's THEME (not its exact wording) is the desired outcome — flag verbatim repetition of Segment 1's sentence as a script-quality issue.

Scoring target: a publishable video should land around 91-94 when it has coherent visuals, readable captions, clean audio, strong hook, and no repeated clips. Reserve 80-90 for technically acceptable but weak videos that should be repaired before publishing.

You MUST return your review ONLY as a raw JSON object with no markdown syntax. The JSON structure must be exactly like this:
{{
  "score": 91, // Overall quality score (0-100)
  "status": "PASSED", // "PASSED" if score >= 91 and no critical mismatches/repeated clips, otherwise "REJECTED"
  "reason": "Explain the decision in detail",
  "cohesiveness_score": 91, // 0-100 score for audio-visual-caption matching
  "hook_score": 91, // 0-100 score for hook appeal
  "retention_score": 91, // 0-100 score for looping and retention triggers
  "failed_segments": [3, 4], // 0-based indices of segments that had bad B-roll, generic placeholders, or mismatches, or empty [] if none
  "issues": ["List of specific issues found, or empty if none"]
}}
"""
            
            # 4. Generate Review Content (Primary: Gemini 2.5 Flash, Fallback: Gemini 2.5 Flash)
            model_to_use = GEMINI_FLASH
            url = f"{self.base_url}/models/{model_to_use}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"fileData": {"mimeType": mime_type, "fileUri": file_uri}},
                            {"text": rubric}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json"
                }
            }
            
            print(f"Sending video to model '{model_to_use}' for analysis...")
            response = None
            for attempt in range(4):
                try:
                    response = requests.post(url, headers=headers, json=payload, timeout=180)
                    if response.status_code == 429:
                        from pipeline.gemini import _is_daily_quota_exhausted
                        if _is_daily_quota_exhausted(response):
                            print(f"[JudgeAI] Daily quota exhausted on key slot {slot}. Rotating immediately.")
                            raise requests.exceptions.HTTPError("Daily quota exhausted during review", response=response)
                        wait_s = (attempt + 1) * 15
                        print(f"[JudgeAI] Review call 429 rate limit. Waiting {wait_s}s...")
                        time.sleep(wait_s)
                        continue
                    if response.status_code in (500, 502, 503, 504):
                        wait_s = (attempt + 1) * 5
                        print(f"[JudgeAI] Review call {response.status_code} server error. Waiting {wait_s}s...")
                        time.sleep(wait_s)
                        continue
                    response.raise_for_status()
                    break
                except requests.exceptions.RequestException as e:
                    if attempt == 3:
                        raise
                    wait_s = (attempt + 1) * 5
                    print(f"[JudgeAI] Review call network error: {e}. Waiting {wait_s}s...")
                    time.sleep(wait_s)
                    
            if response is None:
                raise RuntimeError("Failed to get review response after retries")
            response_data = response.json()
            
            # Extract and parse response
            try:
                text_response = response_data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as parse_err:
                # If primary failed, we will trigger the fallback check in the except block
                raise RuntimeError(f"Unexpected response format: {response_data}") from parse_err
                
            report = json.loads(_clean_json_output(text_response))
            print(f"Judge AI Review complete. Status: {report.get('status')} (Score: {report.get('score')}/100)")
            return report
            
        except Exception as model_err:
            print(f"Primary model review failed: {model_err}. Falling back to {GEMINI_FLASH}...")
            model_to_use = GEMINI_FLASH
            url_fallback = f"{self.base_url}/models/{model_to_use}:generateContent?key={api_key}"
            payload["generationConfig"] = {"temperature": 0.2}
            
            response = None
            for attempt in range(4):
                try:
                    response = requests.post(url_fallback, headers=headers, json=payload, timeout=180)
                    if response.status_code == 429:
                        from pipeline.gemini import _is_daily_quota_exhausted
                        if _is_daily_quota_exhausted(response):
                            print(f"[JudgeAI][fallback] Daily quota exhausted on key slot {slot}. Rotating immediately.")
                            raise requests.exceptions.HTTPError("Daily quota exhausted during fallback review", response=response)
                        wait_s = (attempt + 1) * 15
                        print(f"[JudgeAI][fallback] 429 rate limit. Waiting {wait_s}s...")
                        time.sleep(wait_s)
                        continue
                    if response.status_code in (500, 502, 503, 504):
                        wait_s = (attempt + 1) * 5
                        print(f"[JudgeAI][fallback] {response.status_code} server error. Waiting {wait_s}s...")
                        time.sleep(wait_s)
                        continue
                    response.raise_for_status()
                    break
                except requests.exceptions.RequestException as e:
                    if attempt == 3:
                        raise
                    wait_s = (attempt + 1) * 5
                    print(f"[JudgeAI][fallback] Network error: {e}. Waiting {wait_s}s...")
                    time.sleep(wait_s)
                    
            if response is None:
                raise RuntimeError("Failed to get fallback review response after retries")
            response_data = response.json()
            
            try:
                text_response = response_data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as parse_err:
                raise RuntimeError(f"Unexpected fallback response format: {response_data}") from parse_err
                
            report = json.loads(_clean_json_output(text_response))
            print(f"Judge AI Review complete via fallback. Status: {report.get('status')} (Score: {report.get('score')}/100)")
            return report
            
        finally:
            # Clean up the file in Gemini storage
            if file_name:
                delete_file_from_gemini(file_name, api_key)

import os
import random
import wave
import json
from pipeline.config import GEMINI_VOICES, KOKORO_VOICES
from pipeline.gemini import GeminiClient

STATE_PATH = "voice_state.json"

def pick_voice(pool: list[str], state_key: str) -> str:
    state = {}
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r") as f:
                state = json.load(f)
        except Exception:
            pass
    last = state.get(state_key)
    choice = random.choice([v for v in pool if v != last] or pool)
    state[state_key] = choice
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Warning: Failed to write voice state: {e}")
    return choice

def generate_audio(script: dict) -> list[str]:
    """
    Generates TTS for all segments using a SINGLE voice for the whole video.
    Engine decision: try Gemini first. If any segment fails, redo ALL
    segments with Kokoro — never mix engines within one video.
    Inter-video voice rotation is handled by pick_voice() which persists
    the last-used voice to voice_state.json.
    """
    gemini_client = GeminiClient()
    os.makedirs("output", exist_ok=True)

    # Pick one voice per engine — persisted across videos for rotation
    gemini_voice = pick_voice(GEMINI_VOICES, "gemini")
    ko_voice     = pick_voice(KOKORO_VOICES, "kokoro")

    segments = script["segments"]

    # ── Pass 1: Try Gemini for ALL segments ──────────────────────────────────
    print(f"[TTS] Using Gemini voice '{gemini_voice}' for this video.")
    gemini_results: dict[int, str] = {}   # seg_id → filepath
    gemini_failed  = False

    for seg in segments:
        seg_id   = seg["id"]
        out_path = f"output/tts_segment_{seg_id}.wav"

        # Use cached file if valid
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            print(f"[TTS] Segment {seg_id}: cached, skipping.")
            gemini_results[seg_id] = out_path
            continue

        try:
            vocal_tone = script.get("vocal_tone")
            audio_bytes, mime_type = gemini_client.generate_tts(
                seg["narration"], voice=gemini_voice, vocal_tone=vocal_tone
            )
            if audio_bytes.startswith(b"RIFF") or "wav" in mime_type.lower():
                with open(out_path, "wb") as wf:
                    wf.write(audio_bytes)
            else:
                import wave
                with wave.open(out_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(audio_bytes)
            print(f"[TTS] Segment {seg_id}: Gemini OK.")
            gemini_results[seg_id] = out_path
        except Exception as e:
            print(f"[TTS] Segment {seg_id}: Gemini FAILED — {e}")
            gemini_failed = True
            break   # Stop Gemini pass immediately; will redo all with Kokoro

    if not gemini_failed:
        # All segments succeeded with Gemini — return in order
        return [gemini_results[seg["id"]] for seg in segments]

    # ── Pass 2: Gemini failed for at least one segment.
    #    Delete any partial Gemini files and redo ALL segments with Kokoro.
    #    This guarantees a single consistent voice across the whole video.
    print(f"[TTS] Gemini failed — switching entire video to Kokoro '{ko_voice}'.")
    for seg in segments:
        p = f"output/tts_segment_{seg['id']}.wav"
        if os.path.exists(p):
            os.remove(p)   # remove partial Gemini output

    try:
        import wave
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline
        pipeline_ko = KPipeline(lang_code="a")
    except ImportError as e:
        raise RuntimeError(f"Kokoro not available and Gemini failed: {e}")

    audio_files = []
    for seg in segments:
        seg_id   = seg["id"]
        out_path = f"output/tts_segment_{seg_id}.wav"
        samples  = []
        for _, _, audio in pipeline_ko(seg["narration"], voice=ko_voice, speed=1.0):
            samples.append(audio)
        audio_np   = np.concatenate(samples)
        audio_i16  = np.clip(audio_np * 32767, -32768, 32767).astype(np.int16)
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(audio_i16.tobytes())
        print(f"[TTS] Segment {seg_id}: Kokoro OK.")
        audio_files.append(out_path)

    return audio_files

import os
import random
import json
import wave
import subprocess

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

def get_wav_duration(filepath: str) -> float:
    with wave.open(filepath, 'rb') as f:
        frames = f.getnframes()
        rate = f.getframerate()
        return frames / float(rate)

def split_combined_audio(combined_path: str, segments: list[dict]):
    import subprocess
    # First, try Whisper word alignment
    try:
        from faster_whisper import WhisperModel
        print("[TTS] Loading faster-whisper 'base' model on CPU for segmentation...")
        model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=1, num_workers=1)
        segments_out, info = model.transcribe(combined_path, word_timestamps=True)
        
        whisper_words = []
        for whisper_seg in segments_out:
            if whisper_seg.words:
                for word_info in whisper_seg.words:
                    w_text = word_info.word.strip()
                    if w_text:
                        whisper_words.append({
                            "text": w_text,
                            "start": word_info.start,
                            "end": word_info.end
                        })
        
        # Build script words list and map word indices back to segments
        script_words = []
        seg_word_counts = []
        for seg in segments:
            words = seg["narration"].split()
            script_words.extend(words)
            seg_word_counts.append(len(words))
            
        aligned_words = []
        ns = len(script_words)
        nw = len(whisper_words)
        if ns > 0 and nw > 0:
            w_idx = 0
            for s_idx, s_word in enumerate(script_words):
                best_w_idx = w_idx
                best_score = 0
                for candidate_idx in range(max(0, w_idx - 4), min(nw, w_idx + 15)):
                    w_word = whisper_words[candidate_idx]["text"].strip(".,!?\"'()").upper()
                    s_word_clean = s_word.strip(".,!?\"'()").upper()
                    if w_word == s_word_clean:
                        score = 3
                    elif w_word in s_word_clean or s_word_clean in w_word:
                        score = 2
                    else:
                        score = 0
                    if score > best_score:
                        best_score = score
                        best_w_idx = candidate_idx
                if best_score > 0:
                    w_idx = best_w_idx
                clamped_w_idx = min(max(0, w_idx), nw - 1)
                aligned_words.append({
                    "word": s_word,
                    "start": whisper_words[clamped_w_idx]["start"],
                    "end": whisper_words[clamped_w_idx]["end"]
                })
                w_idx = clamped_w_idx + 1
        
        if len(aligned_words) == len(script_words):
            word_offset = 0
            total_duration = get_wav_duration(combined_path)
            
            # 1. Gather raw word starts/ends for each segment
            seg_bounds = []
            for i, seg in enumerate(segments):
                num_words = seg_word_counts[i]
                seg_words = aligned_words[word_offset : word_offset + num_words]
                word_offset += num_words
                
                if seg_words:
                    seg_bounds.append((seg_words[0]["start"], seg_words[-1]["end"]))
                else:
                    # fallback if segment is empty
                    seg_bounds.append((total_duration, total_duration))
                    
            # 2. Calculate continuous slice boundaries (midpoints during silences)
            slice_starts = []
            slice_ends = []
            
            for i in range(len(segments)):
                if i == 0:
                    start_time = 0.0
                else:
                    # Midpoint between previous segment's end and this segment's start
                    # Prevents cutting off trailing reverb/breath and preserves natural gaps
                    start_time = (seg_bounds[i-1][1] + seg_bounds[i][0]) / 2.0
                    
                if i == len(segments) - 1:
                    end_time = total_duration
                else:
                    end_time = (seg_bounds[i][1] + seg_bounds[i+1][0]) / 2.0
                    
                slice_starts.append(start_time)
                slice_ends.append(end_time)
            
            # 3. Perform slicing
            for i, seg in enumerate(segments):
                start_time = slice_starts[i]
                end_time = slice_ends[i]
                    
                out_path = f"output/tts_segment_{seg['id']}.wav"
                print(f"[TTS] Slicing Segment {seg['id']}: {start_time:.3f}s -> {end_time:.3f}s")
                cmd = [
                    "ffmpeg", "-y", "-ss", f"{start_time:.3f}", "-to", f"{end_time:.3f}",
                    "-i", combined_path, out_path
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    except Exception as e:
        print(f"[TTS] Word alignment split failed: {e}. Falling back to proportional split.")
        
    # Proportional split fallback
    total_duration = get_wav_duration(combined_path)
    weights = [len(seg["narration"]) for seg in segments]
    total_weight = sum(weights)
    
    current_time = 0.0
    for i, seg in enumerate(segments):
        duration = total_duration * (weights[i] / total_weight)
        end_time = current_time + duration
        if i == len(segments) - 1:
            end_time = total_duration
            
        out_path = f"output/tts_segment_{seg['id']}.wav"
        print(f"[TTS] Proportional slicing Segment {seg['id']}: {current_time:.3f}s -> {end_time:.3f}s")
        cmd = [
            "ffmpeg", "-y", "-ss", f"{current_time:.3f}", "-to", f"{end_time:.3f}",
            "-i", combined_path, out_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        current_time = end_time

def generate_audio(script: dict) -> list[str]:
    """
    Generates TTS for all segments using a SINGLE voice for the whole video.
    To ensure perfect voice tone consistency and prevent shifting depth/pitch,
    we generate the entire voiceover script as a SINGLE combined audio file, 
    then split it back into segment files using word-level alignment (Whisper).
    """
    gemini_client = GeminiClient()
    os.makedirs("output", exist_ok=True)

    gemini_voice = pick_voice(GEMINI_VOICES, "gemini")
    ko_voice     = pick_voice(KOKORO_VOICES, "kokoro")

    segments = script["segments"]
    combined_raw_path = "output/tts_combined_raw.wav"
    
    # Clean up any previously generated segment files to prevent stale state
    for seg in segments:
        p = f"output/tts_segment_{seg['id']}.wav"
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    # We join segments with a period and newline for natural pauses between sentences
    full_text = "\n\n".join(seg["narration"] for seg in segments)

    # ── Pass 1: Try Gemini combined ──────────────────────────────────────────
    print(f"[TTS] Using Gemini voice '{gemini_voice}' for this video.")
    gemini_failed = False

    try:
        vocal_tone = script.get("vocal_tone")
        voiceover_plan = script.get("voiceover_plan")
        
        audio_bytes, mime_type = gemini_client.generate_tts(
            full_text,
            voice=gemini_voice,
            vocal_tone=vocal_tone,
            voiceover_plan=voiceover_plan
        )
        
        if audio_bytes.startswith(b"RIFF") or "wav" in mime_type.lower():
            with open(combined_raw_path, "wb") as wf:
                wf.write(audio_bytes)
        else:
            with wave.open(combined_raw_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(audio_bytes)
        print(f"[TTS] Gemini combined generated successfully.")
    except Exception as e:
        print(f"[TTS] Gemini combined failed: {e}")
        gemini_failed = True

    if not gemini_failed:
        try:
            split_combined_audio(combined_raw_path, segments)
            return [f"output/tts_segment_{seg['id']}.wav" for seg in segments]
        except Exception as split_err:
            print(f"[TTS] Split combined audio failed: {split_err}")
            gemini_failed = True

    # ── Pass 2: Gemini failed combined. Switched to Kokoro combined. ──────────
    print(f"[TTS] SWITCHING entire video to Kokoro '{ko_voice}'.")
    if os.path.exists(combined_raw_path):
        try:
            os.remove(combined_raw_path)
        except Exception:
            pass

    try:
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline
        pipeline_ko = KPipeline(lang_code="a")
        
        samples = []
        for _, _, audio in pipeline_ko(full_text, voice=ko_voice, speed=1.0):
            samples.append(audio)
        audio_np = np.concatenate(samples)
        audio_i16 = np.clip(audio_np * 32767, -32768, 32767).astype(np.int16)
        
        with wave.open(combined_raw_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(audio_i16.tobytes())
            
        split_combined_audio(combined_raw_path, segments)
        return [f"output/tts_segment_{seg['id']}.wav" for seg in segments]
    except Exception as ko_err:
        raise RuntimeError(f"Kokoro combined generation failed: {ko_err}")

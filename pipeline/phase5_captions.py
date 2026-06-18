import os
import soundfile as sf

def fmt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def generate_captions(audio_files: list[str], script: dict, format_type: str = "short") -> str:
    ass_events = []
    time_offset = 0.0
    
    try:
        from faster_whisper import WhisperModel
        print("Loading faster-whisper 'base' model on CPU...")
        model = WhisperModel("base", device="cpu", compute_type="int8")
        
        for i, (audio_path, seg) in enumerate(zip(audio_files, script["segments"])):
            print(f"Transcribing TTS file: {audio_path}...")
            if not os.path.exists(audio_path):
                raise FileNotFoundError(f"Audio file not found: {audio_path}")
                
            segments_out, info = model.transcribe(audio_path, word_timestamps=True)
            
            for whisper_seg in segments_out:
                if whisper_seg.words:
                    for word_info in whisper_seg.words:
                        word  = word_info.word.strip().upper()
                        if not word:
                            continue
                        start = time_offset + word_info.start
                        end   = time_offset + word_info.end
                        ass_events.append(f"Dialogue: 0,{fmt_time(start)},{fmt_time(end)},Default,,0,0,0,,"
                                           f"{{\\blur2\\1c&H00D4FF&\\fscx120\\fscy120\\t(0,80,\\fscx100\\fscy100)}}{word}")
            
            data, sr = sf.read(audio_path)
            duration = len(data) / sr
            time_offset += duration
            print(f"Segment {seg['id']} duration: {duration:.2f}s, Cumulative offset: {time_offset:.2f}s")
            
    except Exception as exc:
        print(f"Warning: faster-whisper failed ({exc}). Falling back to rule-based word timing...")
        for i, (audio_path, seg) in enumerate(zip(audio_files, script["segments"])):
            if not os.path.exists(audio_path):
                raise FileNotFoundError(f"Audio file not found: {audio_path}")
                
            data, sr = sf.read(audio_path)
            duration = len(data) / sr
            
            words = seg["narration"].split()
            if words:
                word_dur = duration / len(words)
                for w_idx, word in enumerate(words):
                    word_clean = word.strip().upper()
                    if not word_clean:
                        continue
                    w_start = time_offset + w_idx * word_dur
                    w_end = w_start + word_dur
                    ass_events.append(f"Dialogue: 0,{fmt_time(w_start)},{fmt_time(w_end)},Default,,0,0,0,,"
                                       f"{{\\blur2\\1c&H00D4FF&\\fscx120\\fscy120\\t(0,80,\\fscx100\\fscy100)}}{word_clean}")
                    
            time_offset += duration
            print(f"Segment {seg['id']} duration: {duration:.2f}s (rule-timed), Cumulative offset: {time_offset:.2f}s")
        
    # Dynamic ASS subtitle configuration based on format
    if format_type == "short":
        play_res_x = 1080
        play_res_y = 1920
        font_size  = 88      # was 72 — larger for mobile screens
        margin_v   = 420     # position in lower-middle area of short
    else:
        play_res_x = 1920
        play_res_y = 1080
        font_size  = 60      # was 54
        margin_v   = 130

    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Bebas Neue,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,8,2,2,30,30,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    os.makedirs("output", exist_ok=True)
    ass_path = "output/captions.ass"
    with open(ass_path, "w") as f:
        f.write(ass_header)
        f.write("\n".join(ass_events))
        f.write("\n")
        
    print(f"Generated ASS captions saved to {ass_path}")
    return ass_path

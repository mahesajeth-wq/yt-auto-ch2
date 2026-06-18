import os
import wave
import shutil
import subprocess
from pipeline.sfx import create_sfx_track

def get_wav_duration(filepath: str) -> float:
    with wave.open(filepath, 'rb') as f:
        frames = f.getnframes()
        rate = f.getframerate()
        return frames / float(rate)

def assemble_video(broll_files: list[str], tts_files: list[str], captions_ass: str, music_path: str, script: dict, format_type: str) -> str:
    print("Starting video assembly...")
    os.makedirs("output", exist_ok=True)
    
    # Step 1: Normalize all B-roll clips to uniform spec
    print("Step 1: Normalizing B-roll clips...")
    normalized_brolls = []
    durations = []
    
    w, h = (1080, 1920) if format_type == "short" else (1920, 1080)
    
    for i, (broll_path, tts_path) in enumerate(zip(broll_files, tts_files)):
        duration = get_wav_duration(tts_path)
        durations.append(duration)
        norm_path = f"output/broll_{i}_norm.mp4"
        
        print(f"Normalizing segment {i} B-roll to duration {duration:.3f}s...")
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", broll_path, "-t", f"{duration:.3f}",
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,eq=contrast=1.05:saturation=1.1:gamma=0.95,setsar=1",
            "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", norm_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        normalized_brolls.append(norm_path)

    # Step 2: Concatenate B-roll (no audio)
    print("Step 2: Concatenating B-roll clips...")
    concat_list_path = "output/concat_list.txt"
    with open(concat_list_path, "w") as f:
        for norm_path in normalized_brolls:
            abs_path = os.path.abspath(norm_path)
            f.write(f"file '{abs_path}'\n")
            
    assembled_video_path = "output/assembled_video.mp4"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path,
        "-c", "copy", assembled_video_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 3: Concatenate TTS audio segments
    print("Step 3: Concatenating TTS audio segments...")
    audio_list_path = "output/audio_list.txt"
    with open(audio_list_path, "w") as f:
        for tts_path in tts_files:
            abs_path = os.path.abspath(tts_path)
            f.write(f"file '{abs_path}'\n")
            
    tts_combined_path = "output/tts_combined.wav"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", audio_list_path,
        "-c", "copy", tts_combined_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 3b: Create SFX track (whoosh at each clip boundary)
    print("Step 3b: Generating SFX track…")
    total_tts_duration = sum(durations)
    # Clip boundaries are at cumulative TTS durations (skip the first clip — no whoosh at t=0)
    boundary_times = []
    cumulative = 0.0
    for d in durations[:-1]:   # all boundaries except the last (end of video)
        cumulative += d
        boundary_times.append(cumulative)
    sfx_track_path = create_sfx_track(boundary_times, total_tts_duration)

    # Step 4: Add karaoke captions to video
    print("Step 4: Adding captions...")
    assembled_capped_path = "output/assembled_capped.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", assembled_video_path,
        "-vf", f"ass={captions_ass}",
        "-c:v", "libx264", "-preset", "superfast", "-crf", "18", "-pix_fmt", "yuv420p",
        assembled_capped_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 5: Adding premium hook overlays (Shorts only)
    print("Step 5: Adding premium hook overlays...")
    assembled_flashed_path = "output/assembled_flashed.mp4"
    if format_type == "short":
        clean_title = "".join(c for c in script.get("title", "").upper() if c.isalnum() or c.isspace()).strip()
        
        filters = []
        # 1. Pattern interrupt flashes at the start of each segment (0.15s transparent white overlay)
        for t_start in [0.0] + boundary_times:
            filters.append(f"drawbox=y=0:color=white@0.3:t=fill:enable='between(t,{t_start:.3f},{t_start+0.15:.3f})'")
            
        # 2. Big title hook card (first 1.5s) - Yellow font with premium box padding
        filters.append(f"drawtext=text='{clean_title}':fontsize=80:fontcolor=yellow:font='Bebas Neue':"
                       f"x=(w-text_w)/2:y=h*0.22:enable='between(t,0,1.5)':borderw=8:bordercolor=black:"
                       f"box=1:boxcolor=black@0.5:boxborderw=15")
                       
        if len(durations) >= 4:
            seg4_start = sum(durations[:3])
            seg4_end = seg4_start + 0.8
            # 3. Rewatch trigger
            filters.append(
                f"drawtext=text='PAUSE - CATCH THE DETAIL':fontsize=48:fontcolor=yellow:font='Bebas Neue':"
                f"x=(w-text_w)/2:y=h*0.15:enable='between(t,{seg4_start:.3f},{seg4_end:.3f})':"
                f"box=1:boxcolor=black@0.6:boxborderw=10"
            )
            
        cmd = [
            "ffmpeg", "-y", "-i", assembled_capped_path,
            "-vf", ",".join(filters),
            "-c:v", "libx264", "-preset", "superfast", "-crf", "18", "-pix_fmt", "yuv420p",
            assembled_flashed_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        shutil.copy(assembled_capped_path, assembled_flashed_path)

    # Step 6: Final mix: video + TTS + music + SFX
    print("Step 6: Final audio mix with SFX…")
    final_output_path = f"output/final_{format_type}.mp4"

    filter_complex = (
        "[1:a]volume=2.0[tts];"
        "[2:a]volume=0.12,aloop=loop=-1:size=2147483647[music_loop];"
        "[3:a]volume=0.35[sfx];"
        "[tts][music_loop]amix=inputs=2:duration=first:normalize=0[mixed];"
        "[mixed][sfx]amix=inputs=2:duration=first:normalize=0[premix];"
        "[premix]loudnorm=I=-14:TP=-1.5:LRA=11[audio_final]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", assembled_flashed_path,
        "-i", tts_combined_path,
        "-i", music_path,
        "-i", sfx_track_path,           # ← NEW: 4th input
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[audio_final]",
        "-c:v", "copy",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-r", "30", "-movflags", "+faststart",
        final_output_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print(f"Assembly completed. Final video: {final_output_path}")
    return final_output_path

import argparse
import json
import os
import sys
import traceback

from pipeline.config import validate_config
import pipeline.phase1_topics as phase1
import pipeline.phase2_script as phase2
import pipeline.phase3_tts as phase3
import pipeline.phase4_broll as phase4
import pipeline.phase5_captions as phase5
import pipeline.phase6_music as phase6
import pipeline.phase7_assemble as phase7
import pipeline.phase8_thumbnail as phase8

def main():
    parser = argparse.ArgumentParser(description="yt-auto Video Generator")
    parser.add_argument("--format", choices=["short", "long"], required=True, help="Video format to generate")
    parser.add_argument("--resume", action="store_true", help="Resume generation from existing files in output/")
    args = parser.parse_args()
    
    # 0. Validate Config
    try:
        validate_config()
    except ValueError as val_err:
        print(f"Configuration Error: {val_err}")
        sys.exit(1)
        
    # Handle directory clearing if not resuming
    if not args.resume and os.path.exists("output"):
        print("Clearing output/ directory for a fresh run...")
        import shutil
        try:
            shutil.rmtree("output")
        except Exception as e:
            print(f"Warning: Could not clear output directory: {e}")
            
    os.makedirs("output", exist_ok=True)
    
    topic_json_path = "output/topic.json"
    script_json_path = "output/script.json"
    
    try:
        # Load or select topic
        if args.resume and os.path.exists(topic_json_path):
            print("[Phase 1] Resuming: Loading existing topic...")
            with open(topic_json_path, "r") as f:
                topic = json.load(f)
        else:
            print(f"[Phase 1] Selecting trending topic for {args.format}...")
            topic = phase1.select_topic(args.format)
            with open(topic_json_path, "w") as f:
                json.dump(topic, f, indent=2)
        
        # Load or generate script
        if args.resume and os.path.exists(script_json_path):
            print("[Phase 2] Resuming: Loading existing script...")
            with open(script_json_path, "r") as f:
                script = json.load(f)
        else:
            print(f"[Phase 2] Generating script for topic: '{topic['topic']}'...")
            script = phase2.generate_script(topic, args.format)
            with open(script_json_path, "w") as f:
                json.dump(script, f, indent=2)
        print(f"Generated title: '{script['title']}'")
        
        print(f"[Phase 3] Generating TTS audio ({len(script['segments'])} segments)...")
        audio_files = phase3.generate_audio(script)
        
        print("[Phase 4] Fetching B-roll media...")
        from pipeline.phase7_assemble import get_wav_duration
        tts_durations = [get_wav_duration(f) for f in audio_files] if audio_files else []
        used_urls = set()
        broll_files = []
        for i, seg in enumerate(script["segments"]):
            dur = tts_durations[i] if tts_durations else 6.0
            bpath = phase4.fetch_broll(
                seg["broll_query"],
                args.format,
                i,
                duration=dur,
                narration=seg["narration"],
                alt_queries=seg.get("broll_queries"),
                used_urls=used_urls
            )
            broll_files.append(bpath)
            
        print("[Phase 5] Generating captions with word-level timing...")
        # Pass args.format to customize resolution/style
        captions_ass = phase5.generate_captions(audio_files, script, args.format)
        
        print("[Phase 6] Generating background music...")
        # Determine music duration. Shorts = 35s, Long-form = total duration + padding
        if args.format == "short":
            music_duration = 35
        else:
            # For long-form, calculate total audio duration and pad it
            from pipeline.phase7_assemble import get_wav_duration
            total_audio = sum(get_wav_duration(f) for f in audio_files)
            music_duration = int(total_audio) + 15
            
        music_path = phase6.generate_music(topic["topic"], duration_seconds=music_duration)
        
        print("[Phase 7] Assembling final video with FFmpeg...")
        final_video = phase7.assemble_video(broll_files, audio_files, captions_ass, music_path, script, args.format)
        
        # ── Judge AI Quality Review Loop ──────────────────────────────────────
        from pipeline.judge import JudgeClient
        judge = JudgeClient()
        
        review_metadata = {
            "title": script["title"],
            "segments": [
                {
                    "id": seg["id"],
                    "narration": seg["narration"],
                    "broll_query": seg["broll_query"]
                }
                for seg in script["segments"]
            ]
        }
        
        max_attempts = 3
        attempt = 1
        
        while attempt <= max_attempts:
            print(f"\n[Judge AI] Review Attempt {attempt}/{max_attempts} for video: {final_video}...")
            review_result = judge.review_video(final_video, review_metadata)
            
            status = review_result.get("status", "PASSED")
            score = review_result.get("score", 100)
            reason = review_result.get("reason", "")
            failed_segs = review_result.get("failed_segments", [])
            
            print(f"[Judge AI] Score: {score}, Status: {status}")
            print(f"[Judge AI] Reason: {reason}")
            
            if status == "PASSED" or score >= 80 or not failed_segs:
                print("[Judge AI] Video PASSED the quality review.")
                with open("output/judge_report.json", "w") as rf:
                    json.dump(review_result, rf, indent=2)
                break
                
            print(f"[Judge AI] Video REJECTED. Failed segments: {failed_segs}")
            if attempt == max_attempts:
                print("[Judge AI] Reached max review attempts. Proceeding with current version.")
                with open("output/judge_report.json", "w") as rf:
                    json.dump(review_result, rf, indent=2)
                break
                
            print(f"[Judge AI] Re-fetching B-roll for failed segments {failed_segs}...")
            for idx in failed_segs:
                if idx < 0 or idx >= len(script["segments"]):
                    print(f"Warning: Invalid failed segment index: {idx}")
                    continue
                
                seg = script["segments"][idx]
                dur = tts_durations[idx] if tts_durations else 6.0
                
                # Delete existing failed broll to ensure we generate a new one
                old_broll = f"output/broll_{idx}.mp4"
                if os.path.exists(old_broll):
                    try:
                        os.remove(old_broll)
                    except Exception as e:
                        print(f"Warning: Could not remove old B-roll: {e}")
                
                print(f"[Judge AI] Re-fetching Segment {idx} (Query: '{seg['broll_query']}') with used_urls...")
                bpath = phase4.fetch_broll(
                    seg["broll_query"],
                    args.format,
                    idx,
                    duration=dur,
                    narration=seg["narration"],
                    alt_queries=seg.get("broll_queries"),
                    used_urls=used_urls
                )
                broll_files[idx] = bpath
                
            print("[Judge AI] Re-assembling video after updating failed B-roll clips...")
            final_video = phase7.assemble_video(broll_files, audio_files, captions_ass, music_path, script, args.format)
            attempt += 1
        
        print("[Phase 8] Generating thumbnail...")
        thumbnail = phase8.generate_thumbnail(final_video, script["thumbnail_text"])
        
        # Save metadata for publish step
        metadata_path = "output/metadata.json"
        metadata = {
            "title":       script["title"],
            "description": script["description"],
            "tags":        script["tags"],
            "category_id": script.get("category_id", "27"),
            "publish_at":  script.get("publish_at"),
            "format":      args.format,
            "video_path":  final_video,
            "thumbnail":   thumbnail
        }
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
            
        # Cleanup intermediate files in output/ to save space
        print("Cleaning up intermediate files...")
        keep_files = [
            os.path.basename(final_video),
            os.path.basename(thumbnail),
            "metadata.json",
            "topic.json",
            "script.json",
            "judge_report.json"
        ]
        for f in os.listdir("output"):
            if f not in keep_files:
                path = os.path.join("output", f)
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                    elif os.path.isdir(path):
                        import shutil
                        shutil.rmtree(path)
                except Exception as e:
                    print(f"Warning: Could not remove temporary file {f}: {e}")

        print(f"\n✅ Generation complete. Video: {final_video}")
        print("Artifact ready. Trigger the Publish workflow in GitHub mobile app to upload.")
        
    except Exception as err:
        print(f"\n❌ Pipeline failed during execution: {err}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

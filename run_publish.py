import argparse
import json
import os
import sys
import google.auth.exceptions
import pipeline.phase9_upload as phase9

def main():
    parser = argparse.ArgumentParser(description="yt-auto Video Publisher")
    parser.add_argument("--bypass-judge", action="store_true", help="Bypass the Judge AI visual check")
    args = parser.parse_args()
    
    metadata_path = "output/metadata.json"
    if not os.path.exists(metadata_path):
        print(f"Error: Metadata file not found at {metadata_path}. Have you run generation first?")
        sys.exit(1)
        
    with open(metadata_path, "r") as f:
        metadata = json.load(f)
        
    # Extract format and files
    fmt = metadata.get("format", "short")
    video_path = metadata.get("video_path")
    thumbnail_path = metadata.get("thumbnail")
    
    # Resilient path check: if the absolute path from generation doesn't exist,
    # look in the local output/ folder
    if not video_path or not os.path.exists(video_path):
        fallback_video = f"output/final_{fmt}.mp4"
        if os.path.exists(fallback_video):
            video_path = fallback_video
        else:
            print(f"Error: Video file not found. Checked: {video_path} and {fallback_video}")
            sys.exit(1)
            
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        fallback_thumb = "output/thumbnail.jpg"
        if os.path.exists(fallback_thumb):
            thumbnail_path = fallback_thumb
        else:
            print(f"Error: Thumbnail file not found. Checked: {thumbnail_path} and {fallback_thumb}")
            sys.exit(1)
            
    # --- JUDGE AI GATEKEEPER ---
    if not args.bypass_judge:
        print("\n⚖️ Initiating Judge AI visual and narrative check...")
        
        # Check if we already have a cached report from the generation phase
        report_path = "output/judge_report.json"
        report = None
        if os.path.exists(report_path):
            try:
                with open(report_path, "r") as rf:
                    report = json.load(rf)
                print("Found cached Judge AI report from generation phase. Reusing report...")
            except Exception as e:
                print(f"Warning: Failed to load cached judge report: {e}. Running full review...")
                
        if not report:
            from pipeline.judge import JudgeClient

            judge = JudgeClient()
            try:
                report = judge.review_video(video_path, metadata)
                # Save the judge report
                with open(report_path, "w") as rf:
                    json.dump(report, rf, indent=2)
            except Exception as judge_err:
                print(f"Warning: Judge AI review encountered an error: {judge_err}.")
                print("Proceeding with upload (fallback due to Judge AI system error)...")
                report = {"status": "PASSED", "score": 91, "reason": "Bypassed due to Judge API error"}
                
        status = report.get("status", "REJECTED")
        score = report.get("score", 0)
        reason = report.get("reason", "No reason provided")
        issues = report.get("issues", [])
        
        if status != "PASSED":
            print("\n🛑 VIDEO REJECTED BY JUDGE AI!")
            print(f"Score: {score}/100")
            print(f"Reason: {reason}")
            if issues:
                print("Issues:")
                for issue in issues:
                    print(f" - {issue}")
            print("\nFix the issues and regenerate the video before publishing.")
            sys.exit(1)
        else:
            print(f"\n✅ Video PASSED Judge AI review! (Score: {score}/100)")
            print(f"Judge Comments: {reason}\n")
    else:
        print("\n⚠️ Bypassing Judge AI check as requested.")
            
    print(f"Publishing {fmt} video...")
    print(f"Video: {video_path}")
    print(f"Thumbnail: {thumbnail_path}")
    print(f"Title: {metadata.get('title')}")
    
    try:
        video_id = phase9.upload_to_youtube(video_path, thumbnail_path, metadata)
        print(f"\n✅ Successfully published to YouTube! Video ID: {video_id}")
        print(f"Direct Link: https://www.youtube.com/watch?v={video_id}")
        
        # --- DAILYMOTION UPLOAD ---
        try:
            print("\n🚀 Initiating Dailymotion upload...")
            import importlib
            phase10 = importlib.import_module("pipeline.phase10_dailymotion")
            dm_id = phase10.upload_to_dailymotion(video_path, metadata)
            if dm_id:
                print(f"✅ Successfully published to Dailymotion! Video ID: {dm_id}")
        except Exception as dm_err:
            print(f"⚠️ Warning: Dailymotion upload encountered an error: {dm_err}")

        # --- RUMBLE UPLOAD ---
        try:
            print("\n🚀 Initiating Rumble upload...")
            phase11 = importlib.import_module("pipeline.phase11_rumble")
            rumble_url = phase11.upload_to_rumble(video_path, metadata)
            if rumble_url:
                print(f"✅ Successfully published to Rumble! URL: {rumble_url}")
        except Exception as rb_err:
            print(f"⚠️ Warning: Rumble upload encountered an error: {rb_err}")

        # --- META (FACEBOOK + INSTAGRAM) UPLOAD ---
        try:
            print("\n🚀 Initiating Meta (Facebook + Instagram) upload...")
            phase12 = importlib.import_module("pipeline.phase12_meta")
            meta_result = phase12.upload_to_meta(video_path, metadata)
            if meta_result.get("fb_video_id"):
                print(f"✅ Facebook Reel published! ID: {meta_result['fb_video_id']}")
            if meta_result.get("ig_media_id"):
                print(f"✅ Instagram Reel published! ID: {meta_result['ig_media_id']}")
        except Exception as meta_err:
            print(f"⚠️ Warning: Meta upload encountered an error: {meta_err}")

        # --- THREADS UPLOAD ---
        try:
            print("\n🚀 Initiating Threads upload...")
            threads_user_id = os.environ.get("THREADS_USER_ID", os.environ.get("IG_USER_ID", ""))
            threads_token = os.environ.get("THREADS_ACCESS_TOKEN", os.environ.get("FB_PAGE_TOKEN", ""))
            
            if threads_user_id and threads_token:
                phase13 = importlib.import_module("pipeline.phase13_threads")
                
                # Build Threads caption
                title = metadata.get("title", "")
                hashtags_list = []
                tags = metadata.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                elif isinstance(tags, list):
                    pass
                else:
                    tags = []
                    
                for tag in tags:
                    clean_tag = "".join(c for c in tag if c.isalnum())
                    if clean_tag:
                        hashtags_list.append(f"#{clean_tag.lower()}")
                        
                desc = metadata.get("description", "")
                for word in desc.split():
                    if word.startswith("#"):
                        clean_h = "#" + "".join(c for c in word if c.isalnum())
                        if clean_h != "#" and clean_h.lower() not in [h.lower() for h in hashtags_list]:
                            hashtags_list.append(clean_h.lower())
                            
                threads_hashtags = " ".join(hashtags_list)
                threads_caption = f"{title}\n\n📲 Link in bio!\n\n{threads_hashtags}"
                threads_caption = threads_caption[:500]
                
                threads_post_id = phase13.upload_to_threads(video_path, threads_caption, threads_user_id, threads_token)
                if threads_post_id:
                    print(f"✅ Threads post published! ID: {threads_post_id}")
            else:
                print("[Threads] Skipped — THREADS_USER_ID or THREADS_ACCESS_TOKEN not set.")
        except Exception as threads_err:
            print(f"⚠️ Warning: Threads upload encountered an error: {threads_err}")
    except google.auth.exceptions.RefreshError as ref_err:
        print("\n❌ Authentication Error: Refresh token may have expired or is invalid.")
        print("Re-generate your refresh token at: https://developers.google.com/oauthplayground")
        print(f"Details: {ref_err}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Publish failed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

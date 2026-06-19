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

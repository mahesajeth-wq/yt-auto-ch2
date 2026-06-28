"""
Phase 13 — Meta Threads Publisher

Uploads the generated video as a Threads post.
Uses the Meta Threads Graph API.
"""
import os
import time
import requests
from pipeline.phase12_meta import _get_public_video_url

THREADS_API = "https://graph.threads.net/v1.0"

def upload_to_threads(video_path: str, caption: str, user_id: str, access_token: str) -> str | None:
    """Upload a video to Threads as a post.

    Returns the post ID on success, None on failure.
    """
    # 1. Get a public URL for the video (Threads API requires a publicly reachable URL)
    video_url = _get_public_video_url(video_path)
    if not video_url:
        print("[Threads] Could not obtain public video URL. Skipping Threads upload.")
        return None

    # 2. Create Threads media container
    print("[Threads] Creating Threads media container...")
    container_url = f"{THREADS_API}/{user_id}/threads"
    container_resp = requests.post(
        container_url,
        data={
            "media_type": "VIDEO",
            "video_url": video_url,
            "text": caption[:500],  # Threads text limit is 500 characters
            "access_token": access_token,
        },
        timeout=60,
    )
    if container_resp.status_code != 200:
        print(f"[Threads] Container creation failed: {container_resp.status_code} {container_resp.text[:300]}")
        return None

    container_id = container_resp.json().get("id")
    if not container_id:
        print(f"[Threads] No container ID returned: {container_resp.json()}")
        return None
    print(f"[Threads] Container created: {container_id}. Waiting for processing...")

    # 3. Poll status until FINISHED (Threads processing takes some time)
    max_wait = 600
    poll_interval = 15
    waited = 0
    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval

        status_resp = requests.get(
            f"{THREADS_API}/{container_id}",
            params={"fields": "status,error_message", "access_token": access_token},
            timeout=30,
        )
        if status_resp.status_code != 200:
            print(f"[Threads] Status poll error: {status_resp.status_code}")
            continue

        status_data = status_resp.json()
        status = status_data.get("status", "")
        print(f"[Threads] Container status: {status} ({waited}s elapsed)")

        if status == "FINISHED":
            break
        elif status == "ERROR":
            err = status_data.get("error_message", "Unknown error")
            print(f"[Threads] ❌ Container processing failed: {err}")
            return None
    else:
        print(f"[Threads] ❌ Timed out waiting for container to process ({max_wait}s)")
        return None

    # 4. Publish the container
    print("[Threads] Publishing Threads post...")
    publish_url = f"{THREADS_API}/{user_id}/threads_publish"
    publish_resp = requests.post(
        publish_url,
        data={"creation_id": container_id, "access_token": access_token},
        timeout=60,
    )
    if publish_resp.status_code != 200:
        print(f"[Threads] Publish failed: {publish_resp.status_code} {publish_resp.text[:300]}")
        return None

    post_id = publish_resp.json().get("id")
    print(f"[Threads] ✅ Threads post published! post_id={post_id}")
    return post_id

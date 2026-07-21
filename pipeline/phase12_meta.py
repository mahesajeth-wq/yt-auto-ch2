"""
Phase 12 — Meta (Facebook Page Reel + Instagram Reel) Publisher

Uploads the generated video as a Reel to:
  1. Facebook Page  (via /{page-id}/video_reels resumable upload)
  2. Instagram       (via /{ig-user-id}/media container model)

Environment variables required:
  FB_PAGE_ID, FB_PAGE_TOKEN, IG_USER_ID
"""
import os
import time
import json
import requests

GRAPH_API = "https://graph.facebook.com/v22.0"
GRAPH_VIDEO_API = "https://graph-video.facebook.com/v22.0"


# ── Facebook Page Reel Upload ────────────────────────────────────────────────

def _get_page_access_token(page_id: str, token: str) -> str:
    """Fetch specific Page Access Token using user token if required."""
    try:
        r = requests.get(
            f"{GRAPH_API}/{page_id}",
            params={"fields": "access_token", "access_token": token},
            timeout=15,
        )
        if r.status_code == 200:
            p_token = r.json().get("access_token")
            if p_token:
                print(f"[Meta/FB] Successfully retrieved Page Access Token for Page {page_id}")
                return p_token
    except Exception as e:
        print(f"[Meta/FB] Note: Page access token query returned: {e}")
    return token


def _fb_upload_reel(video_path: str, description: str, page_id: str, page_token: str, title: str = "") -> str | None:
    """Upload a video as a Reel to a Facebook Page using resumable upload.

    Returns the video_id on success, None on failure.
    """
    file_size = os.path.getsize(video_path)
    page_token = _get_page_access_token(page_id, page_token)

    # Step 1: Initialize upload session
    print("[Meta/FB] Starting Reel upload session...")
    init_resp = requests.post(
        f"{GRAPH_API}/{page_id}/video_reels",
        data={"upload_phase": "start", "access_token": page_token},
        timeout=30,
    )
    if init_resp.status_code != 200:
        print(f"[Meta/FB] Init failed: {init_resp.status_code} {init_resp.text[:300]}")
        return None

    init_data = init_resp.json()
    video_id = init_data.get("video_id")
    if not video_id:
        print(f"[Meta/FB] No video_id in init response: {init_data}")
        return None
    print(f"[Meta/FB] Upload session created. video_id={video_id}")

    # Step 2: Upload binary data via rupload
    print(f"[Meta/FB] Uploading {file_size} bytes to rupload.facebook.com...")
    with open(video_path, "rb") as f:
        upload_resp = requests.post(
            f"https://rupload.facebook.com/video-upload/v22.0/{video_id}",
            headers={
                "Authorization": f"OAuth {page_token}",
                "offset": "0",
                "file_size": str(file_size),
                "Content-Type": "application/octet-stream",
            },
            data=f,
            timeout=600,
        )
    if upload_resp.status_code != 200:
        print(f"[Meta/FB] Upload failed: {upload_resp.status_code} {upload_resp.text[:300]}")
        return None
    print("[Meta/FB] Binary upload complete.")

    # Step 3: Publish (finish phase)
    print("[Meta/FB] Publishing Reel...")
    payload = {
        "upload_phase": "finish",
        "video_id": video_id,
        "description": description[:2000],
        "video_state": "PUBLISHED",
        "access_token": page_token,
    }
    if title:
        payload["title"] = title[:100]

    finish_resp = requests.post(
        f"{GRAPH_API}/{page_id}/video_reels",
        data=payload,
        timeout=30,
    )
    if finish_resp.status_code != 200:
        print(f"[Meta/FB] Finish failed: {finish_resp.status_code} {finish_resp.text[:300]}")
        # Even if finish fails, the video may still process. Return the video_id.
        return video_id

    print(f"[Meta/FB] ✅ Reel published! video_id={video_id}")
    return video_id


# ── Instagram Reel Upload ────────────────────────────────────────────────────

def _get_public_video_url(video_path: str) -> str | None:
    """Upload video to get a temporary public URL for IG API.
    
    Tries uguu.se first, then falls back to tmpfiles.org and file.io.
    """
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    
    # ── Method 1: uguu.se ─────────────────────────────────────────────────────
    print(f"[Meta/IG] Uploading {file_size_mb:.1f}MB to uguu.se for public URL...")
    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://uguu.se/upload",
                files={"files[]": (os.path.basename(video_path), f, "video/mp4")},
                timeout=600,
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("files"):
                direct_url = data["files"][0].get("url")
                if direct_url:
                    print(f"[Meta/IG] uguu.se URL: {direct_url}")
                    return direct_url
        print(f"[Meta/IG] uguu.se failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Meta/IG] uguu.se error: {e}")
        
    # ── Method 2: tmpfiles.org (Fallback) ─────────────────────────────────────
    print(f"[Meta/IG] Falling back: Uploading {file_size_mb:.1f}MB to tmpfiles.org...")
    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": (os.path.basename(video_path), f, "video/mp4")},
                timeout=600,
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                viewer_url = data.get("data", {}).get("url")
                if viewer_url and "tmpfiles.org/" in viewer_url:
                    # Convert to direct download link: https://tmpfiles.org/dl/...
                    direct_url = viewer_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
                    print(f"[Meta/IG] tmpfiles.org URL: {direct_url}")
                    return direct_url
        print(f"[Meta/IG] tmpfiles.org failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Meta/IG] tmpfiles.org error: {e}")

    # ── Method 2: file.io (Fallback) ──────────────────────────────────────────
    print(f"[Meta/IG] Falling back: Uploading to file.io...")
    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://file.io",
                files={"file": (os.path.basename(video_path), f, "video/mp4")},
                data={"expires": "1d", "autoDelete": "true"},
                timeout=600,
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                url = data.get("link")
                print(f"[Meta/IG] file.io URL: {url}")
                return url
        print(f"[Meta/IG] file.io failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Meta/IG] file.io error: {e}")
        
    return None


def _ig_upload_reel(video_path: str, caption: str, ig_user_id: str, access_token: str) -> str | None:
    """Upload a video as an Instagram Reel using the Content Publishing API.

    Returns the IG media ID on success, None on failure.
    """
    # Get a public URL for the video
    video_url = _get_public_video_url(video_path)
    if not video_url:
        print("[Meta/IG] Could not obtain public video URL. Skipping IG upload.")
        return None

    # Step 1: Create media container
    print("[Meta/IG] Creating Reel container...")
    container_resp = requests.post(
        f"{GRAPH_API}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],
            "share_to_feed": "true",
            "access_token": access_token,
        },
        timeout=60,
    )
    if container_resp.status_code != 200:
        print(f"[Meta/IG] Container creation failed: {container_resp.status_code} {container_resp.text[:300]}")
        return None

    container_id = container_resp.json().get("id")
    if not container_id:
        print(f"[Meta/IG] No container ID returned: {container_resp.json()}")
        return None
    print(f"[Meta/IG] Container created: {container_id}. Waiting for processing...")

    # Step 2: Poll until FINISHED (max 10 minutes)
    max_wait = 600
    poll_interval = 15
    waited = 0
    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval

        status_resp = requests.get(
            f"{GRAPH_API}/{container_id}",
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=30,
        )
        if status_resp.status_code != 200:
            print(f"[Meta/IG] Status poll error: {status_resp.status_code}")
            continue

        status_data = status_resp.json()
        status_code = status_data.get("status_code", "")
        print(f"[Meta/IG] Container status: {status_code} ({waited}s elapsed)")

        if status_code == "FINISHED":
            break
        elif status_code == "ERROR":
            err = status_data.get("status", "Unknown error")
            print(f"[Meta/IG] ❌ Container processing failed: {err}")
            return None
    else:
        print(f"[Meta/IG] ❌ Timed out waiting for container to process ({max_wait}s)")
        return None

    # Step 3: Publish
    print("[Meta/IG] Publishing Reel...")
    publish_resp = requests.post(
        f"{GRAPH_API}/{ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=60,
    )
    if publish_resp.status_code != 200:
        print(f"[Meta/IG] Publish failed: {publish_resp.status_code} {publish_resp.text[:300]}")
        return None

    media_id = publish_resp.json().get("id")
    print(f"[Meta/IG] ✅ Reel published! media_id={media_id}")
    return media_id


def _extract_hashtags(metadata: dict) -> str:
    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    elif not isinstance(tags, list):
        tags = []
    
    hashtags_list = []
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
                
    return " ".join(hashtags_list)


# ── Public Interface ─────────────────────────────────────────────────────────

def upload_to_meta(video_path: str, metadata: dict) -> dict:
    """Upload video to Facebook Page + Instagram as Reels.

    Returns dict with 'fb_video_id' and 'ig_media_id' (either may be None).
    """
    fb_page_id = os.environ.get("FB_PAGE_ID", "")
    fb_page_token = os.environ.get("FB_PAGE_TOKEN", "")
    ig_user_id = os.environ.get("IG_USER_ID", "")

    title = metadata.get("title", "")
    hashtags = _extract_hashtags(metadata)

    # Snappy social-optimized captions with space dot dividers to hide hashtags overlay
    fb_caption = f"{title}\n\n📲 Link in bio to learn more!\n\n.\n.\n.\n{hashtags}"
    ig_caption = f"{title}\n\n📲 Link in bio!\n\n.\n.\n.\n{hashtags}"

    result = {"fb_video_id": None, "ig_media_id": None}

    # Facebook Page Reel
    if fb_page_id and fb_page_token:
        print("\n📘 Uploading to Facebook Page as Reel...")
        try:
            result["fb_video_id"] = _fb_upload_reel(video_path, fb_caption, fb_page_id, fb_page_token, title=title)
        except Exception as e:
            print(f"[Meta/FB] Error: {e}")
    else:
        print("[Meta/FB] Skipped — FB_PAGE_ID or FB_PAGE_TOKEN not set.")

    # Instagram Reel (uses the page token as access token for the IG API)
    if ig_user_id and fb_page_token:
        print("\n📸 Uploading to Instagram as Reel...")
        try:
            result["ig_media_id"] = _ig_upload_reel(video_path, ig_caption, ig_user_id, fb_page_token)
        except Exception as e:
            print(f"[Meta/IG] Error: {e}")
    else:
        print("[Meta/IG] Skipped — IG_USER_ID or FB_PAGE_TOKEN not set.")

    return result

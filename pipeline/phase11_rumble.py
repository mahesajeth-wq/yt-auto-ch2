"""
phase11_rumble.py — Rumble session-based video upload.

Auth flow (from HAR):
  1. POST user.get_salts  → salt, salt2, uid
  2. md5(md5(pw)+salt) = h1; md5(h1+salt2) = h2; pw_field = h1,h2,uid
  3. POST user.2fa.first_step  → session cookies
  4. GET  user.has_unread_notifications → upload endpoint
  5. POST <endpoint>/upload.php?api=1.3  → temp filename
  6. POST <endpoint>/upload.php?form=1&api=1.3  → publish
"""

import hashlib
import json
import os
import re
import time

import requests


# ── Env vars ────────────────────────────────────────────────────────────────
RUMBLE_EMAIL = os.environ.get("RUMBLE_EMAIL", "").strip()
RUMBLE_PASSWORD = os.environ.get("RUMBLE_PASSWORD", "").strip()
RUMBLE_CHANNEL_ID = os.environ.get("RUMBLE_CHANNEL_ID", "").strip()  # optional numeric

BASE = "https://rumble.com"
HEADERS_AUTH = {
    "Origin": "https://auth.rumble.com",
    "Referer": "https://auth.rumble.com/",
}
HEADERS_MAIN = {
    "Origin": BASE,
    "Referer": f"{BASE}/",
}


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _hash_stretch(password: str, salt: str, iterations: int = 128) -> str:
    h = _md5(salt + password)
    for _ in range(iterations):
        h = _md5(h + password)
    return h


def _login(session: requests.Session) -> dict:
    """Authenticate and return user info dict with upload endpoint."""

    # Step 1 — get salts
    print("[Rumble] Getting auth salts...")
    r = session.post(
        f"{BASE}/service.php?name=user.get_salts",
        data={"username": RUMBLE_EMAIL},
        headers=HEADERS_AUTH,
    )
    r.raise_for_status()
    salts_data = r.json()
    salts_list = salts_data.get("data", {}).get("salts", [])
    if len(salts_list) < 3:
        raise ValueError(f"[Rumble] Failed to get valid salts: {salts_data}")
    salt, uid, salt2 = salts_list[0], salts_list[1], salts_list[2]
    print(f"[Rumble] Got salts for uid={uid}")

    # Step 2 — hash password
    h_stretch = _hash_stretch(RUMBLE_PASSWORD, salt, 128)
    hash1 = _md5(h_stretch + uid)
    hash2 = _hash_stretch(RUMBLE_PASSWORD, salt2, 128)
    pw_field = f"{hash1},{hash2},{uid}"

    # Step 3 — login
    print("[Rumble] Logging in...")
    r = session.post(
        f"{BASE}/service.php?name=user.2fa.first_step&response_type=session",
        data={
            "legacy_password": "1",
            "login": RUMBLE_EMAIL,
            "password": pw_field,
            "redirect_uri": f"{BASE}/",
        },
        headers=HEADERS_AUTH,
    )
    r.raise_for_status()
    print("[Rumble] Login successful — session cookies acquired")

    # Step 4 — discover upload endpoint
    print("[Rumble] Discovering upload endpoint...")
    r = session.get(
        f"{BASE}/service.php",
        params={
            "api": "7",
            "name": "user.has_unread_notifications",
            "included_js_libs": "main,web_services",
        },
        headers=HEADERS_MAIN,
    )
    r.raise_for_status()
    user_data = r.json()

    user_info = user_data.get("user", {})
    if not user_info.get("logged_in"):
        raise RuntimeError("[Rumble] Session not logged in after auth")

    upload_endpoint = user_info.get("upload", {}).get("endpoint", "")
    if not upload_endpoint:
        raise RuntimeError("[Rumble] No upload endpoint in session response")

    print(f"[Rumble] Upload endpoint: {upload_endpoint}")
    return {"endpoint": upload_endpoint, "user": user_info}


def _get_media_channel_id(session: requests.Session) -> str:
    """Parse mediaChannelId from upload.php HTML form."""
    r = session.get(f"{BASE}/upload.php", headers=HEADERS_MAIN)
    r.raise_for_status()

    # Look for mediaChannelId in form or JS
    match = re.search(r'name=["\']mediaChannelId["\'].*?value=["\'](\d+)["\']', r.text)
    if match:
        return match.group(1)

    # Fallback: search for channelId select options
    match = re.search(r'mediaChannelId["\s:]+(\d+)', r.text)
    if match:
        return match.group(1)

    return ""


def _upload_file(session: requests.Session, endpoint: str, video_path: str) -> str:
    """Upload video binary, return temp filename."""
    file_size = os.path.getsize(video_path)
    file_name = os.path.basename(video_path)
    print(f"[Rumble] Uploading {file_name} ({file_size / 1_048_576:.1f} MB)...")

    time_start = int(time.time() * 1000)

    with open(video_path, "rb") as f:
        r = session.post(
            f"{endpoint}/upload.php?api=1.3",
            files={"Filedata": (file_name, f, "video/mp4")},
            headers=HEADERS_MAIN,
        )
    r.raise_for_status()

    time_end = int(time.time() * 1000)
    temp_filename = r.text.strip()
    elapsed_ms = max(time_end - time_start, 1)
    speed = int(file_size / elapsed_ms * 1000)

    print(f"[Rumble] Upload complete → {temp_filename} ({elapsed_ms}ms, {speed} B/s)")
    return temp_filename


def _get_thumbnail_index(session: requests.Session, endpoint: str, temp_filename: str) -> str:
    """Get auto-generated thumbnails, return first available index."""
    try:
        r = session.get(
            f"{endpoint}/upload.php",
            params={"thumbnails": temp_filename, "api": "1.3"},
            headers=HEADERS_MAIN,
        )
        r.raise_for_status()
        thumbs = r.json()
        if thumbs:
            # Return the first key (frame index)
            return str(next(iter(thumbs)))
    except Exception as e:
        print(f"[Rumble] Warning: Could not get thumbnails: {e}")

    return "0"


def _publish(
    session: requests.Session,
    endpoint: str,
    temp_filename: str,
    metadata: dict,
    media_channel_id: str,
    thumb_index: str,
) -> dict:
    """Submit metadata form to publish the video."""

    title = metadata.get("title", "Educational Video")[:100].replace("<", "").replace(">", "")
    description = metadata.get("description", "")[:5000].replace("<", "").replace(">", "")
    tags_list = metadata.get("tags", [])
    if isinstance(tags_list, list):
        tags_str = ",".join(str(t).replace("<", "").replace(">", "") for t in tags_list)[:500]
    else:
        tags_str = str(tags_list)[:500]

    file_size = 0
    try:
        video_path = metadata.get("video_path", "")
        if video_path and os.path.exists(video_path):
            file_size = os.path.getsize(video_path)
    except Exception:
        pass

    now_ms = int(time.time() * 1000)
    file_meta = json.dumps({
        "name": f"{temp_filename}",
        "modified": now_ms - 60000,
        "size": file_size,
        "type": "video/mp4",
        "time_start": now_ms - 5000,
        "speed": max(file_size // 5, 1),
        "num_chunks": 1,
        "time_end": now_ms,
    })

    channel_id = RUMBLE_CHANNEL_ID if RUMBLE_CHANNEL_ID else "undefined"

    form_data = {
        "title": title,
        "description": description,
        "video[]": temp_filename,
        "featured": "6",          # Standard / Rumble Only — keep rights
        "rights": "1",
        "terms": "1",
        "facebookUpload": "",
        "vimeoUpload": "",
        "infoWho": "",
        "infoWhen": "",
        "infoWhere": "",
        "infoExtUser": "",
        "tags": tags_str,
        "related_video_url": "",
        "channelId": channel_id,
        "siteChannelId": "1",
        "mediaChannelId": media_channel_id or "1",
        "isGamblingRelated": "false",
        "set_default_channel_id": "0",
        "sendPush": "1",
        "setFeaturedForUser": "1",
        "setFeaturedForChannel": "0",
        "visibility": "public",
        "availability": "free",
        "thumb": thumb_index,
        "file_meta": file_meta,
    }

    print(f"[Rumble] Publishing: '{title}'...")
    r = session.post(
        f"{endpoint}/upload.php?form=1&api=1.3",
        data=form_data,
        headers={
            **HEADERS_MAIN,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "text/html, */*; q=0.01",
        },
    )
    r.raise_for_status()

    # Parse response — HTML wrapping JS: uf.response.setSuccess({...}, N)
    match = re.search(r"uf\.response\.setSuccess\((\{.*?\}),\s*\d+\)", r.text, re.DOTALL)
    if match:
        result = json.loads(match.group(1))
        return result

    # Fallback — check for error
    if "setError" in r.text:
        raise RuntimeError(f"[Rumble] Publish error. Response: {r.text[:500]}")

    # If we can't parse but got 200, log and return partial
    print(f"[Rumble] Warning: Could not parse success response. Raw: {r.text[:300]}")
    return {"url": "", "fid": ""}


def upload_to_rumble(video_path: str, metadata: dict) -> str:
    """
    Full Rumble upload pipeline.
    Returns the published video URL or empty string on skip/error.
    """
    if not RUMBLE_EMAIL or not RUMBLE_PASSWORD:
        print("[Rumble] Warning: Missing RUMBLE_EMAIL/RUMBLE_PASSWORD. Skipping upload.")
        return ""

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        ),
    })

    # 1. Login + discover endpoint
    info = _login(session)
    endpoint = info["endpoint"]

    # 2. Get mediaChannelId from upload page
    media_channel_id = _get_media_channel_id(session)
    if media_channel_id:
        print(f"[Rumble] Media channel ID: {media_channel_id}")
    else:
        print("[Rumble] Warning: Could not parse mediaChannelId, using default")

    # 3. Upload video binary
    temp_filename = _upload_file(session, endpoint, video_path)

    # 4. Get thumbnail index from auto-generated thumbnails
    thumb_index = _get_thumbnail_index(session, endpoint, temp_filename)

    # 5. Publish with metadata
    result = _publish(session, endpoint, temp_filename, metadata, media_channel_id, thumb_index)

    video_url = result.get("url", "")
    fid = result.get("fid", "")

    if video_url:
        print(f"[Rumble] ✅ Published! URL: {video_url}")
        print(f"[Rumble] Video FID: {fid}")
    else:
        print(f"[Rumble] Published (no URL in response). FID: {fid}")

    return video_url or str(fid)

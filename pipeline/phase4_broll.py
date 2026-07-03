import os
import random
import requests
import urllib.parse
import subprocess
import time
from pipeline.config import PEXELS_API_KEY, PIXABAY_API_KEY, COVERR_API_KEY, NASA_API_KEY, KLIPY_API_KEY, NASA_BROLL_ENABLED



def _nasa_params(query: str, media_type: str, page_size: int) -> dict:
    return {"q": query, "media_type": media_type, "page_size": page_size}


def _walk_urls(obj) -> list[str]:
    urls: list[str] = []
    if isinstance(obj, dict):
        for value in obj.values():
            urls.extend(_walk_urls(value))
    elif isinstance(obj, list):
        for value in obj:
            urls.extend(_walk_urls(value))
    elif isinstance(obj, str) and obj.startswith("http"):
        urls.append(obj)
    return urls


def _pick_klipy_urls(item: dict) -> tuple[str | None, str | None]:
    urls = _walk_urls(item)
    video_url = None
    thumb_url = None
    for ext in (".mp4", ".webm", ".gif"):
        video_url = next((u for u in urls if ext in u.lower()), None)
        if video_url:
            break
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        thumb_url = next((u for u in urls if ext in u.lower()), None)
        if thumb_url:
            break
    if not thumb_url:
        thumb_url = video_url
    return video_url, thumb_url


def _klipy_candidates(query: str, n: int = 4) -> list[dict]:
    if not KLIPY_API_KEY:
        return []
    try:
        r = requests.get(
            f"https://api.klipy.com/api/v1/{KLIPY_API_KEY}/gifs/search",
            params={"q": query, "per_page": max(8, n), "rating": "pg-13", "locale": "en_US"},
            headers={"User-Agent": "yt-auto/1.0"},
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("data") or data.get("results") or data.get("gifs") or []
        if isinstance(items, dict):
            items = list(items.values())
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                continue
            video_url, thumb_url = _pick_klipy_urls(item)
            if video_url and thumb_url:
                candidates.append({
                    "video_url": video_url,
                    "thumb_url": thumb_url,
                    "source": "Klipy"
                })
            if len(candidates) >= n:
                break
        return candidates
    except Exception as e:
        print(f"[B-roll] Klipy search failed for '{query}': {e}")
        return []


def _klipy_video(query: str) -> str | None:
    candidates = _klipy_candidates(query, n=1)
    return candidates[0]["video_url"] if candidates else None


# ── Source 1: Pexels Candidates ──────────────────────────────────────────────

def _pexels_candidates(query: str, orientation: str, n: int = 8) -> list[dict]:
    if not PEXELS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": n, "orientation": orientation},
            timeout=30,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        candidates = []
        for video in videos:
            image_url = video.get("image")
            video_files = [f for f in video.get("video_files", []) if f.get("quality") in ("hd", "sd")]
            if image_url and video_files:
                video_files.sort(key=lambda f: f.get("width", 0), reverse=True)
                candidates.append({
                    "video_url": video_files[0]["link"],
                    "thumb_url": image_url,
                    "source": "Pexels"
                })
        return candidates
    except Exception as e:
        print(f"[B-roll] Pexels search failed for '{query}': {e}")
        return []


# ── Source 2: Pixabay ────────────────────────────────────────────────────────

def _pixabay_video(query: str) -> str | None:
    if not PIXABAY_API_KEY:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_API_KEY, "q": query, "per_page": 3},
            timeout=30,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            return None
        videos_data = hits[0].get("videos", {})
        for size in ["large", "medium", "small", "tiny"]:
            url = videos_data.get(size, {}).get("url")
            if url:
                return url
        return None
    except Exception as e:
        print(f"[B-roll] Pixabay failed for '{query}': {e}")
        return None


# ── Source 3: Coverr (cinematic, high quality) ───────────────────────────────

def _coverr_video(query: str) -> str | None:
    if not COVERR_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.coverr.co/videos",
            params={"keywords": query, "api_key": COVERR_API_KEY, "page": 1, "size": 5, "urls": "true"},
            timeout=30,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            return None
        item = random.choice(hits[:3])
        urls = item.get("urls", {})
        if not urls:
            return None
        video_url = urls.get("mp4_download") or urls.get("mp4")
        if isinstance(video_url, dict):
            video_url = video_url.get("hd") or video_url.get("sd")
        return video_url
    except Exception as e:
        print(f"[B-roll] Coverr failed for '{query}': {e}")
        return None


def _coverr_candidates(query: str, orientation: str, n: int = 5) -> list[dict]:
    if not COVERR_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.coverr.co/videos",
            params={"keywords": query, "api_key": COVERR_API_KEY, "page": 1, "size": n * 3, "urls": "true"},
            timeout=30,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        candidates = []
        for item in hits:
            thumb = item.get("thumbnail")
            urls = item.get("urls", {})
            if urls:
                video_url = urls.get("mp4_download") or urls.get("mp4")
                if isinstance(video_url, dict):
                    video_url = video_url.get("hd") or video_url.get("sd")
                if thumb and video_url:
                    is_vertical = item.get("is_vertical", False)
                    candidates.append({
                        "video_url": video_url,
                        "thumb_url": thumb,
                        "is_vertical": is_vertical,
                        "source": "Coverr"
                    })
        # Sort candidates to prefer the requested orientation
        if orientation == "portrait":
            candidates.sort(key=lambda x: x["is_vertical"], reverse=True)
        else:
            candidates.sort(key=lambda x: x["is_vertical"], reverse=False)
        return candidates[:n]
    except Exception as e:
        print(f"[B-roll] Coverr candidates search failed for '{query}': {e}")
        return []


def _pixabay_candidates(query: str, n: int = 3) -> list[dict]:
    if not PIXABAY_API_KEY:
        return []
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_API_KEY, "q": query, "per_page": max(3, n)},
            timeout=30,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        candidates = []
        for item in hits:
            picture_id = item.get("picture_id")
            thumb = None
            if picture_id:
                thumb = f"https://i.vimeocdn.com/video/{picture_id}_640x360.jpg"
            
            videos_data = item.get("videos", {})
            video_url = None
            for size in ["large", "medium", "small", "tiny"]:
                url = videos_data.get(size, {}).get("url")
                if url:
                    video_url = url
                    break
            if thumb and video_url:
                candidates.append({
                    "video_url": video_url,
                    "thumb_url": thumb,
                    "source": "Pixabay"
                })
        return candidates
    except Exception as e:
        print(f"[B-roll] Pixabay candidates failed for '{query}': {e}")
        return []


def _nasa_video_candidate(query: str) -> dict | None:
    try:
        r = requests.get(
            "https://images-api.nasa.gov/search",
            params=_nasa_params(query, "video", 3),
            headers={"User-Agent": "yt-auto/1.0"},
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("collection", {}).get("items", [])
        if not items:
            return None
        
        for item in items[:2]:
            nasa_id = item.get("data", [{}])[0].get("nasa_id")
            links = item.get("links", [])
            thumb_url = None
            for link in links:
                if link.get("rel") == "preview" or link.get("render") == "image":
                    thumb_url = link.get("href")
                    break
            if not nasa_id or not thumb_url:
                continue
                
            r_asset = requests.get(
                f"https://images-api.nasa.gov/asset/{urllib.parse.quote(nasa_id)}",
                headers={"User-Agent": "yt-auto/1.0"},
                timeout=15,
            )
            r_asset.raise_for_status()
            items_asset = r_asset.json().get("collection", {}).get("items", [])
            video_url = None
            for a in items_asset:
                href = a.get("href", "")
                if href.endswith("~medium.mp4") or href.endswith("~mobile.mp4"):
                    video_url = href
                    break
            if not video_url:
                for a in items_asset:
                    href = a.get("href", "")
                    if href.endswith(".mp4"):
                        video_url = href
                        break
            if video_url:
                return {
                    "video_url": video_url,
                    "thumb_url": thumb_url,
                    "source": "NASA"
                }
        return None
    except Exception as e:
        print(f"[B-roll] NASA candidate search failed for '{query}': {e}")
        return None


def _wikimedia_video_candidate(query: str) -> dict | None:
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srnamespace": "6",  # File namespace
                "srsearch": f"{query} filetype:video",
                "format": "json",
                "srlimit": "3",
            },
            headers={"User-Agent": "yt-auto/1.0"},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return None
  
        for res in results[:2]:
            title = res["title"]
            r_info = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "titles": title,
                    "prop": "imageinfo",
                    "iiprop": "url|thumb",
                    "iiurlwidth": "640",
                    "format": "json",
                },
                headers={"User-Agent": "yt-auto/1.0"},
                timeout=15,
            )
            r_info.raise_for_status()
            pages = r_info.json().get("query", {}).get("pages", {})
            for page_id, page_data in pages.items():
                imageinfo = page_data.get("imageinfo", [])
                if imageinfo:
                    video_url = imageinfo[0].get("url")
                    thumb_url = imageinfo[0].get("thumburl")
                    if video_url and thumb_url:
                        return {
                            "video_url": video_url,
                            "thumb_url": thumb_url,
                            "source": "Wikimedia"
                        }
        return None
    except Exception as e:
        print(f"[B-roll] Wikimedia video candidate failed for '{query}': {e}")
        return None





# ── Source 4: NASA Image & Video Library (no key — public domain) ─────────────

def _nasa_image(query: str) -> str | None:
    """Fetches a real NASA image for science/space topics. Completely free, no key."""
    try:
        r = requests.get(
            "https://images-api.nasa.gov/search",
            params={
                **_nasa_params(query, "image", 5),
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("collection", {}).get("items", [])
        if not items:
            return None
        item = random.choice(items[:3])
        links = item.get("links", [])
        for link in links:
            href = link.get("href", "")
            if href and href.startswith("http"):
                return href
        return None
    except Exception as e:
        print(f"[B-roll] NASA failed for '{query}': {e}")
        return None


# ── Source 5: Wikipedia article thumbnail ────────────────────────────────────

def _wikipedia_image(query: str) -> str | None:
    """
    Fetches the Wikipedia article image for the query topic.
    No API key required. Perfect for named people and well-known concepts.
    """
    try:
        title = urllib.parse.quote(query.replace(" ", "_"))
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        # Prefer full-size original, fall back to thumbnail
        img = data.get("originalimage", {}).get("source") \
           or data.get("thumbnail", {}).get("source")
        return img
    except Exception as e:
        print(f"[B-roll] Wikipedia failed for '{query}': {e}")
        return None


def _wikimedia_video(query: str) -> str | None:
    """Search Wikimedia Commons for CC-licensed educational videos and fetch actual URL. No API key needed."""
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srnamespace": "6",  # File namespace
                "srsearch": f"{query} filetype:video",
                "format": "json",
                "srlimit": "5",
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return None

        # Pick the top result and use Wikipedia API to get the correct URL
        title = results[0]["title"]
        r_info = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=15,
        )
        r_info.raise_for_status()
        pages = r_info.json().get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            imageinfo = page_data.get("imageinfo", [])
            if imageinfo:
                return imageinfo[0].get("url")
        return None
    except Exception as e:
        print(f"[B-roll] Wikimedia Commons failed for '{query}': {e}")
        return None


def _dvids_candidates(query: str, n: int = 3) -> list[dict]:
    try:
        r = requests.get(
            "https://www.dvidshub.net/api/search",
            params={"query": query, "type": "video",
                    "rows": n * 2, "output": "json"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        r.raise_for_status()
        out = []
        for item in r.json().get("results", []):
            v = item.get("download_url") or item.get("file_url")
            t = item.get("thumbnail_url") or item.get("image_url")
            if v and t:
                out.append({"video_url": v, "thumb_url": t, "source": "DVIDS"})
        return out[:n]
    except Exception as e:
        print(f"[B-roll] DVIDS failed: {e}")
        return []

def _dvids_video(query: str) -> str | None:
    candidates = _dvids_candidates(query, n=1)
    return candidates[0]["video_url"] if candidates else None

def _openverse_image(query: str) -> str | None:
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "license": "cc0,by",
                    "page_size": 5, "format": "json"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        return random.choice(results[:3]).get("url")
    except Exception as e:
        print(f"[B-roll] Openverse failed: {e}")
        return None


def _nasa_video(query: str) -> str | None:
    """Fetches a real NASA video for science/space topics. Completely free, no key."""
    try:
        r = requests.get(
            "https://images-api.nasa.gov/search",
            params={
                **_nasa_params(query, "video", 5),
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("collection", {}).get("items", [])
        if not items:
            return None

        # Pick one from top 3
        item = random.choice(items[:3])
        nasa_id = item.get("data", [{}])[0].get("nasa_id")
        if not nasa_id:
            return None

        r_asset = requests.get(
            f"https://images-api.nasa.gov/asset/{urllib.parse.quote(nasa_id)}",
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=15,
        )
        r_asset.raise_for_status()
        items_asset = r_asset.json().get("collection", {}).get("items", [])
        for a in items_asset:
            href = a.get("href", "")
            if href.endswith("~medium.mp4") or href.endswith("~mobile.mp4"):
                return href
        for a in items_asset:
            href = a.get("href", "")
            if href.endswith(".mp4"):
                return href
        return None
    except Exception as e:
        print(f"[B-roll] NASA video failed for '{query}': {e}")
        return None


def _archive_video(query: str) -> str | None:
    """Search Internet Archive for public domain movies. No API key needed."""
    try:
        r = requests.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f"collection:prelinger AND title:({query})",
                "fl[]": "identifier",
                "rows": "5",
                "output": "json",
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
        if docs:
            identifier = docs[0]["identifier"]
            r_files = requests.get(
                f"https://archive.org/metadata/{urllib.parse.quote(identifier)}",
                headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
                timeout=15,
            )
            r_files.raise_for_status()
            files = r_files.json().get("files", [])
            for f in files:
                name = f.get("name", "")
                if name.endswith(".mp4") and int(f.get("size", 0)) > 10_000:
                    return f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
    except Exception as e:
        print(f"[B-roll] Prelinger filter search failed for '{query}': {e}")

    try:
        r = requests.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f"title:({query}) AND mediatype:(movies)",
                "fl[]": "identifier",
                "rows": "5",
                "output": "json",
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
        if not docs:
            return None

        identifier = docs[0]["identifier"]
        r_files = requests.get(
            f"https://archive.org/metadata/{urllib.parse.quote(identifier)}",
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=15,
        )
        r_files.raise_for_status()
        files = r_files.json().get("files", [])
        for f in files:
            name = f.get("name", "")
            if name.endswith(".mp4") and int(f.get("size", 0)) > 10_000:
                return f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
        return None
    except Exception as e:
        print(f"[B-roll] Internet Archive failed for '{query}': {e}")
        return None


def _download_video_robust(url: str, out_path: str, segment_index: int) -> bool:
    try:
        r = requests.get(url, stream=True, timeout=90, headers={"User-Agent": "yt-auto/1.0"})
        r.raise_for_status()

        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        is_webm = path.endswith(".webm") or path.endswith(".ogv")
        is_gif = path.endswith(".gif")

        temp_ext = ".webm" if is_webm else ".gif" if is_gif else ".mp4"
        temp_file = f"output/temp_dl_{segment_index}{temp_ext}"
        with open(temp_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        if os.path.exists(temp_file) and os.path.getsize(temp_file) > 10_000:
            if is_webm or is_gif:
                print(f"[B-roll] Converting {temp_ext} from {url} to mp4...")
                cmd = [
                    "ffmpeg", "-y", "-i", temp_file,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-an", out_path
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                return res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 10_000
            else:
                if os.path.exists(out_path):
                    os.remove(out_path)
                os.rename(temp_file, out_path)
                return True
        return False
    except Exception as e:
        print(f"[B-roll] Robust download failed for {url}: {e}")
        return False


# ── Ken Burns zoom — applied to ALL image-to-video conversions ───────────────

def _image_to_ken_burns_video(img_path: str, out_path: str, w: int, h: int, duration: float = 6.0):
    """
    Converts a static image to a video with a slow cinematic zoom (Ken Burns effect).
    Uses FFmpeg zoompan filter — zero dependencies, no quality loss.
    Randomly picks zoom direction for variety across segments.
    """
    fps    = 30
    frames = int(duration * fps)  # zoompan needs total frame count, not seconds

    # Three zoom styles — randomly chosen per segment for variety
    styles = [
        # Slow zoom into center
        f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}",
        # Slow zoom starting top-left
        f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':d={frames}:x=0:y=0:s={w}x{h}:fps={fps}",
        # Slow zoom, panning slightly right
        f"scale=8000:-1,zoompan=z='min(zoom+0.001,1.3)':d={frames}:x='iw-iw/zoom':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}",
    ]
    vf = random.choice(styles)

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", img_path,
        "-vf", f"{vf},setsar=1",
        "-t", str(duration), "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-an", out_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── Fallback: Pollinations.ai (AI-generated, multiple models) ────────────────

def _pollinations_image(query: str, w: int, h: int, img_path: str) -> bool:
    """Returns True if image was downloaded successfully."""
    encoded = urllib.parse.quote(query)
    for model in ["flux", "flux-realism", "turbo"]:
        try:
            seed = random.randint(1, 100000)
            url = (
                f"https://image.pollinations.ai/prompt/{encoded}"
                f"?width={w}&height={h}&model={model}&nologo=true&seed={seed}"
            )
            r = requests.get(url, timeout=90)
            if r.status_code == 200 and len(r.content) > 5000:
                with open(img_path, "wb") as f:
                    f.write(r.content)
                return True
        except Exception as e:
            print(f"[B-roll] Pollinations {model} failed: {e}")
    return False


# ── Last resort: PIL gradient placeholder ────────────────────────────────────

def _pil_placeholder(query: str, w: int, h: int, img_path: str):
    """Better-looking placeholder: dark gradient with large centered text."""
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    # Dark gradient background (top dark blue → bottom near-black)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        ratio = y / h
        arr[y, :, 0] = int(10 + ratio * 5)   # R
        arr[y, :, 1] = int(10 + ratio * 20)   # G
        arr[y, :, 2] = int(40 + ratio * 20)   # B

    img  = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)

    # Draw centered query text, large and readable
    words  = query.upper().split()
    lines  = []
    line   = ""
    for word in words:
        test = (line + " " + word).strip()
        if len(test) > 18:
            lines.append(line.strip())
            line = word
        else:
            line = test
    if line:
        lines.append(line.strip())

    font_size = max(60, min(100, w // (max(len(l) for l in lines) + 1) if lines else 80))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    total_text_h = len(lines) * (font_size + 10)
    y_start      = (h - total_text_h) // 2

    for i, line_text in enumerate(lines):
        bbox = draw.textbbox((0, 0), line_text, font=font)
        tw   = bbox[2] - bbox[0]
        x    = (w - tw) // 2
        y    = y_start + i * (font_size + 10)
        # Shadow
        draw.text((x + 3, y + 3), line_text, font=font, fill=(0, 0, 0))
        # Main text
        draw.text((x, y), line_text, font=font, fill=(255, 255, 255))

    img.save(img_path, "JPEG", quality=90)


def _make_clean_fallback(query: str) -> str:
    stop_words = {
        "failure", "failed", "failed", "breaking", "broken", "broke",
        "damaged", "damage", "collapsed", "collapse", "slipping", "slipped",
        "slip", "during", "mechanism", "problems", "problem", "defect",
        "defective", "faulty", "error", "issue", "issues", "accident",
        "disaster", "ruined", "destroy", "destroyed", "destroying",
        "a", "an", "the", "in", "on", "at", "to", "for", "with", "by", "of"
    }
    words = query.lower().split()
    filtered = [w for w in words if w not in stop_words]
    if filtered:
        return " ".join(filtered)
    return query


def _get_video_duration(filepath: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        filepath
    ]
    try:
        import subprocess
        return float(subprocess.check_output(cmd).decode().strip())
    except Exception:
        return 0.0

# ── Master fetch function ────────────────────────────────────────────────────

def fetch_broll(query: str, format_type: str, segment_index: int, duration: float = 6.0, narration: str = "", alt_queries: list[str] | None = None, used_urls: set[str] | None = None) -> str:
    """
    Unified B-roll candidate ranking across multiple platforms (Coverr, Pexels, Pixabay, NASA, Wikimedia)
    using Gemini Vision matching and URL de-duplication.
    """
    orientation = "portrait" if format_type == "short" else "landscape"
    out_path    = f"output/broll_{segment_index}.mp4"
    img_path    = f"output/broll_{segment_index}.jpg"
    w, h        = (1080, 1920) if format_type == "short" else (1920, 1080)
    budget_default = "180" if format_type == "short" else "240"
    budget_seconds = int(os.environ.get("BROLL_SEGMENT_BUDGET_SECONDS", budget_default))
    deadline = time.monotonic() + budget_seconds

    def budget_exceeded() -> bool:
        if time.monotonic() <= deadline:
            return False
        print(f"[B-roll] Segment {segment_index}: time budget exceeded ({budget_seconds}s). Using fast fallback.")
        return True

    os.makedirs("output", exist_ok=True)

    # Return cached clip if already valid
    if os.path.exists(out_path) and os.path.getsize(out_path) > 10_000:
        print(f"[B-roll] Segment {segment_index}: using cached clip.")
        return out_path

    # Build fallback queries
    queries_to_try = [query]
    if alt_queries:
        queries_to_try.extend([q for q in alt_queries if q != query])
    
    clean_fallback = _make_clean_fallback(query)
    if clean_fallback not in queries_to_try:
        queries_to_try.append(clean_fallback)
        
    clean_words = clean_fallback.split()
    if len(clean_words) > 2:
        general_fallback = " ".join(clean_words[:2])
        if general_fallback not in queries_to_try:
            queries_to_try.append(general_fallback)

    # Gather candidate video metadata from ALL platforms
    candidates = []
    
    # 1. Fetch NASA video candidate if science/space query
    is_science = any(k in query.lower() for k in ["space", "nasa", "star", "planet", "galaxy", "orbit", "telescope", "asteroid", "science", "physics", "chemical", "atom", "molecule", "earth", "moon", "sun", "nebula", "black hole"])
    if is_science and NASA_BROLL_ENABLED:
        for q in queries_to_try:
            if budget_exceeded():
                break
            print(f"[B-roll] Segment {segment_index}: checking NASA video for '{q}'…")
            nasa_cand = _nasa_video_candidate(q)
            if nasa_cand:
                candidates.append(nasa_cand)
                if len(candidates) >= 2:
                    break

    # 2. Fetch Wikimedia video candidate
    for q in queries_to_try:
        if budget_exceeded():
            break
        print(f"[B-roll] Segment {segment_index}: checking Wikimedia video for '{q}'…")
        wiki_cand = _wikimedia_video_candidate(q)
        if wiki_cand:
            candidates.append(wiki_cand)
            if len(candidates) >= 2:
                break

    # 2.5 Fetch DVIDS video candidates (up to 2)
    for q in queries_to_try:
        if budget_exceeded():
            break
        print(f"[B-roll] Segment {segment_index}: checking DVIDS video for '{q}'…")
        d_cands = _dvids_candidates(q, n=2)
        if d_cands:
            candidates.extend(d_cands)
            if len(candidates) >= 3:
                break

    # 3. Fetch Coverr candidates (up to 2)
    if COVERR_API_KEY:
        for q in queries_to_try:
            if budget_exceeded():
                break
            c_cands = _coverr_candidates(q, orientation, n=2)
            if c_cands:
                candidates.extend(c_cands)
                if len(candidates) >= 4:
                    break

    # 4. Fetch Klipy GIF/meme candidates (converted to MP4 if selected)
    if KLIPY_API_KEY:
        for q in queries_to_try:
            if budget_exceeded():
                break
            k_cands = _klipy_candidates(q, n=2)
            if k_cands:
                candidates.extend(k_cands)
                if len(candidates) >= 4:
                    break

    # 5. Fetch Pexels candidates (up to 2)
    if PEXELS_API_KEY:
        for q in queries_to_try:
            if budget_exceeded():
                break
            p_cands = _pexels_candidates(q, orientation, n=2)
            if p_cands:
                candidates.extend(p_cands)
                if len(candidates) >= 4:
                    break

    # 6. Fetch Pixabay candidates (up to 2)
    if PIXABAY_API_KEY:
        for q in queries_to_try:
            if budget_exceeded():
                break
            px_cands = _pixabay_candidates(q, n=2)
            if px_cands:
                candidates.extend(px_cands)
                if len(candidates) >= 4:
                    break

    # Apply de-duplication: filter out candidates that have already been used
    if used_urls:
        original_count = len(candidates)
        candidates = [c for c in candidates if c["video_url"] not in used_urls]
        if len(candidates) < original_count:
            print(f"[B-roll] De-duplicated candidates: filtered out {original_count - len(candidates)} already used clips.")

    # Run Gemini Vision matching on candidates
    if candidates:
        print(f"[B-roll] Segment {segment_index}: Ranking {len(candidates)} candidates from: {', '.join(set(c.get('source', 'Unknown') for c in candidates))}…")
        thumbs = []
        valid_candidates = []
        for idx, cand in enumerate(candidates):
            if budget_exceeded():
                break
            try:
                r_thumb = requests.get(cand["thumb_url"], timeout=15)
                r_thumb.raise_for_status()
                from PIL import Image
                import io
                Image.open(io.BytesIO(r_thumb.content)).verify()
                
                thumbs.append(r_thumb.content)
                valid_candidates.append(cand)
            except Exception as e:
                print(f"[B-roll] Failed/invalid thumbnail {idx} from {cand.get('source', 'Unknown')}: {e}")

        if valid_candidates:
            print(f"[B-roll] Segment {segment_index}: Ranking {len(valid_candidates)} candidates from: {', '.join(set(c.get('source', 'Unknown') for c in valid_candidates))}…")
            from pipeline.vision_match import vision_rank_broll
            best_idx, match_found = vision_rank_broll(thumbs, narration, query)

            if match_found and best_idx is not None and best_idx < len(valid_candidates):
                chosen = valid_candidates[best_idx]
                print(f"[B-roll] Winner chosen! Source: {chosen.get('source', 'Unknown')} (Index: {best_idx}). Downloading video…")
                if _download_video_robust(chosen["video_url"], out_path, segment_index):
                    if used_urls is not None:
                        used_urls.add(chosen["video_url"])
                    return out_path
            else:
                print(f"[B-roll] None of the {len(valid_candidates)} candidates passed strict Vision Match.")
        else:
            print(f"[B-roll] No candidates with valid thumbnails for Segment {segment_index}.")

    # ── Fallback 1: Single Frame fallback search on other videos waterfall ─────────────────
    print(f"[B-roll] Segment {segment_index}: falling back to parallel waterfall search...")
    
    # We prioritize archive databases (NASA, DVIDS, Wikimedia, Archive) at the top of the waterfall,
    # and search with main, clean fallback, and general fallback queries.
    other_videos = []
    if NASA_BROLL_ENABLED:
        other_videos.extend([
            ("NASA video (main)", lambda: _nasa_video(query)),
            ("NASA video (fallback)", lambda: _nasa_video(clean_fallback)),
            ("NASA video (general)", lambda: _nasa_video(general_fallback)),
        ])
    other_videos.extend([
        ("DVIDS video (main)", lambda: _dvids_video(query)),
        ("DVIDS video (fallback)", lambda: _dvids_video(clean_fallback)),
        ("DVIDS video (general)", lambda: _dvids_video(general_fallback)),
        ("Wikimedia video (main)", lambda: _wikimedia_video(query)),
        ("Wikimedia video (fallback)", lambda: _wikimedia_video(clean_fallback)),
        ("Wikimedia video (general)", lambda: _wikimedia_video(general_fallback)),
        ("Archive video (main)", lambda: _archive_video(query)),
        ("Archive video (fallback)", lambda: _archive_video(clean_fallback)),
        ("Archive video (general)", lambda: _archive_video(general_fallback)),
    ])
    
    # Stock sites are fallbacks at the bottom of the waterfall list
    other_videos.extend([
        ("Pixabay (main)", lambda: _pixabay_video(query)),
        ("Pixabay (fallback)", lambda: _pixabay_video(clean_fallback)),
        ("Pixabay (general)", lambda: _pixabay_video(general_fallback)),
        ("Coverr (main)", lambda: _coverr_video(query)),
        ("Coverr (fallback)", lambda: _coverr_video(clean_fallback)),
        ("Coverr (general)", lambda: _coverr_video(general_fallback)),
        ("Klipy GIF (main)", lambda: _klipy_video(query)),
        ("Klipy GIF (fallback)", lambda: _klipy_video(clean_fallback)),
        ("Klipy GIF (general)", lambda: _klipy_video(general_fallback)),
    ])

    # Gather candidate URLs to download in parallel (up to 5)
    candidates_to_download = []
    seen_urls = set()
    for label, fetch_url_fn in other_videos:
        if budget_exceeded():
            break
        try:
            video_url = fetch_url_fn()
            if video_url and video_url not in seen_urls:
                if used_urls and video_url in used_urls:
                    continue
                seen_urls.add(video_url)
                candidates_to_download.append({
                    "label": label,
                    "video_url": video_url
                })
                if len(candidates_to_download) >= 5:
                    break
        except Exception as e:
            print(f"[B-roll] Failed to fetch URL for {label}: {e}")

    # Helper function for parallel downloads and frame extraction
    def download_and_extract_frame(cand, idx):
        lbl = cand["label"]
        vurl = cand["video_url"]
        temp_v = f"output/temp_video_{segment_index}_{idx}.mp4"
        temp_f = f"output/temp_frame_{segment_index}_{idx}.jpg"
        
        for p in [temp_v, temp_f]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        
        print(f"[B-roll] Downloading video from {lbl} in parallel...")
        if _download_video_robust(vurl, temp_v, f"{segment_index}_{idx}"):
            v_dur = _get_video_duration(temp_v)
            seek = 0.0
            if v_dur > 20.0:
                seek = min(10.0, v_dur * 0.15)
            elif v_dur > 10.0:
                seek = 2.0
            elif v_dur > 4.0:
                seek = 1.0
                
            cmd = [
                "ffmpeg", "-y", "-ss", f"{seek:.3f}", "-i", temp_v,
                "-vf", "thumbnail=n=30", "-frames:v", "1", temp_f
            ]
            import subprocess
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0 and os.path.exists(temp_f):
                with open(temp_f, "rb") as fh:
                    f_data = fh.read()
                return {
                    "label": lbl,
                    "video_url": vurl,
                    "temp_v": temp_v,
                    "temp_f": temp_f,
                    "frame_data": f_data
                }
        
        # Cleanup on failure
        for p in [temp_v, temp_f]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        return None

    # Download candidates in parallel threads
    import concurrent.futures
    downloaded_results = []
    if candidates_to_download:
        max_workers = min(len(candidates_to_download), 5)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(download_and_extract_frame, cand, i)
                for i, cand in enumerate(candidates_to_download)
            ]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    res = fut.result()
                    if res:
                        downloaded_results.append(res)
                except Exception as e:
                    print(f"[B-roll] Thread download failed: {e}")

    # Rank downloaded candidates using Gemini Vision Match in one batch
    from pipeline.vision_match import vision_rank_broll
    if downloaded_results:
        print(f"[B-roll] Segment {segment_index}: Ranking {len(downloaded_results)} downloaded candidates in batch...")
        thumbs = [r["frame_data"] for r in downloaded_results]
        best_idx, match_found = vision_rank_broll(thumbs, narration, query)
        
        if match_found and best_idx is not None and 0 <= best_idx < len(downloaded_results):
            winner = downloaded_results[best_idx]
            print(f"[B-roll] Parallel winner chosen! Source: {winner['label']} (Index: {best_idx})")
            
            # Keep the winner video
            if os.path.exists(out_path):
                os.remove(out_path)
            import shutil
            shutil.move(winner["temp_v"], out_path)
            
            if used_urls is not None:
                used_urls.add(winner["video_url"])
                
            # Clean up all files
            for r in downloaded_results:
                for p in [r["temp_v"], r["temp_f"]]:
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
            return out_path
        else:
            print(f"[B-roll] None of the {len(downloaded_results)} parallel candidates matched.")
            
        # Clean up on no match
        for r in downloaded_results:
            for p in [r["temp_v"], r["temp_f"]]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    # ── Fallback 2: image sources (all converted with Ken Burns) ─────────────────────
    print(f"[B-roll] Segment {segment_index}: trying image sources…")

    img_sources = []
    if NASA_BROLL_ENABLED:
        img_sources.extend([
            (_nasa_image, query),
            (_nasa_image, clean_fallback),
        ])
    img_sources.extend([
        (_openverse_image, query),
        (_openverse_image, clean_fallback),
        (_wikipedia_image, query),
        (_wikipedia_image, clean_fallback)
    ])

    img_url = None
    for img_fn, q in img_sources:
        candidate_img = img_fn(q)
        if candidate_img and (used_urls is None or candidate_img not in used_urls):
            img_url = candidate_img
            if used_urls is not None:
                used_urls.add(img_url)
            break

    if img_url:
        try:
            r = requests.get(img_url, timeout=30, headers={"User-Agent": "yt-auto/1.0"})
            r.raise_for_status()
            with open(img_path, "wb") as f:
                f.write(r.content)
            print(f"[B-roll] Segment {segment_index}: image downloaded. Applying Ken Burns…")
            _image_to_ken_burns_video(img_path, out_path, w, h, duration)
            return out_path
        except Exception as e:
            print(f"[B-roll] Image source failed: {e}. Trying Pollinations…")

    # ── Fallback 3: Pollinations AI image ─────────────────────────────────────────────────
    if _pollinations_image(query, w, h, img_path):
        print(f"[B-roll] Segment {segment_index}: Pollinations OK. Applying Ken Burns…")
        _image_to_ken_burns_video(img_path, out_path, w, h, duration)
        return out_path

    # ── Fallback 4: PIL gradient placeholder ──────────────────────────────────────────────
    print(f"[B-roll] Segment {segment_index}: all sources failed. Using gradient placeholder.")
    _pil_placeholder(query, w, h, img_path)
    _image_to_ken_burns_video(img_path, out_path, w, h, duration)
    return out_path

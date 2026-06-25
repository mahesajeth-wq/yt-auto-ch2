import os
import random
import requests
import urllib.parse
import subprocess
import time
from pipeline.config import PEXELS_API_KEY, PIXABAY_API_KEY, COVERR_API_KEY, NASA_API_KEY, KLIPY_API_KEY


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
    words         = query.split()
    fallback_query = " ".join(words[:2]) if len(words) > 2 else query
    queries_to_try = [query]
    if alt_queries:
        queries_to_try.extend([q for q in alt_queries if q != query])
    queries_to_try.append(fallback_query)

    # Gather candidate video metadata from ALL platforms
    candidates = []
    
    # 1. Fetch NASA video candidate if science/space query
    is_science = any(k in query.lower() for k in ["space", "nasa", "star", "planet", "galaxy", "orbit", "telescope", "asteroid", "science", "physics", "chemical", "atom", "molecule", "earth", "moon", "sun", "nebula", "black hole"])
    if is_science:
        for q in queries_to_try[:2]:
            if budget_exceeded():
                break
            print(f"[B-roll] Segment {segment_index}: checking NASA video for '{q}'…")
            nasa_cand = _nasa_video_candidate(q)
            if nasa_cand:
                candidates.append(nasa_cand)
                break

    # 2. Fetch Wikimedia video candidate
    for q in queries_to_try[:2]:
        if budget_exceeded():
            break
        print(f"[B-roll] Segment {segment_index}: checking Wikimedia video for '{q}'…")
        wiki_cand = _wikimedia_video_candidate(q)
        if wiki_cand:
            candidates.append(wiki_cand)
            break

    # 3. Fetch Coverr candidates (up to 2)
    if COVERR_API_KEY:
        for q in queries_to_try[:2]:
            if budget_exceeded():
                break
            c_cands = _coverr_candidates(q, orientation, n=2)
            if c_cands:
                candidates.extend(c_cands)
                break

    # 4. Fetch Klipy GIF/meme candidates (converted to MP4 if selected)
    if KLIPY_API_KEY:
        for q in queries_to_try[:2]:
            if budget_exceeded():
                break
            k_cands = _klipy_candidates(q, n=2)
            if k_cands:
                candidates.extend(k_cands)
                break

    # 5. Fetch Pexels candidates (up to 2)
    if PEXELS_API_KEY:
        for q in queries_to_try[:2]:
            if budget_exceeded():
                break
            p_cands = _pexels_candidates(q, orientation, n=2)
            if p_cands:
                candidates.extend(p_cands)
                break

    # 6. Fetch Pixabay candidates (up to 2)
    if PIXABAY_API_KEY:
        for q in queries_to_try[:2]:
            if budget_exceeded():
                break
            px_cands = _pixabay_candidates(q, n=2)
            if px_cands:
                candidates.extend(px_cands)
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
    print(f"[B-roll] Segment {segment_index}: falling back to single-frame waterfall search...")
    other_videos = [
        ("Pixabay (main)", lambda: _pixabay_video(query)),
        ("Pixabay (fallback)", lambda: _pixabay_video(fallback_query)),
        ("Coverr (main)", lambda: _coverr_video(query)),
        ("Coverr (fallback)", lambda: _coverr_video(fallback_query)),
        ("Klipy GIF (main)", lambda: _klipy_video(query)),
        ("Klipy GIF (fallback)", lambda: _klipy_video(fallback_query)),
        ("NASA video (main)", lambda: _nasa_video(query)),
        ("NASA video (fallback)", lambda: _nasa_video(fallback_query)),
        ("Wikimedia video (main)", lambda: _wikimedia_video(query)),
        ("Wikimedia video (fallback)", lambda: _wikimedia_video(fallback_query)),
        ("Archive video (main)", lambda: _archive_video(query)),
        ("Archive video (fallback)", lambda: _archive_video(fallback_query)),
    ]

    from pipeline.vision_match import vision_rank_broll

    for label, fetch_url_fn in other_videos:
        if budget_exceeded():
            break
        video_url = fetch_url_fn()
        if video_url:
            # Check de-duplication
            if used_urls and video_url in used_urls:
                continue
            print(f"[B-roll] Downloading video from {label}…")
            if _download_video_robust(video_url, out_path, segment_index):
                temp_frame_path = f"output/temp_frame_{segment_index}.jpg"
                if os.path.exists(temp_frame_path):
                    os.remove(temp_frame_path)
                
                cmd = [
                    "ffmpeg", "-y", "-i", out_path,
                    "-vf", "thumbnail=n=30", "-frames:v", "1", temp_frame_path
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                if os.path.exists(temp_frame_path):
                    with open(temp_frame_path, "rb") as tf:
                        frame_data = tf.read()
                    os.remove(temp_frame_path)
                    
                    _, match_found = vision_rank_broll([frame_data], narration, query)
                    if match_found:
                        print(f"[B-roll] {label} video accepted by Vision Match.")
                        if used_urls is not None:
                            used_urls.add(video_url)
                        return out_path
                    else:
                        print(f"[B-roll] {label} video rejected by Vision Match. Continuing waterfall…")
                        os.remove(out_path)
                else:
                    print(f"[B-roll] Warning: Frame extraction failed for {label}. Accepting by default.")
                    if used_urls is not None:
                        used_urls.add(video_url)
                    return out_path

    # ── Fallback 2: image sources (all converted with Ken Burns) ─────────────────────
    print(f"[B-roll] Segment {segment_index}: trying image sources…")

    img_url = None
    for img_fn, q in [
        (_nasa_image, query),
        (_nasa_image, fallback_query),
        (_wikipedia_image, query),
        (_wikipedia_image, fallback_query)
    ]:
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

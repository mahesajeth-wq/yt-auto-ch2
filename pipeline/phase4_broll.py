import os
import random
import requests
import urllib.parse
import subprocess
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pipeline.config import PEXELS_API_KEY, PIXABAY_API_KEY, COVERR_API_KEY, NASA_API_KEY, KLIPY_API_KEY, NASA_BROLL_ENABLED, GEMINI_API_BASE, GEMINI_FLASH



def _nasa_params(query: str, media_type: str, page_size: int) -> dict:
    return {
        "q": query,
        "media_type": media_type,
        "page_size": page_size,
        "keywords": query,
        "year_start": "2010"
    }


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
            params={
                "query": query,
                "per_page": min(80, max(n * 3, 15)),
                "orientation": orientation,
                "size": "medium",
            },
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
            params={
                "key": PIXABAY_API_KEY,
                "q": query,
                "per_page": min(50, max(3 * 3, 10)),
                "order": "popular",
                "safesearch": "true",
                "min_width": 1920
            },
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
            params={
                "key": PIXABAY_API_KEY,
                "q": query,
                "per_page": min(50, max(n * 3, 10)),
                "order": "popular",
                "safesearch": "true",
                "min_width": 1920
            },
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
            params=_nasa_params(query, "video", 20),
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
                "srsearch": f"{query} filetype:video OR filetype:webm OR filetype:ogv",
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
                "srsearch": f"{query} filetype:video OR filetype:webm OR filetype:ogv",
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
            params={"query": query, "type": "video", "rows": n * 3, "output": "json"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; yt-auto/1.0)"},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        out = []
        for item in results:
            v = item.get("download_url") or item.get("file_url")
            t = item.get("thumbnail_url") or item.get("image_url")
            title = item.get("title", "")
            id_val = item.get("id")
            if v and t:
                out.append({
                    "video_url": v,
                    "thumb_url": t,
                    "source": "DVIDS",
                    "title": title,
                    "id": id_val,
                    "width": 1920
                })
        return out[:n]
    except Exception as e:
        print(f"[B-roll] DVIDS search failed for '{query}': {e}")
        return []

def _dvids_video(query: str) -> str | None:
    candidates = _dvids_candidates(query, n=1)
    return candidates[0]["video_url"] if candidates else None

def _openverse_image(query: str) -> str | None:
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "license": "cc0,by", "page_size": 5, "orientation": "landscape"},
            headers={"User-Agent": "yt-auto/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        chosen = random.choice(results[:3])
        return chosen.get("url")
    except Exception as e:
        print(f"[B-roll] Openverse image search failed for '{query}': {e}")
        return None

def _archive_candidates(query: str, n: int = 3) -> list[dict]:
    import urllib.parse
    headers = {"User-Agent": "yt-auto/1.0 (educational-pipeline)"}
    candidates = []

    try:
        r = requests.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f"collection:prelinger AND ({query})",
                "fl[]": ["identifier", "title", "downloads"],
                "sort[]": "downloads desc",
                "rows": n * 4,
                "output": "json"
            },
            headers=headers,
            timeout=20
        )
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
    except Exception as e:
        print(f"[B-roll] Archive Prelinger search failed for '{query}': {e}")
        docs = []

    if not docs:
        try:
            r = requests.get(
                "https://archive.org/advancedsearch.php",
                params={
                    "q": f"({query}) AND mediatype:movies",
                    "fl[]": ["identifier", "title", "downloads"],
                    "sort[]": "downloads desc",
                    "rows": n * 4,
                    "output": "json"
                },
                headers=headers,
                timeout=20
            )
            r.raise_for_status()
            docs = r.json().get("response", {}).get("docs", [])
        except Exception as e:
            print(f"[B-roll] Archive broader search failed for '{query}': {e}")
            docs = []

    for doc in docs:
        if len(candidates) >= n:
            break
        identifier = doc.get("identifier")
        title = doc.get("title", "")
        if not identifier:
            continue
        try:
            r_files = requests.get(
                f"https://archive.org/metadata/{urllib.parse.quote(identifier)}",
                headers=headers,
                timeout=15
            )
            r_files.raise_for_status()
            files = r_files.json().get("files", [])
            
            video_url = None
            for f in files:
                name = f.get("name", "")
                if (name.endswith(".mp4") or name.endswith(".webm") or name.endswith(".mkv") or name.endswith(".avi")) and int(f.get("size", 0)) > 10_000:
                    video_url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
                    break
            
            if not video_url:
                continue
                
            thumb_url = None
            for f in files:
                name = f.get("name", "")
                if name.endswith("__ia_thumb.jpg") or name.lower().endswith((".jpg", ".png", ".jpeg")):
                    thumb_url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
                    break
            if not thumb_url:
                thumb_url = f"https://archive.org/services/img/{identifier}"
                
            candidates.append({
                "video_url": video_url,
                "thumb_url": thumb_url,
                "source": "Archive",
                "title": title,
                "id": identifier
            })
        except Exception as e:
            print(f"[B-roll] Archive metadata fetch failed for '{identifier}': {e}")
            
    return candidates


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
                if (name.endswith(".mp4") or name.endswith(".webm") or name.endswith(".mkv") or name.endswith(".avi")) and int(f.get("size", 0)) > 10_000:
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
            if (name.endswith(".mp4") or name.endswith(".webm") or name.endswith(".mkv") or name.endswith(".avi")) and int(f.get("size", 0)) > 10_000:
                return f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
        return None
    except Exception as e:
        print(f"[B-roll] Internet Archive failed for '{query}': {e}")
        return None


def _parse_iso_duration(duration_str: str) -> float:
    import re
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0.0
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    return float(hours * 3600 + minutes * 60 + seconds)


def _youtube_candidates(query: str, n: int = 5) -> list[dict]:
    """
    Search YouTube for royalty-free / copyright-free videos matching the query.
    Prioritizes Creative Commons licensed videos.
    Checks titles and descriptions to ensure they are free to use.
    """
    import yt_dlp
    import re
    
    candidates = []
    try:
        print(f"[B-roll] Searching YouTube with yt-dlp matching: '{query}'...")
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'force_generic_extractor': False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # We search for "query royalty free" or "query creative commons"
            search_query = f"ytsearch{n}:{query} royalty free"
            result = ydl.extract_info(search_query, download=False)
            
            entries = result.get('entries', []) if result else []
            for entry in entries:
                if not entry:
                    continue
                title = entry.get('title', '')
                desc = entry.get('description', '')
                url = entry.get('url', '')
                duration = entry.get('duration')
                
                if not url:
                    continue
                    
                duration_secs = float(duration) if duration else 0.0
                # Skip extremely short or extremely long videos
                if duration_secs < 5 or duration_secs > 600:
                    continue
                
                title_lower = title.lower()
                desc_lower = (desc or '').lower()
                
                # Check for royalty-free keywords
                has_free_keywords = any(kw in (title_lower + " " + desc_lower) for kw in [
                    "royalty free", "copyright free", "no copyright", "free to use",
                    "creative commons", "cc0", "public domain", "free stock footage", "stock video free"
                ])
                
                # Skip if there are clear copyright restrictions
                has_copyright_restriction = any(kw in desc_lower for kw in [
                    "all rights reserved", "do not copy", "copyright protected", "unauthorized reuse prohibited"
                ])
                
                if has_free_keywords and not has_copyright_restriction:
                    # Get highest resolution thumbnail
                    thumbnails = entry.get("thumbnails", [])
                    thumb_url = ""
                    if thumbnails:
                        # Find thumbnail with highest height/width or just take the last one
                        thumb_url = thumbnails[-1].get("url", "")
                        
                    candidates.append({
                        "source": "YouTube",
                        "video_url": url,
                        "thumb_url": thumb_url,
                        "title": title,
                        "description": desc or "",
                        "duration": duration_secs
                    })
                    
        print(f"[B-roll] Found {len(candidates)} valid YouTube candidates using yt-dlp.")
        return candidates
    except Exception as e:
        print(f"[B-roll] YouTube search via yt-dlp failed: {e}")
        return []


def _download_video_robust(url: str, out_path: str, segment_index: int) -> bool:
    try:
        # Check if downloading from YouTube
        if "youtube.com" in url or "youtu.be" in url:
            print(f"[B-roll] Downloading YouTube video using yt-dlp: {url}...")
            import yt_dlp
            ydl_opts = {
                'format': 'bestvideo[height<=1080][ext=mp4]/best[height<=1080][ext=mp4]/best',
                'outtmpl': out_path,
                'quiet': True,
                'no_warnings': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return os.path.exists(out_path) and os.path.getsize(out_path) > 10_000

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

def _shorten_narration(text: str, max_words: int = 10) -> str:
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def _image_to_ken_burns_video(img_path: str, out_path: str, w: int, h: int, duration: float = 6.0, niche: str = "general", caption: str = ""):
    """
    Converts a static image to a video with a slow cinematic zoom (Ken Burns effect).
    Tries HeyGen Hyperframes for high-quality niche-specific motion overlays.
    Falls back to robust FFmpeg zoompan filter if Hyperframes fails.
    """
    try:
        import json
        import subprocess
        
        niche_map = {
            "science": "science",
            "nature": "nature",
            "mystery": "nature",
            "engineering": "engineering",
            "business": "business",
            "general": "general"
        }
        mapped_niche = niche_map.get(niche, "general")
        
        abs_img = os.path.abspath(img_path)
        abs_out = os.path.abspath(out_path)
        template_dir = os.path.abspath("pipeline/hyperframes_templates")
        
        variables = {
            "imageUrl": f"file://{abs_img}",
            "duration": duration,
            "niche": mapped_niche,
            "caption": caption
        }
        
        resolution = "portrait" if h > w else "landscape"
        template_file = "index_portrait.html" if h > w else "index_landscape.html"
        cmd = [
            "npx", "hyperframes", "render", template_dir,
            "-c", template_file,
            "--output", abs_out,
            "--resolution", resolution,
            "--quality", "high",
            "--variables", json.dumps(variables)
        ]
        
        print(f"[B-roll] Rendering Hyperframes with niche={mapped_niche}...")
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 10_000:
            print(f"[B-roll] Hyperframes render successful: {out_path}")
            return
        else:
            print("[B-roll] Hyperframes render failed or returned empty file. Falling back to FFmpeg.")
    except Exception as e:
        print(f"[B-roll] Hyperframes execution error: {e}. Falling back to FFmpeg.")

    fps    = 30
    frames = int(duration * fps)

    styles = [
        f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}",
        f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':d={frames}:x=0:y=0:s={w}x{h}:fps={fps}",
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


def _extract_collage_to_file(video_path: str, out_path: str) -> bool:
    try:
        from PIL import Image
        import subprocess
        # Get video duration
        cmd_dur = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        duration = float(subprocess.check_output(cmd_dur).decode().strip())
        if duration <= 0:
            return False
            
        # Extract 3 frames at 20%, 50%, 80%
        timestamps = [duration * 0.2, duration * 0.5, duration * 0.8]
        frames = []
        
        for idx, ts in enumerate(timestamps):
            temp_frame = f"{video_path}_collage_f_{idx}.jpg"
            # Extract frame at ts
            cmd = [
                "ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", video_path,
                "-vframes", "1", "-f", "image2", temp_frame
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if os.path.exists(temp_frame):
                try:
                    img = Image.open(temp_frame).convert("RGB")
                    # Resize to keep aspect ratio but limit size (e.g. height 240)
                    img.thumbnail((320, 240))
                    frames.append((img, temp_frame))
                except Exception:
                    if os.path.exists(temp_frame):
                        os.remove(temp_frame)
                        
        if not frames:
            return False
            
        # Stitch frames horizontally
        widths, heights = zip(*(f[0].size for f in frames))
        total_width = sum(widths)
        max_height = max(heights)
        
        collage = Image.new('RGB', (total_width, max_height))
        x_offset = 0
        for img, path in frames:
            collage.paste(img, (x_offset, 0))
            x_offset += img.size[0]
            # Clean up temp frame
            os.remove(path)
            
        collage.save(out_path, "JPEG", quality=80)
        return True
    except Exception as e:
        print(f"[B-roll] Failed to create collage for {video_path}: {e}")
        return False


def _expand_query(query: str, channel: str, n: int = 5) -> list[str]:
    from pipeline.gemini import _post_with_rotation
    from pipeline.config import GEMINI_API_BASE, GEMINI_FLASH
    try:
        prompt_text = (
            f"You are a video search expert. Topic: '{query}'. Channel: {channel}.\n"
            f"Generate {n} SHORT search queries (2-4 words) to find relevant b-roll footage.\n"
            f"Think: synonyms, visual angles, related concepts, settings.\n"
            f"Return ONLY a JSON array of strings."
        )
        url = f"{GEMINI_API_BASE}/models/{GEMINI_FLASH}:generateContent?key={{key}}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
            },
        }
        resp = _post_with_rotation(url, payload, timeout=30)
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        items = json.loads(raw)
        if isinstance(items, list):
            res = []
            for item in items:
                if isinstance(item, str):
                    s = item.strip()
                    if s and s.lower() != query.lower():
                        res.append(s)
            seen = set()
            deduped = []
            for item in res:
                if item.lower() not in seen:
                    seen.add(item.lower())
                    deduped.append(item)
            return deduped
        return []
    except Exception:
        return []


def _score_candidate(item: dict, query: str, target_duration: float = 8.0) -> float:
    text_to_check = ""
    for field in ["title", "tags", "video_url", "thumb_url"]:
        val = item.get(field)
        if isinstance(val, str):
            text_to_check += " " + val
        elif isinstance(val, list):
            text_to_check += " " + " ".join(str(v) for v in val)
            
    query_words = [w.strip(",.?!:;-()\"'").lower() for w in query.split()]
    query_words = [w for w in query_words if len(w) > 2]
    if not query_words:
        query_words = [w.strip(",.?!:;-()\"'").lower() for w in query.split() if w]
        
    overlap_score = 0.0
    if query_words:
        matches = sum(1 for w in query_words if w in text_to_check.lower())
        overlap_score = (matches / len(query_words)) * 30.0
        
    width = item.get("width")
    height = item.get("height")
    res_score = 5.0
    url_str = str(item.get("video_url", "")).lower()
    
    if isinstance(width, (int, float)) and width > 0:
        if width >= 3840:
            res_score = 25.0
        elif width >= 1920:
            res_score = 20.0
        elif width >= 1280:
            res_score = 10.0
        else:
            res_score = 5.0
    elif isinstance(height, (int, float)) and height > 0:
        if height >= 2160:
            res_score = 25.0
        elif height >= 1080:
            res_score = 20.0
        elif height >= 720:
            res_score = 10.0
        else:
            res_score = 5.0
    else:
        if "4k" in url_str or "2160p" in url_str:
            res_score = 25.0
        elif "1080p" in url_str or "1920" in url_str or "hd" in url_str:
            res_score = 20.0
        elif "720p" in url_str or "1280" in url_str:
            res_score = 10.0
        else:
            res_score = 5.0
            
    dur_score = 10.0
    item_dur = item.get("duration")
    if isinstance(item_dur, (int, float)) and item_dur > 0:
        diff = abs(item_dur - target_duration)
        dur_score = max(0.0, 20.0 - 2.0 * diff)
        
    source_weights = {
        "youtube": 25.0,
        "nasa": 20.0,
        "dvids": 18.0,
        "wikimedia": 16.0,
        "archive": 14.0,
        "coverr": 18.0,
        "pexels": 15.0,
        "pixabay": 14.0,
        "klipy": 8.0
    }
    source_lower = str(item.get("source", "")).lower()
    source_score = source_weights.get(source_lower, 10.0)
    
    return float(overlap_score + res_score + dur_score + source_score)


def fetch_broll(query: str, format_type: str, segment_index: int, duration: float = 6.0, narration: str = "", alt_queries: list[str] | None = None, used_urls: set[str] | None = None, channel: str = "general") -> str:
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

    if not budget_exceeded():
        expanded = _expand_query(query, channel=channel, n=5)
        queries_to_try.extend(expanded)

    # Deduplicate final query list (case-insensitive while preserving order)
    seen_q = set()
    queries_to_try_dedup = []
    for q in queries_to_try:
        if q.lower() not in seen_q:
            seen_q.add(q.lower())
            queries_to_try_dedup.append(q)
    queries_to_try = queries_to_try_dedup

    # Gather candidate video metadata from platforms in parallel
    candidates = []

    CHANNEL_SOURCE_PRIORITY = {
        "science":     ["youtube", "nasa", "dvids", "wikimedia", "coverr", "archive", "pexels", "pixabay"],
        "nature":      ["youtube", "pexels", "pixabay", "coverr", "wikimedia", "archive"],
        "mystery":     ["youtube", "archive", "wikimedia", "coverr", "pexels", "pixabay"],
        "engineering": ["youtube", "nasa", "dvids", "coverr", "wikimedia", "pexels", "archive"],
        "business":    ["youtube", "coverr", "pexels", "pixabay", "klipy"],
        "general":     ["youtube", "coverr", "pexels", "pixabay", "nasa", "wikimedia", "archive", "dvids"],
    }

    def run_source_query(source: str, q: str) -> list[dict]:
        try:
            if source == "youtube":
                return _youtube_candidates(q, n=3)
            elif source == "nasa":
                if not NASA_BROLL_ENABLED:
                    return []
                cand = _nasa_video_candidate(q)
                return [cand] if cand else []
            elif source == "wikimedia":
                cand = _wikimedia_video_candidate(q)
                return [cand] if cand else []
            elif source == "dvids":
                return _dvids_candidates(q, n=3)
            elif source == "coverr":
                if not COVERR_API_KEY:
                    return []
                return _coverr_candidates(q, orientation, n=2)
            elif source == "klipy":
                if not KLIPY_API_KEY:
                    return []
                return _klipy_candidates(q, n=2)
            elif source == "pexels":
                if not PEXELS_API_KEY:
                    return []
                return _pexels_candidates(q, orientation, n=2)
            elif source == "pixabay":
                if not PIXABAY_API_KEY:
                    return []
                return _pixabay_candidates(q, n=2)
            elif source == "archive":
                return _archive_candidates(q, n=3)
        except Exception as e:
            print(f"[B-roll] Source {source} query '{q}' failed: {e}")
        return []

    sources = CHANNEL_SOURCE_PRIORITY.get(channel, CHANNEL_SOURCE_PRIORITY["general"])
    tasks = []
    for source in sources:
        for q in queries_to_try[:3]:
            tasks.append((source, q))

    seen_gathering = set()
    source_counts = {src: 0 for src in sources}

    remaining_budget = max(1.0, deadline - time.monotonic())
    timeout = min(45, int(remaining_budget * 0.5))
    if timeout < 1:
        timeout = 1

    print(f"[B-roll] Segment {segment_index}: starting parallel candidate gathering with timeout={timeout}s for sources: {sources}...")

    with ThreadPoolExecutor(max_workers=min(12, len(tasks))) as executor:
        future_to_info = {}
        for source, q in tasks:
            f = executor.submit(run_source_query, source, q)
            future_to_info[f] = (source, q)

        try:
            for future in as_completed(future_to_info.keys(), timeout=timeout):
                source, q = future_to_info[future]
                try:
                    res = future.result()
                    if res:
                        added_count = 0
                        for cand in res:
                            if not isinstance(cand, dict):
                                continue
                            v_url = cand.get("video_url")
                            if v_url and v_url not in seen_gathering:
                                seen_gathering.add(v_url)
                                if "source" not in cand:
                                    cand["source"] = source
                                candidates.append(cand)
                                added_count += 1
                        source_counts[source] += added_count
                except Exception as e:
                    print(f"[B-roll] Future failed for source {source} query '{q}': {e}")
        except Exception as e:
            if "TimeoutError" in type(e).__name__:
                print(f"[B-roll] Parallel gathering timed out after {timeout} seconds.")
            else:
                print(f"[B-roll] Error during parallel gathering: {e}")

    for src in sources:
        print(f"[B-roll] Source '{src}' returned {source_counts[src]} unique candidates.")

    # Apply de-duplication: filter out candidates that have already been used
    if used_urls:
        original_count = len(candidates)
        candidates = [c for c in candidates if c["video_url"] not in used_urls]
        if len(candidates) < original_count:
            print(f"[B-roll] De-duplicated candidates: filtered out {original_count - len(candidates)} already used clips.")

    # Score all candidates
    for c in candidates:
        c["_score"] = _score_candidate(c, query, target_duration=duration)

    # Sort descending by score
    candidates.sort(key=lambda x: x.get("_score", 0.0), reverse=True)

    # Send only the top 8 to vision_rank_broll
    candidates = candidates[:8]

    # Print the top sources in order so the log shows ranking
    if candidates:
        ranking_str = ", ".join(f"{c.get('source', 'Unknown')} (score: {c.get('_score', 0.0):.1f})" for c in candidates)
        print(f"[B-roll] Top candidates after scoring: {ranking_str}")

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
                temp_video_path = f"output/temp_video_{segment_index}.mp4"
                if _download_video_robust(chosen["video_url"], temp_video_path, segment_index):
                    if used_urls is not None:
                        used_urls.add(chosen["video_url"])
                    print(f"[B-roll] Video downloaded. Running Hyperframes overlays...")
                    _image_to_ken_burns_video(temp_video_path, out_path, w, h, duration, niche=channel, caption="")
                    if os.path.exists(temp_video_path):
                        try:
                            os.remove(temp_video_path)
                        except Exception:
                            pass
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
            if _extract_collage_to_file(temp_v, temp_f):
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
            
            # Run the video through Hyperframes overlays
            print(f"[B-roll] Parallel winner video. Running Hyperframes overlays...")
            _image_to_ken_burns_video(winner["temp_v"], out_path, w, h, duration, niche=channel, caption="")
            
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
            _image_to_ken_burns_video(img_path, out_path, w, h, duration, niche=channel, caption="")
            return out_path
        except Exception as e:
            print(f"[B-roll] Image source failed: {e}. Trying Pollinations…")

    # ── Fallback 3: Pollinations AI image ─────────────────────────────────────────────────
    if _pollinations_image(query, w, h, img_path):
        print(f"[B-roll] Segment {segment_index}: Pollinations OK. Applying Ken Burns…")
        _image_to_ken_burns_video(img_path, out_path, w, h, duration, niche=channel, caption="")
        return out_path

    # ── Fallback 4: PIL gradient placeholder ──────────────────────────────────────────────
    print(f"[B-roll] Segment {segment_index}: all sources failed. Using gradient placeholder.")
    _pil_placeholder(query, w, h, img_path)
    _image_to_ken_burns_video(img_path, out_path, w, h, duration, niche=channel, caption="")
    return out_path

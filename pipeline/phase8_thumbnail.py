import os
import json
import random
import subprocess
from pipeline.config import THUMBNAIL_LAYOUTS

_LAYOUT_STATE_FILE = "thumbnail_state.json"

def _load_last_layout() -> str | None:
    if os.path.exists(_LAYOUT_STATE_FILE):
        try:
            with open(_LAYOUT_STATE_FILE) as f:
                return json.load(f).get("last_layout")
        except Exception:
            return None
    return None

def _save_last_layout(layout: str):
    with open(_LAYOUT_STATE_FILE, "w") as f:
        json.dump({"last_layout": layout}, f)

def clean_thumbnail_text(text: str) -> str:
    cleaned = "".join(c for c in text if c.isalnum() or c in " -!?")
    return cleaned.replace("'", "'\\\\''")

def _build_filter(layout: str, cleaned_text: str) -> str:
    """Return an FFmpeg -vf filter string for the given layout."""
    import random
    text_color = random.choice(["#FFDD00", "#FF2D55", "#00C7FC", "#FFFFFF", "#FF9500"])
    shadow = "shadowcolor=black@0.55:shadowx=6:shadowy=6"
    
    if layout == "dark_top_bar":
        return (
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"drawbox=x=0:y=0:w=iw:h=190:color=black@0.75:t=fill,"
            f"drawtext=text='{cleaned_text}':font='Bebas Neue':fontsize=105:"
            f"fontcolor='{text_color}':borderw=6:bordercolor=black:{shadow}:x=(w-text_w)/2:y=65"
        )
    elif layout == "centered_gradient":
        return (
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"drawbox=x=0:y=210:w=iw:h=300:color=black@0.65:t=fill,"
            f"drawtext=text='{cleaned_text}':font='Bebas Neue':fontsize=115:"
            f"fontcolor='{text_color}':borderw=8:bordercolor=black:{shadow}:x=(w-text_w)/2:y=(h-text_h)/2"
        )
    elif layout == "bottom_third":
        return (
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"drawbox=x=0:y=490:w=iw:h=230:color=black@0.80:t=fill,"
            f"drawtext=text='{cleaned_text}':font='Bebas Neue':fontsize=100:"
            f"fontcolor='{text_color}':borderw=6:bordercolor=black:{shadow}:x=(w-text_w)/2:y=535"
        )
    else:  # split_left
        return (
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"drawbox=x=0:y=0:w=560:h=ih:color=black@0.75:t=fill,"
            f"drawtext=text='{cleaned_text}':font='Bebas Neue':fontsize=90:"
            f"fontcolor='{text_color}':borderw=5:bordercolor=black:{shadow}:x=40:y=(h-text_h)/2"
        )

def generate_thumbnail(final_video_path: str, thumbnail_text: str) -> str:
    print(f"Generating thumbnail for '{thumbnail_text}'...")
    os.makedirs("output", exist_ok=True)

    hook_frame_path = "output/hook_frame.jpg"
    thumbnail_path  = "output/thumbnail.jpg"

    # 1. Extract best frame
    subprocess.run(
        ["ffmpeg", "-y", "-i", final_video_path,
         "-vf", "thumbnail=n=300", "-frames:v", "1", "-q:v", "2", hook_frame_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # 2. Pick layout — avoid repeating last one
    last = _load_last_layout()
    available = [l for l in THUMBNAIL_LAYOUTS if l != last]
    if not available:
        available = THUMBNAIL_LAYOUTS
    layout = random.choice(available)
    print(f"[Thumbnail] Layout: {layout}")

    cleaned = clean_thumbnail_text(thumbnail_text).upper()
    vf = _build_filter(layout, cleaned)

    # 3. Try Bebas Neue, fallback to DejaVu Sans Bold
    cmd = ["ffmpeg", "-y", "-i", hook_frame_path, "-vf", vf, "-q:v", "2", thumbnail_path]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("Bebas Neue failed, retrying with DejaVu Sans Bold...")
        vf_fallback = vf.replace("font='Bebas Neue':fontsize=110", "font='DejaVu Sans Bold':fontsize=90")
        vf_fallback = vf_fallback.replace("font='Bebas Neue':fontsize=100", "font='DejaVu Sans Bold':fontsize=85")
        vf_fallback = vf_fallback.replace("font='Bebas Neue':fontsize=95", "font='DejaVu Sans Bold':fontsize=80")
        vf_fallback = vf_fallback.replace("font='Bebas Neue':fontsize=85", "font='DejaVu Sans Bold':fontsize=75")
        cmd_fb = ["ffmpeg", "-y", "-i", hook_frame_path, "-vf", vf_fallback, "-q:v", "2", thumbnail_path]
        subprocess.run(cmd_fb, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    _save_last_layout(layout)
    print(f"Thumbnail generated: {thumbnail_path}")
    return thumbnail_path

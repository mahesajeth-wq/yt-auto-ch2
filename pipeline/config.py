import os

# ── Gemini Key Pool ──────────────────────────────────────────────────────────
# GEMINI_API_KEYS = comma-separated list (e.g. "key1,key2,key3")
# Multiple keys from DIFFERENT Google accounts give truly separate quotas.
# Multiple keys from the SAME account share the same daily quota but help
# with per-minute rate limits (RPM throttling).
def _load_keys() -> list[str]:
    keys: list[str] = []
    multi = os.environ.get("GEMINI_API_KEYS", "").strip()
    if multi:
        keys.extend(k.strip() for k in multi.split(",") if k.strip())
    single = os.environ.get("GEMINI_API_KEY", "").strip()
    if single and not keys:
        keys.append(single)
    return list(dict.fromkeys(keys))

GEMINI_API_KEYS: list[str] = _load_keys()
GEMINI_API_KEY: str = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""

# Dedicated key for the video Judge (keeps generation quota separate)
GEMINI_JUDGE_API_KEY: str = os.environ.get("GEMINI_JUDGE_API_KEY", "").strip() or GEMINI_API_KEY

# ── Other APIs ───────────────────────────────────────────────────────────────
PEXELS_API_KEY   = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY  = os.environ.get("PIXABAY_API_KEY", "")
COVERR_API_KEY   = os.environ.get("COVERR_API_KEY", "")   # free at coverr.co/developers
NASA_API_KEY     = os.environ.get("NASA_API_KEY", "DEMO_KEY")  # free at api.nasa.gov
KLIPY_API_KEY    = os.environ.get("KLIPY_API_KEY", "")
FREESOUND_API_KEY = os.environ.get("FREESOUND_API_KEY", "")

# ── YouTube OAuth ────────────────────────────────────────────────────────────
YT_CLIENT_ID     = os.environ.get("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN", "")

# ── Gemini Models ────────────────────────────────────────────────────────────
GEMINI_FLASH     = "gemini-2.5-flash"
GEMINI_PRO       = "gemini-2.5-flash"          # deliberately using flash; 1.5-pro = 2 RPD free
GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_API_BASE  = "https://generativelanguage.googleapis.com/v1beta"

GEMINI_VOICES    = ["Aoede","Charon","Fenrir","Kore","Puck","Leda","Orus","Callirrhoe","Achird","Iapetus"]
KOKORO_VOICES    = ["af_heart","af_bella","af_nicole","af_sarah","af_sky","af_aoede","am_adam","am_michael","am_fenrir","am_puck"]

# ── Video Specs ──────────────────────────────────────────────────────────────
SHORTS_W, SHORTS_H = 1080, 1920
LONG_W,   LONG_H   = 1920, 1080
FPS                 = 30
TOPIC_LOG_SIZE      = 90

HOOK_PATTERNS = [
    "The {topic} fact that breaks a rule you learned in school",
    "In exactly 30 seconds you'll never see {topic} the same way",
    "Scientists found something inside {topic} that shouldn't exist",
    "The {topic} detail that 99% of people never notice — even experts",
    "What {topic} does when no one is watching will disturb you",
    "The one thing about {topic} that every textbook gets wrong",
    "This single {topic} fact overturns 100 years of assumptions",
    "You've seen {topic} your whole life. You've never actually seen it.",
]

# 4 layout variants — rotated per video to avoid similarity-score flagging
THUMBNAIL_LAYOUTS = [
    "dark_top_bar",       # original: dark bar at top, yellow text centred
    "centered_gradient",  # text centred on a dark-to-transparent gradient overlay
    "bottom_third",       # text in lower third, full-bleed frame behind it
    "split_left",         # dark left panel with text, right panel shows frame
]

# topic sub-cluster rotation for Natural World Mysteries channel
NATURAL_WORLD_SUBCLUSTERS = [
    "deep ocean and marine biology",
    "forest ecosystems and plant behavior",
    "extreme weather and atmospheric phenomena",
    "geology ancient earth and extreme environments",
    "wild animal behavior and survival strategies",
]

YT_CATEGORY_EDUCATION = "27"
YT_CATEGORY_SCIENCE   = "28"


def validate_config():
    missing = []
    if not GEMINI_API_KEYS:
        missing.append("GEMINI_API_KEY or GEMINI_API_KEYS")
    for var, val in [("PEXELS_API_KEY", PEXELS_API_KEY),
                     ("YT_CLIENT_ID", YT_CLIENT_ID),
                     ("YT_CLIENT_SECRET", YT_CLIENT_SECRET),
                     ("YT_REFRESH_TOKEN", YT_REFRESH_TOKEN)]:
        if not val:
            missing.append(var)
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")
    n = len(GEMINI_API_KEYS)
    print(f"[Config] {n} Gemini generation key(s) loaded.")
    if GEMINI_JUDGE_API_KEY != GEMINI_API_KEY:
        print("[Config] Separate GEMINI_JUDGE_API_KEY active — Judge uses its own quota.")
    if COVERR_API_KEY:
        print("[Config] Coverr API: enabled (cinematic B-roll tier active).")
    if NASA_API_KEY:
        print(f"[Config] NASA API: enabled (key={'DEMO_KEY (rate-limited)' if NASA_API_KEY == 'DEMO_KEY' else 'custom'}).")
    if FREESOUND_API_KEY:
        print("[Config] Freesound API: enabled (CC0 ambient music tier active).")

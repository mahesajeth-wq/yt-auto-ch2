"""
phase6_music.py — Procedural ambient background music generator.
Generates calm chord-pad loops with a soft shaker pulse using pure numpy.
No torch, no transformers, no model download, no OOM risk. Runs in <2s.

If a FREESOUND_API_KEY is set, tries Freesound CC0 ambient tracks first,
then falls back to the procedural generator on any failure.
"""
import os
import random
import shutil
import subprocess
import tempfile
import numpy as np
import wave

SAMPLE_RATE = 44100
_NOTE_FREQS = {  # octave-4 reference frequencies
    "C": 261.63, "C#": 277.18, "D": 293.66, "D#": 311.13, "E": 329.63,
    "F": 349.23, "F#": 369.99, "G": 392.00, "G#": 415.30, "A": 440.00,
    "A#": 466.16, "B": 493.88,
}
# Calm/ambient 4-chord loops: (root, quality)
_PROGRESSIONS = [
    [("C", "maj"), ("A", "min"), ("F", "maj"), ("G", "maj")],
    [("A", "min"), ("F", "maj"), ("C", "maj"), ("G", "maj")],
    [("D", "min"), ("A#", "maj"), ("F", "maj"), ("C", "maj")],
    [("E", "min"), ("C", "maj"), ("G", "maj"), ("D", "maj")],
]


def _adsr(n, attack, release):
    env = np.ones(n)
    a, r = min(attack, n // 2), min(release, n // 2)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.linspace(1, 0, r)
    return env


def _chord(root, quality, duration, base_octave=3):
    intervals = [0, 4, 7] if quality == "maj" else [0, 3, 7]
    root_freq = _NOTE_FREQS[root] / (2 ** (4 - base_octave))
    n = int(duration * SAMPLE_RATE)
    t = np.linspace(0, duration, n, endpoint=False)
    wave = sum(np.sin(2 * np.pi * root_freq * (2 ** (iv / 12)) * t) for iv in intervals)
    wave += 0.6 * np.sin(2 * np.pi * (root_freq / 2) * t)          # sub-bass, -1 octave
    return wave * _adsr(n, int(0.35 * SAMPLE_RATE), int(0.6 * SAMPLE_RATE))


def _shaker(n_samples, beat_samples):
    track = np.zeros(n_samples)
    tt = np.linspace(0, 0.05, int(0.05 * SAMPLE_RATE))
    hit = np.random.randn(len(tt)) * np.exp(-tt * 90) * 0.08
    for start in range(0, n_samples - len(hit), beat_samples):
        track[start:start + len(hit)] += hit
    return track


def _ticking_clock(n_samples, tick_interval_samples):
    track = np.zeros(n_samples)
    t_tick = np.linspace(0, 0.015, int(0.015 * SAMPLE_RATE))
    # A fast-decay sine hit (1500Hz) combined with short noise burst for a crisp clock click
    tick_sound = np.sin(2 * np.pi * 1500 * t_tick) * np.exp(-t_tick * 400) * 0.08
    tick_sound += np.random.randn(len(t_tick)) * np.exp(-t_tick * 500) * 0.02
    for start in range(0, n_samples - len(tick_sound), tick_interval_samples):
        track[start:start + len(tick_sound)] += tick_sound
    return track


def _fetch_freesound_music(topic: str, duration_seconds: int) -> str | None:
    """Try to download a CC0 ambient track from Freesound. Returns wav path or None."""
    try:
        import requests
        from pipeline.config import FREESOUND_API_KEY
    except ImportError:
        return None
    if not FREESOUND_API_KEY:
        return None

    search_url = "https://freesound.org/apiv2/search/text/"
    
    is_history, is_engineering, is_natural = False, False, False
    try:
        from pipeline.config import HISTORY_SUBCLUSTERS
        is_history = True
    except ImportError:
        pass
    try:
        from pipeline.config import ENGINEERING_SUBCLUSTERS
        is_engineering = True
    except ImportError:
        pass
    try:
        from pipeline.config import NATURAL_WORLD_SUBCLUSTERS
        is_natural = True
    except ImportError:
        pass

    if "wedding" in topic.lower() or "marriage" in topic.lower() or "romantic" in topic.lower():
        queries = [f"{topic} ambient", "romantic wedding ambient", "indian wedding instrumental"]
    elif is_history:
        queries = [f"{topic} orchestral tension", "cinematic historical music", "medieval tension ambient", "ancient history ambient"]
    elif is_engineering:
        queries = [f"{topic} industrial tech", "machinery industrial ambient", "cinematic suspense synth", "ambient tech synth"]
    elif is_natural:
        queries = [f"{topic} nature ambient", "wildlife cinematic music", "calm flute ambient", "earth atmospheric loop"]
    else:
        queries = [f"{topic} space cinematic", "cinematic suspense synth", "sci-fi tension loop", "ambient space synth"]



    for query in queries:
        print(f"[Music] Searching Freesound for '{query}' ...")
        params = {
            "query": query,
            "filter": f'duration:[{duration_seconds} TO {duration_seconds * 4}] license:"Creative Commons 0"',
            "fields": "id,name,duration,previews",
            "page_size": 5,
            "token": FREESOUND_API_KEY,
        }
        try:
            resp = requests.get(search_url, params=params, timeout=30)
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except Exception as exc:
            print(f"[Music] Freesound search failed: {exc}")
            continue

        if not results:
            print(f"[Music] No Freesound results for '{query}', trying next query...")
            continue

        pick = random.choice(results)
        sound_id = pick.get("id")
        preview_url = pick.get("previews", {}).get("preview-hq-mp3")
        if not preview_url:
            print("[Music] Selected result has no HQ preview, skipping.")
            continue

        cache_dir = "cache_music"
        cache_path = os.path.join(cache_dir, f"freesound_{sound_id}.wav")
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
            print(f"[Music] Found cached Freesound track #{sound_id} → {cache_path}")
            return cache_path

        print(f"[Music] Downloading Freesound #{sound_id}: {pick['name']} ({pick['duration']:.1f}s)")
        tmpdir = tempfile.mkdtemp(prefix="freesound_")
        tmp_mp3 = os.path.join(tmpdir, "temp.mp3")
        tmp_wav = os.path.join(tmpdir, "temp.wav")
        try:
            dl = requests.get(preview_url, timeout=30)
            dl.raise_for_status()
            with open(tmp_mp3, "wb") as f:
                f.write(dl.content)

            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_mp3, "-ar", "44100", "-ac", "1", tmp_wav],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            )
            print("[Music] Freesound track converted to wav successfully.")
            
            # Save to cache
            os.makedirs(cache_dir, exist_ok=True)
            shutil.copy(tmp_wav, cache_path)
            print(f"[Music] Cached Freesound track to {cache_path}")
            
            return tmp_wav
        except Exception as exc:
            print(f"[Music] Freesound download/convert failed: {exc}")
            continue

    return None


def generate_music(topic: str, duration_seconds: int = 35) -> str:
    out_path = "output/music.wav"
    if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
        print("Background music already exists, skipping generation.")
        return out_path

    # ── Try Freesound CC0 first ──────────────────────────────────────────────
    try:
        fs_wav = _fetch_freesound_music(topic, duration_seconds)
        if fs_wav and os.path.exists(fs_wav) and os.path.getsize(fs_wav) > 1000:
            os.makedirs("output", exist_ok=True)
            shutil.copy(fs_wav, out_path)
            print(f"[Music] Using Freesound CC0 track → {out_path}")
            return out_path
    except Exception as exc:
        print(f"[Music] Freesound attempt failed ({exc}), falling back to procedural.")

    # ── Fallback: procedural ambient generation ──────────────────────────────
    print(f"Generating procedural ambient background music ({duration_seconds}s)...")
    os.makedirs("output", exist_ok=True)

    progression = random.choice(_PROGRESSIONS)
    chord_dur = 4.0
    loop = np.concatenate([_chord(r, q, chord_dur) for r, q in progression])
    reps = int(np.ceil(duration_seconds * SAMPLE_RATE / len(loop))) + 1
    track = np.tile(loop, reps)[: int(duration_seconds * SAMPLE_RATE)]
    # Tick every 0.5s (120 BPM) for high-tension pacing
    tick_interval = int(0.5 * SAMPLE_RATE)
    if "wedding" in topic.lower() or "marriage" in topic.lower() or "romantic" in topic.lower():
        # Soft ambient pads only (no shaker, no clock)
        track = track * 0.4
    else:
        track = track + _shaker(len(track), int(chord_dur * SAMPLE_RATE / 2)) + _ticking_clock(len(track), tick_interval)


    track = track / (np.max(np.abs(track)) + 1e-9) * 0.65
    track_int16 = (track * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(track_int16.tobytes())
    print(f"Procedural music saved ({progression})")
    return out_path

"""
sfx.py — Dynamic sound effects generator for yt-auto pipeline.
Generates whoosh (clip transitions) and snap (text pop-in) sounds.
Uses Freesound API with synthetic fallbacks.

All audio at 44100 Hz, mono, int16.
"""
import os
import random
import wave
import numpy as np


def _synth_whoosh(sample_rate: int = 44100, duration: float = 0.38) -> np.ndarray:
    """
    Synthetic whoosh: logarithmic frequency sweep (4kHz→200Hz) + noise, shaped envelope.
    Sounds like a quick air rush — ideal for clip-to-clip transitions.
    """
    t       = np.linspace(0, duration, int(sample_rate * duration))
    # Frequency sweep from 4000 Hz down to 200 Hz (logarithmic)
    freq    = np.exp(np.linspace(np.log(4000), np.log(200), len(t)))
    phase   = np.cumsum(2 * np.pi * freq / sample_rate)
    sweep   = np.sin(phase)
    # White noise layer
    noise   = np.random.randn(len(t)) * 0.35
    # Combine sweep + noise
    signal  = sweep * 0.65 + noise * 0.35
    # Envelope: very fast attack (15ms), slow exponential decay
    attack  = 1 - np.exp(-t * 200)
    decay   = np.exp(-t * 9)
    env     = attack * decay
    sfx     = signal * env
    sfx    /= np.max(np.abs(sfx)) + 1e-9
    return (sfx * 32767 * 0.65).astype(np.int16)


def _synth_digital_whoosh(sample_rate: int = 44100, duration: float = 0.45) -> np.ndarray:
    """Synthetic sci-fi digital whoosh: sine sweep + vibrato + noise."""
    t = np.linspace(0, duration, int(sample_rate * duration))
    freq = 1800 - 1400 * (t / duration) + 200 * np.sin(2 * np.pi * 30 * t)
    phase = np.cumsum(2 * np.pi * freq / sample_rate)
    sig = np.sin(phase) * 0.75 + np.random.randn(len(t)) * 0.1
    env = np.sin(np.pi * (t / duration)) ** 1.8
    sfx = sig * env
    sfx /= np.max(np.abs(sfx)) + 1e-9
    return (sfx * 32767 * 0.55).astype(np.int16)


def _synth_snap(sample_rate: int = 44100) -> np.ndarray:
    """
    Synthetic snap/click: short sharp transient.
    Sounds like a camera shutter or finger snap.
    Use when a key image or text pops into frame.
    """
    duration = 0.055
    t        = np.linspace(0, duration, int(sample_rate * duration))
    noise    = np.random.randn(len(t))
    # Very fast exponential decay (0 → silence in ~50ms)
    env      = np.exp(-t * 130)
    sfx      = noise * env
    sfx     /= np.max(np.abs(sfx)) + 1e-9
    return (sfx * 32767 * 0.45).astype(np.int16)


def _synth_impact(sample_rate: int = 44100, duration: float = 1.0) -> np.ndarray:
    """
    Synthetic cinematic impact/boom: high-energy start, rapid frequency sweep (150Hz → 30Hz),
    combined with a short burst of noise, with an exponential decay.
    """
    t = np.linspace(0, duration, int(sample_rate * duration))
    # Sub-bass pitch drop: 150Hz down to 30Hz
    freq = 30 + 120 * np.exp(-t * 12)
    phase = np.cumsum(2 * np.pi * freq / sample_rate)
    sub = np.sin(phase)
    # Add noise transient at the very start (first 80ms)
    noise = np.random.randn(len(t))
    noise_env = np.exp(-t * 45) # fast decay
    noise_layer = noise * noise_env
    # Combine sub-bass + noise transient
    signal = sub * 0.75 + noise_layer * 0.25
    # Exponential decay
    env = np.exp(-t * 3.5)
    sfx = signal * env
    sfx /= np.max(np.abs(sfx)) + 1e-9
    return (sfx * 32767 * 0.85).astype(np.int16)


def _fetch_freesound_sfx(query: str, max_duration: float = 2.0) -> str | None:
    """
    Search Freesound for a short transition/whoosh sound and download/cache it.
    """
    import requests
    try:
        from pipeline.config import FREESOUND_API_KEY
    except ImportError:
        return None
    if not FREESOUND_API_KEY:
        return None

    search_url = "https://freesound.org/apiv2/search/text/"
    params = {
        "query": query,
        "filter": f'duration:[0.1 TO {max_duration}] license:"Creative Commons 0"',
        "fields": "id,name,duration,previews",
        "page_size": 8,
        "token": FREESOUND_API_KEY,
    }
    try:
        resp = requests.get(search_url, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        print(f"[SFX] Freesound search failed: {exc}")
        return None

    if not results:
        return None

    pick = random.choice(results)
    sound_id = pick.get("id")
    preview_url = pick.get("previews", {}).get("preview-hq-mp3")
    if not preview_url:
        return None

    cache_dir = "cache_sfx"
    cache_path = os.path.join(cache_dir, f"sfx_{sound_id}.wav")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
        return cache_path

    print(f"[SFX] Downloading Freesound SFX #{sound_id}: {pick['name']}")
    import tempfile
    import shutil
    import subprocess
    tmpdir = tempfile.mkdtemp(prefix="freesound_sfx_")
    tmp_mp3 = os.path.join(tmpdir, "temp.mp3")
    tmp_wav = os.path.join(tmpdir, "temp.wav")
    try:
        dl = requests.get(preview_url, timeout=15)
        dl.raise_for_status()
        with open(tmp_mp3, "wb") as f:
            f.write(dl.content)

        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_mp3, "-ar", "44100", "-ac", "1", tmp_wav],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
        os.makedirs(cache_dir, exist_ok=True)
        shutil.copy(tmp_wav, cache_path)
        return cache_path
    except Exception as exc:
        print(f"[SFX] Freesound SFX download/convert failed: {exc}")
        return None


def _read_wav_file(filepath: str) -> np.ndarray:
    with wave.open(filepath, 'rb') as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        data = wf.readframes(n_frames)
        
        if sampwidth == 2:
            sig = np.frombuffer(data, dtype=np.int16)
        elif sampwidth == 1:
            sig = (np.frombuffer(data, dtype=np.uint8).astype(np.int16) - 128) * 256
        else:
            raise ValueError("Unsupported sample width")
            
        if n_channels > 1:
            sig = sig.reshape(-1, n_channels).mean(axis=1).astype(np.int16)
            
        return sig


def create_sfx_track(
    clip_boundary_times: list[float],
    total_duration: float,
    sample_rate: int = 44100,
    whoosh_volume: float = 0.30,
    topic: str = "",
) -> str:
    """
    Build a single WAV track that contains an impact at t=0, and a whoosh at each clip boundary time.
    This track is mixed into the final video at low volume via FFmpeg amix.

    Args:
        clip_boundary_times: List of times (seconds) where clips change.
                             Typically cumulative sums of TTS durations.
                             Pass [] for no SFX.
        total_duration:      Total video duration in seconds.
        sample_rate:         Output sample rate (must match FFmpeg resampling target).
        whoosh_volume:       Mix volume for SFX (0.0–1.0). Default 0.30.
        topic:               Optional topic string to search Freesound for context-appropriate whooshes.

    Returns:
        Path to the generated "output/sfx_track.wav".
    """
    os.makedirs("output", exist_ok=True)

    total_samples = int(total_duration * sample_rate)
    track         = np.zeros(total_samples, dtype=np.float64)

    # 1. Opening impact at t=0
    impact = _synth_impact(sample_rate)
    end_imp = min(total_samples, len(impact))
    if end_imp > 0:
        track[0:end_imp] += (impact[:end_imp].astype(np.float64) / 32767) * 0.75

    # 2. Whooshes at clip boundaries
    whoosh_pool = []
    if topic:
        topic_words = [w for w in topic.lower().split() if len(w) > 4]
        queries = ["whoosh", "swoosh", "transition swoosh", "cinematic transition", "digital transition", "glitch transition"]
        if topic_words:
            random.shuffle(topic_words)
            queries = [f"{topic_words[0]} whoosh", f"{topic_words[0]} transition"] + queries
        
        # Download up to 3 context-appropriate SFX files
        for q in queries[:4]:
            path = _fetch_freesound_sfx(q, max_duration=1.5)
            if path:
                try:
                    sig = _read_wav_file(path)
                    whoosh_pool.append(sig)
                except Exception as e:
                    print(f"[SFX] Failed to read WAV {path}: {e}")

    for t_sec in clip_boundary_times:
        if whoosh_pool:
            w_sig = random.choice(whoosh_pool)
        else:
            w_sig = random.choice([_synth_whoosh(sample_rate), _synth_digital_whoosh(sample_rate)])

        # Place whoosh 0.12s BEFORE the boundary so it arrives naturally
        start = max(0, int((t_sec - 0.12) * sample_rate))
        end   = min(total_samples, start + len(w_sig))
        length = end - start
        if length > 0:
            track[start:end] += (w_sig[:length].astype(np.float64) / 32767) * whoosh_volume

    # Clip to int16
    track_int16 = np.clip(track * 32767, -32768, 32767).astype(np.int16)
    out_path    = "output/sfx_track.wav"
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(track_int16.tobytes())
    print(f"[SFX] SFX track created: 1 impact, {len(clip_boundary_times)} whoosh(es) at {clip_boundary_times}")
    return out_path

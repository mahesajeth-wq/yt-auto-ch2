"""
sfx.py — Synthetic sound effects generator for yt-auto pipeline.
Generates whoosh (clip transitions) and snap (text pop-in) sounds
using pure numpy. No external audio files or API keys required.

All audio at 44100 Hz, mono, int16.
"""
import os
import numpy as np
import wave


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


def create_sfx_track(
    clip_boundary_times: list[float],
    total_duration: float,
    sample_rate: int = 44100,
    whoosh_volume: float = 0.30,
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
    whoosh        = _synth_whoosh(sample_rate)
    for t_sec in clip_boundary_times:
        # Place whoosh 0.12s BEFORE the boundary so it arrives naturally
        start = max(0, int((t_sec - 0.12) * sample_rate))
        end   = min(total_samples, start + len(whoosh))
        length = end - start
        if length > 0:
            track[start:end] += (whoosh[:length].astype(np.float64) / 32767) * whoosh_volume

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

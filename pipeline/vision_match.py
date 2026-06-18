import base64
import io
import json
from PIL import Image
from pipeline.gemini import _post_with_rotation
from pipeline.config import GEMINI_FLASH, GEMINI_API_BASE

def _shrink(img_bytes: bytes, max_dim: int = 768) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()

def vision_rank_broll(
    thumbnails: list[bytes],
    narration: str,
    query: str,
) -> tuple[int | None, bool]:
    """
    Scores candidate B-roll thumbnails against the EXACT narration sentence.
    Strict mode: rejects generic stock clips, symbolic stand-ins, and
    anything that doesn't specifically represent the concept in the narration.
    Returns (best_index, match_found).
    match_found=False means caller must continue the waterfall — do NOT
    silently accept a bad clip.
    """
    if not thumbnails:
        return None, False

    # Build the strict matching prompt
    parts = [{
        "text": (
            f'NARRATION (exact sentence for this video segment):\n'
            f'"{narration}"\n\n'
            f'SEARCH QUERY used: "{query}"\n\n'
            f'You are evaluating {len(thumbnails)} candidate B-roll thumbnail(s) '
            f'(indexed 0 to {len(thumbnails) - 1}) for the above narration.\n\n'
            f'STRICT SCORING RULES — read carefully:\n'
            f'1. The clip must SPECIFICALLY represent the concept, creature, '
            f'phenomenon, or place named in the narration. A vague thematic '
            f'connection is NOT enough.\n'
            f'2. REJECT any clip that shows:\n'
            f'   - Generic office workers, handshakes, or people at computers\n'
            f'   - Abstract light effects, bokeh, or undefined particle animations\n'
            f'   - A generic human doing an unrelated activity\n'
            f'   - Any scene that could belong to a completely different video topic\n'
            f'3. ACCEPT only if the clip would make a viewer think "yes, this is '
            f'exactly what the voiceover is describing right now."\n'
            f'4. If multiple candidates pass, pick the one with the closest '
            f'visual match to the SPECIFIC subject of the narration.\n'
            f'5. If NO candidate passes the strict test above, return '
            f'match_found=false.\n\n'
            f'Return ONLY valid JSON (no markdown):\n'
            f'{{"best_index": <int or null>, '
            f'"match_found": <bool>, '
            f'"confidence": <0-100 int>, '
            f'"reject_reason": "<why rejected, or empty string if accepted>"}}\n\n'
            f'Set match_found=false if confidence < 65.'
        )
    }]

    for t in thumbnails:
        parts.append({
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": base64.b64encode(_shrink(t)).decode(),
            }
        })

    url = f"{GEMINI_API_BASE}/models/{GEMINI_FLASH}:generateContent?key={{key}}"
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.05,   # very low — deterministic judgment
            "responseMimeType": "application/json",
        },
    }

    try:
        resp = _post_with_rotation(url, payload, timeout=60)
        raw  = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(raw)

        idx        = data.get("best_index")
        found      = bool(data.get("match_found", False))
        confidence = int(data.get("confidence", 0))
        reason     = data.get("reject_reason", "")

        if reason:
            print(f"[VisionMatch] Rejected: {reason} (confidence={confidence})")

        if not (found and isinstance(idx, int) and 0 <= idx < len(thumbnails)):
            return None, False
        if confidence < 65:
            print(f"[VisionMatch] Low confidence ({confidence}) — rejecting.")
            return None, False

        print(f"[VisionMatch] Accepted index {idx} (confidence={confidence})")
        return idx, True

    except Exception as e:
        # IMPORTANT: do NOT silently accept on failure.
        # Return (None, False) so the waterfall continues to the next source.
        print(f"[VisionMatch] Failed/rate-limited: {e}. Continuing waterfall.")
        return None, False

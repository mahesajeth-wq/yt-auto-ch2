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
    Ranks candidates by semantic fit, not first-provider wins.
    Returns (best_index, match_found).
    match_found=True means the best available candidate is worth using;
    final Judge AI can still reject/repair the assembled segment later.
    """
    if not thumbnails:
        return None, False

    import os
    if os.environ.get("BYPASS_VISION_MATCH") == "1":
        print("[VisionMatch] Bypassing Vision Match (BYPASS_VISION_MATCH=1). Accepting index 0.")
        return 0, True

    # Build the strict matching prompt
    parts = [{
        "text": (
            f'NARRATION (exact sentence for this video segment):\n'
            f'"{narration}"\n\n'
            f'SEARCH QUERY used: "{query}"\n\n'
            f'You are evaluating {len(thumbnails)} candidate B-roll thumbnail(s) '
            f'(indexed 0 to {len(thumbnails) - 1}) for the above narration.\n\n'
            f'SCORING RULES — read carefully:\n'
            f'1. The clip must represent the general subject, device, concept, or process discussed in the narration or search query. '
            f'Do NOT reject general subject videos (e.g. a washing machine, general engine, or factory) just because they do not depict the specific '
            f'internal component or microscopic failure mentioned in the narration. A general thematic match of the main subject is highly acceptable (scores 75-90) and far better than falling back to static images.\n'
            f'2. Score every candidate from 0-100:\n'
            f'   - 90-100: exact subject or highly specific real-world match\n'
            f'   - 75-89: strong contextual/thematic match of the main subject\n'
            f'   - 55-74: usable fallback or generic filler related to the topic\n'
            f'   - 0-54: bad mismatch or completely unrelated topic\n'
            f'3. Penalize clips showing:\n'
            f'   - Generic office workers, handshakes, or people at computers\n'
            f'   - Abstract light effects, bokeh, or undefined particle animations\n'
            f'   - A generic human doing an unrelated activity\n'
            f'   - Any scene that could belong to a completely different video topic\n'
            f'4. Pick the highest-scoring candidate even when imperfect, so the pipeline can use the best available asset from all providers.\n'
            f'5. Set match_found=false only when the best candidate scores below 55.\n\n'
            f'Return ONLY valid JSON (no markdown):\n'
            f'{{"best_index": <int or null>, '
            f'"match_found": <bool>, '
            f'"confidence": <0-100 int>, '
            f'"candidate_scores": [<0-100 int for each candidate>], '
            f'"reject_reason": "<why rejected, or empty string if accepted>"}}\n\n'
            f'Set match_found=true if confidence >= 55. Still explain weaknesses in reject_reason if confidence < 75.'
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
        scores     = data.get("candidate_scores", [])
        reason     = data.get("reject_reason", "")

        if isinstance(scores, list) and scores:
            print(f"[VisionMatch] Candidate scores: {scores}")
        if reason:
            print(f"[VisionMatch] Note: {reason} (confidence={confidence})")

        if not (found and isinstance(idx, int) and 0 <= idx < len(thumbnails)):
            return None, False
        if confidence < 55:
            print(f"[VisionMatch] Very low confidence ({confidence}) — rejecting.")
            return None, False

        quality = "strong" if confidence >= 75 else "fallback"
        print(f"[VisionMatch] Accepted {quality} index {idx} (confidence={confidence})")
        return idx, True

    except Exception as e:
        # IMPORTANT: do NOT silently accept on failure.
        # Return (None, False) so the waterfall continues to the next source.
        print(f"[VisionMatch] Failed/rate-limited: {e}. Continuing waterfall.")
        return None, False

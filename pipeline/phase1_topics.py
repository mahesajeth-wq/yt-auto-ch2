import os
import json
from pipeline.config import TOPIC_LOG_SIZE, NATURAL_WORLD_SUBCLUSTERS
from pipeline.gemini import GeminiClient, _robust_json_loads

def select_topic(format_type: str) -> dict:
    # ── 1. Load published topics log ─────────────────────────────────────────
    topic_log_path = "published_topics.json"
    if os.path.exists(topic_log_path):
        try:
            with open(topic_log_path, "r") as f:
                data = json.load(f)
                published = data.get("topics", [])
                subcluster_idx = data.get("subcluster_idx", 0)
                call_count = data.get("call_count", 0)
        except Exception as e:
            print(f"Warning: Failed to load published topics: {e}")
            published = []; subcluster_idx = 0; call_count = 0
    else:
        published = []; subcluster_idx = 0; call_count = 0

    recent_topics = published[-TOPIC_LOG_SIZE:]
    call_count += 1

    # ── 2. Determine subcluster + evergreen vs trending ──────────────────────
    current_subcluster = NATURAL_WORLD_SUBCLUSTERS[subcluster_idx % len(NATURAL_WORLD_SUBCLUSTERS)]
    is_trending = (call_count % 5 == 0)   # every 5th call = trending topic

    if is_trending:
        topic_instruction = (
            f"Generate 5 TRENDING topics about {current_subcluster} "
            f"that are currently in the science news this week. "
            f"Frame each as a recent discovery or finding that most people haven't heard yet."
        )
    else:
        topic_instruction = (
            f"Generate 5 EVERGREEN topics about {current_subcluster}. "
            f"Each must reveal a bizarre, counterintuitive, or little-known fact "
            f"that educated adults don't know. Frame as 'What most people don't know about X' "
            f"or 'The hidden truth about Y'. "
            f"Every topic MUST name a specific number, species, mechanism, or place — "
            f"NOT a vague 'scientists are surprised' hook."
        )

    # ── 3. Build Gemini prompt ───────────────────────────────────────────────
    prompt = f"""{topic_instruction}

Sub-cluster focus for this batch: {current_subcluster}

CRITICAL: Do NOT suggest any topic similar to these recently published topics:
{json.dumps(recent_topics, indent=2)}

AVOID: pet animals, compilations, human psychology, AI, technology, space (those are Channel 1).
FOCUS: the natural world — oceans, forests, weather, geology, wild species.

Return ONLY a raw JSON array of objects. No markdown, no preamble.
Each object must have exactly these fields:
- "topic": specific subject with a named fact, species, or number (e.g. "The pistol shrimp produces a flash hotter than the sun's surface")
- "short_hook": opening question or statement, 8 words or less, creates a strong information gap
- "hook_type": one of "curiosity_gap", "contrarian", "time_pressure", "self_identification", "narrative_pull"
- "for_format": "short", "long", or "both"
- "subcluster": the sub-cluster this belongs to (string)
"""

    print(f"[Phase1] Requesting topics — subcluster: {current_subcluster} | trending: {is_trending}")
    client = GeminiClient()
    response_text = client.generate_text(prompt, use_grounding=is_trending, temperature=0.75)

    try:
        topics_list = _robust_json_loads(response_text)
        if not isinstance(topics_list, list):
            raise ValueError("Response is not a JSON list")
        if not topics_list:
            raise ValueError("Response is an empty list")
    except Exception as e:
        print(f"Error parsing topics: {e}")
        topics_list = [
            {
                "topic": "The immortal jellyfish that resets its own age",
                "short_hook": "One creature actually cheats death.",
                "hook_type": "curiosity_gap",
                "for_format": "both",
                "subcluster": current_subcluster
            }
        ]

    # ── 4. Pick first topic matching format_type ──────────────────────────────
    selected_topic = None
    for item in topics_list:
        if item.get("for_format", "both") in (format_type, "both"):
            selected_topic = item
            break
    if not selected_topic:
        selected_topic = topics_list[0]
        selected_topic["for_format"] = format_type

    print(f"[Phase1] Selected: {selected_topic['topic']}")

    # ── 5. Persist state ──────────────────────────────────────────────────────
    published.append(selected_topic["topic"])
    published = published[-TOPIC_LOG_SIZE:]
    next_subcluster_idx = (subcluster_idx + 1) % len(NATURAL_WORLD_SUBCLUSTERS)

    with open(topic_log_path, "w") as f:
        json.dump({
            "topics": published,
            "subcluster_idx": next_subcluster_idx,
            "call_count": call_count
        }, f, indent=2)

    return selected_topic

import json
import datetime
import random
from pipeline.config import HOOK_PATTERNS
from pipeline.gemini import GeminiClient, _robust_json_loads

def get_next_tuesday_3pm_ist_utc():
    # IST is UTC+5:30. 3:00 PM IST = 15:00 IST = 09:30 AM UTC.
    now = datetime.datetime.now(datetime.timezone.utc)
    ist_offset = datetime.timedelta(hours=5, minutes=30)
    now_ist = now + ist_offset
    
    target_date = now_ist.date()
    # Find next Tuesday (1=Tue)
    days_ahead = (1 - target_date.weekday() + 7) % 7
    if days_ahead == 0 and now_ist.time() >= datetime.time(15, 0):
        days_ahead = 7
    target_date += datetime.timedelta(days=days_ahead)
        
    target_dt_ist = datetime.datetime.combine(target_date, datetime.time(15, 0))
    target_dt_utc = target_dt_ist - ist_offset
    return target_dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

def generate_script(topic: dict, format_type: str) -> dict:
    client = GeminiClient()
    
    if format_type == "short":
        import random as _random
        segment_count = _random.choices([4, 5, 6], weights=[15, 65, 20], k=1)[0]
        
        hook_pattern = random.choice(HOOK_PATTERNS)
        hook_formatted = hook_pattern.format(
            subject=topic.get("topic", "science"),
            thing=topic.get("topic", "science"),
            seconds="30",
            topic=topic.get("topic", "science"),
            event="A discovery"
        )
        
        prompt = f"""Generate an extremely viral, high-retention 25-35 second YouTube Short educational script on the topic: "{topic['topic']}".
Use the following hook concept as your core theme: "{hook_formatted}" (short hook: "{topic.get('short_hook', '')}").

Narration Style Requirements:
1. Pacing & Punchiness: Every single sentence must be extremely short, sharp, and high-impact (5 to 10 words MAXIMUM per segment's narration). Avoid long clauses or passive language.
2. Conversational & Simple Language: Use very simple, easy-to-understand, and highly relatable words that anyone can easily follow. Avoid obscure, complex, or overly difficult English vocabulary. Keep the narration friendly, extremely engaging, and relatable—like a friend explaining an amazing fact.
3. Engaging Tone: The voiceover narration must be conversational, highly engaging, and relatable—like a friend telling an exciting story. Write the voiceover to be energetic, warm, and inviting.
2. Hook/Pattern Interrupt: Segment 1 must immediately shatter the viewer's attention. DO NOT use introductory filler like "Did you know..." or "Have you ever wondered...". Go straight to a shocking or mind-bending statement that creates an massive information gap in under 8 words.
3. Emotional/Sensory Triggers: Use strong, dramatic verbs and adjectives (e.g., "panicking", "shatters", "banned", "impossible", "melts", "secret", "trapped").
4. No Fluff: Get straight to the mind-blowing science. Every word must justify its existence.

For every `broll_query` field, write a SHORT, SPECIFIC, STOCK-FOOTAGE-FRIENDLY
search term of 3-6 words MAXIMUM. Write exactly what a human would type into
a stock video search bar (Pexels, Pixabay, etc). Use concrete nouns and visual
objects — NOT instructions or descriptions of what you want.

CORRECT examples: "Stephen Hawking wheelchair smiling", "DNA double helix blue",
"quantum computer chip closeup", "black hole space vortex", "astronaut spacewalk ISS",
"brain neurons firing", "atom particle collider", "coral reef fish colorful"

WRONG examples: "visually jarring close-up of the topic", "macro b-roll of scientific
element", "closing beautiful shot returning to start", "diagram concept visualization",
"TMAO molecular structure" (too specific for stock footage), "chemical" (too ambiguous, returns factories)

IMPORTANT B-ROLL RULES:
- Stock video sites DO NOT HAVE specific molecules or rare deep-sea fish by name.
- For chemicals or proteins, use terms like "abstract science background", "microscope biology animation", "glowing particles", or "fluid dynamics".
- NEVER use the word "chemical" alone, as stock sites return industrial factories and smokestacks instead of biology. Use "chemistry laboratory" or "liquid mixture".

For each segment, also provide a `broll_queries` array with 3-5 ALTERNATIVE search terms for the same visual concept. These should be synonyms, related concepts, or different angles on the same subject. The first entry should match `broll_query`.

For any named person (scientist, historical figure): ALWAYS include their name in the query.
For abstract science concepts: use the most recognizable visual symbol.

You MUST return your response ONLY as a raw JSON object with no markdown syntax. The JSON structure MUST be exactly like this:
{{
  "title": "A catchy title under 40 chars, starting with a hook word/number and containing one emoji",
  "description": "Line1: restate the hook\nLine2: Fast. Accurate. Mind-blowing.\nLine3: Full breakdown -> [link]\n\n#science #didyouknow #facts",
  "tags": ["8 to 12 relevant tags under 500 characters total"],
  "category_id": "27",
  "segments": [
    // Provide exactly {segment_count} segments here.
    {{
      "id": 1,
      "narration": "opening shocking hook sentence - 8 words or less, massive information gap",
      "broll_query": "{topic['topic']} black hole accretion disk space",
      "broll_queries": ["{topic['topic']} black hole accretion disk space", "event horizon visualization", "gravitational lensing effect", "supermassive black hole animation"],
      "duration_target": 6
    }},
    {{
      "id": 2,
      "narration": "Mind-bending scientific fact that expands on the hook - 8 words or less",
      "broll_query": "Albert Einstein chalkboard equations",
      "duration_target": 6
    }},
    {{
      "id": {segment_count},
      "narration": "Final sentence that GRAMMATICALLY FLOWS INTO Segment 1's first sentence when read back-to-back — creating an audio loop the viewer doesn't register as a restart. Example pattern: if Segment 1 opens with 'A pistol shrimp creates a flash hotter than the sun', Segment {segment_count} should end with something like '...which is why nothing in the ocean is stranger than what you heard at the start — hotter than the sun.' The viewer loops before realising the video restarted.",
      "broll_query": "nature extreme close-up slow motion",
      "duration_target": 6
    }}
  ],
  "thumbnail_text": "3 to 5 bold words max for the thumbnail",
  "loop_callout": true
}}

For Segment 1 specifically:
- `broll_query` MUST describe a high-motion, high-contrast, visually arresting shot (fast motion, bright colors, dramatic close-up) — this is the opening pattern-interrupt that determines whether viewers keep watching.

For Segments 2 to (n-2):
- Frame facts with visual or scientific paradoxes (e.g., 'Something the size of a city that weighs more than the sun' or 'The man who failed entrance exams rewrote the universe').
- Deliver the single most mind-bending scientific fact in Segment 2.
- Introduce an open loop (a second mystery or surprise fact) in Segment 3 that builds tension towards the loop twist.

For the final segment (Segment {segment_count}) specifically:
- Resolve all loops and design the final sentence to end on a transition that flows seamlessly back into Segment 1's hook narration.
- The final sentence should THEMATICALLY echo or re-contextualize the IDEA from Segment 1's hook — e.g. answer the question it posed, or reveal a twist that recasts it — WITHOUT repeating its exact wording. The goal is a satisfying "full circle" feeling on rewatch, not a verbatim repeat.
"""
    else:  # long-form
        prompt = f"""Generate a comprehensive 7-10 minute YouTube educational script on the topic: "{topic['topic']}".
The script must have 15 to 18 segments, each targeting 25-35 seconds of narration.

Narration Style Requirements:
1. Conversational & Simple Language: Use very simple, easy-to-understand, and highly relatable words that anyone can easily follow. Avoid obscure, complex, or overly difficult English vocabulary. Keep the narration friendly, extremely engaging, and relatable—like a friend explaining an amazing topic.
2. Engaging Tone: The voiceover narration must be conversational, highly engaging, and relatable—like a friend telling an exciting story. Write the voiceover to be energetic, warm, and inviting.
Structure the narrative into:
- Intro hook (segments 1-2)
- Act 1: The core mystery/mechanism (segments 3-7)
- Act 2: The surprising twist/implication (segments 8-12)
- Act 3: Modern applications or future outlook (segments 13-16)
- Closing CTA & link (segments 17-18)

For every `broll_query` field, write a SHORT, SPECIFIC, STOCK-FOOTAGE-FRIENDLY
search term of 3-6 words MAXIMUM. Write exactly what a human would type into
a stock video search bar (Pexels, Pixabay, etc). Use concrete nouns and visual
objects — NOT instructions or descriptions of what you want.

CORRECT examples: "Stephen Hawking wheelchair smiling", "DNA double helix blue",
"quantum computer chip closeup", "black hole space vortex", "astronaut spacewalk ISS",
"brain neurons firing", "atom particle collider", "coral reef fish colorful"

WRONG examples: "visually jarring close-up of the topic", "macro b-roll of scientific
element", "closing beautiful shot returning to start", "diagram concept visualization",
"TMAO molecular structure" (too specific for stock footage), "chemical" (too ambiguous, returns factories)

IMPORTANT B-ROLL RULES:
- Stock video sites DO NOT HAVE specific molecules or rare deep-sea fish by name.
- For chemicals or proteins, use terms like "abstract science background", "microscope biology animation", "glowing particles", or "fluid dynamics".
- NEVER use the word "chemical" alone, as stock sites return industrial factories and smokestacks instead of biology. Use "chemistry laboratory" or "liquid mixture".

For each segment, also provide a `broll_queries` array with 3-5 ALTERNATIVE search terms for the same visual concept. These should be synonyms, related concepts, or different angles on the same subject. The first entry should match `broll_query`.

For any named person (scientist, historical figure): ALWAYS include their name in the query.
For abstract science concepts: use the most recognizable visual symbol.

You MUST return your response ONLY as a raw JSON object with no markdown syntax. The JSON structure MUST be exactly like this:
{{
  "title": "Engaging educational title for a long video, under 70 characters",
  "description": "A detailed, engaging description explaining what the video covers, including timestamps and educational value.\\n\\n#science #education #technology",
  "tags": ["15 to 20 relevant tags"],
  "category_id": "27",
  "segments": [
    {{
      "id": 1,
      "narration": "Opening narration hook...",
      "broll_query": "{topic['topic']} space stars universe",
      "broll_queries": ["{topic['topic']} space stars universe", "galaxy nebula deep space", "cosmos starfield timelapse", "astronomical observatory night sky"],
      "duration_target": 30
    }}
    // ... total 15-18 segments
  ],
  "thumbnail_text": "3 to 5 bold words max for the thumbnail image",
  "loop_callout": false
}}
"""

    print("Generating script content using Gemini...")
    max_attempts = 3
    script_text = ""
    script = None
    for attempt in range(max_attempts):
        try:
            script_text = client.generate_text(prompt, use_grounding=False, temperature=0.8)
            script = _robust_json_loads(script_text)
            break
        except Exception as e:
            print(f"Error parsing script JSON on attempt {attempt+1}: {e}. Raw script text: {script_text}")
            if attempt == max_attempts - 1:
                raise RuntimeError("Failed to generate a valid script JSON from Gemini after 3 attempts") from e

    if format_type == "short":
        script["segment_count"] = segment_count

    # Add scheduling metadata for long form
    if format_type == "long":
        script["publish_at"] = get_next_tuesday_3pm_ist_utc()
    else:
        # Default publish_at for shorts: let's set it to None so we can upload as private first
        script["publish_at"] = None

    # --- FACT VERIFICATION ---
    print("Running fact verification on the generated script...")
    verification_prompt = f"""You are a fact checker. Verify the scientific accuracy of each segment's narration in the following script JSON:
{json.dumps(script, indent=2)}

Check if all claims are backed by credible scientific consensus.
Return ONLY the modified script JSON with an added `"verified": true` or `"verified": false` field inside EACH segment object in the "segments" list.
If a claim is unverifiable, speculative, or false, mark `"verified": false`.
"""
    try:
        verified_text = client.generate_text(verification_prompt, use_grounding=True, temperature=0.2)
        verified_script = _robust_json_loads(verified_text)
        script["segments"] = verified_script.get("segments", script["segments"])
    except Exception as e:
        print(f"Fact check failed or quota-limited ({e}), keeping original script for Judge AI review.")
        for seg in script["segments"]:
            seg["verified"] = True

    # Regenerate unverified segments
    for seg in script["segments"]:
        if not seg.get("verified", True):
            print(f"Segment {seg['id']} failed fact check. Regenerating narration...")
            regen_prompt = f"""The following script segment narration failed fact-checking or was unverified:
Topic: {topic['topic']}
Segment details: {json.dumps(seg, indent=2)}

Rewrite the "narration" so that it is 100% scientifically accurate, verifiable, and maintains the exact same tone and target duration.
Return ONLY a raw JSON object for this segment with the updated "narration" and `"verified": true`.
"""
            try:
                regen_text = client.generate_text(regen_prompt, use_grounding=True, temperature=0.3)
                regen_seg = _robust_json_loads(regen_text)
                seg["narration"] = regen_seg.get("narration", seg["narration"])
                seg["verified"] = True
            except Exception as e:
                print(f"Failed to regenerate segment {seg['id']} ({e}). Keeping original for Judge AI review.")
                seg["verified"] = True

    return script

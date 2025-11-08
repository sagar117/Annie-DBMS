import os
import openai
from typing import Dict, Any
openai.api_key = os.getenv("OPENAI_API_KEY")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

EXTRACTION_PROMPT = """
You are given a medical call transcript between an AI nurse and a patient. Produce:
1) A concise call summary (1-3 sentences).
2) A JSON object "readings" listing any measurements found (BP, pulse, glucose, weight).
   - For BP provide systolic and diastolic (integers) and units "mmHg".
   - For pulse provide value (integer) and units "bpm".
   - For glucose provide value and units (mg/dL or mmol/L if mentioned).
   - For weight provide value (float) and units (kg/lb).
3) A JSON object "questionnaire" with any questions asked and the patient's responses/ratings.
   - Format: array of objects with "question" and "response" fields
   - For numeric ratings, include "rating" field with the number
   - Example: {"question": "How would you rate your pain?", "response": "moderate", "rating": 5}
If a reading has a timestamp in the transcript, include recorded_at (ISO 8601). If not, omit recorded_at.
Return only valid JSON with keys: summary (string), readings (array), and questionnaire (array).
Transcript:
---
{transcript}
---
"""

def extract_readings_from_transcript(transcript: str) -> Dict[str, Any]:
    prompt = EXTRACTION_PROMPT.replace("{transcript}", transcript)
    try:
        resp = openai.ChatCompletion.create(
            model=MODEL,
            messages=[{"role":"user", "content": prompt}],
            temperature=0.0,
            max_tokens=800,
        )
        text = resp.choices[0].message.content.strip()
        import json, re
        m = re.search(r"\{.*\}\s*$", text, flags=re.DOTALL)
        json_text = m.group(0) if m else text
        data = json.loads(json_text)
        return data
    except Exception as e:
        return {"summary": "", "readings": [] , "error": str(e)}

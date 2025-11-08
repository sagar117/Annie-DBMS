import os
import openai
from typing import Dict, Any
openai.api_key = os.getenv("OPENAI_API_KEY")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

EXTRACTION_PROMPT = """
You are given a medical call transcript between an AI nurse and a patient. Produce:
1) A concise call summary (1-3 sentences).
2) A JSON object "readings" array containing readings in this format:
   - For BP: {"BP": {"systolic": 120, "diastolic": 80, "units": "mmHg"}}
   - For others: {"type": "pulse", "value": 80, "units": "bpm"}
   Supported types:
   - BP (special format above)
   - pulse (value as integer, units "bpm")
   - glucose (value as number, units "mg/dL" or "mmol/L")
   - weight (value as float, units "kg" or "lb")
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
    if not transcript or not transcript.strip():
        return {"summary": "", "readings": [], "questionnaire": []}
        
    prompt = EXTRACTION_PROMPT.replace("{transcript}", transcript)
    try:
        resp = openai.ChatCompletion.create(
            model=MODEL,
            messages=[{"role":"user", "content": prompt}],
            temperature=0.0,
            max_tokens=800,
        )
        text = resp.choices[0].message.content.strip()
        if not text:
            return {"summary": "", "readings": [], "questionnaire": []}
            
        import json, re
        m = re.search(r"\{.*\}\s*$", text, flags=re.DOTALL)
        json_text = m.group(0) if m else text
        data = json.loads(json_text)
        
        # Ensure the response has the expected structure
        if not isinstance(data, dict):
            return {"summary": "", "readings": [], "questionnaire": []}
            
        # Ensure readings and questionnaire are lists
        if "readings" not in data or not isinstance(data["readings"], list):
            data["readings"] = []
        if "questionnaire" not in data or not isinstance(data["questionnaire"], list):
            data["questionnaire"] = []
            
        return data
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error("Failed to extract readings: %s", e)
        return {"summary": "", "readings": [], "questionnaire": []}

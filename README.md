Annie Backend (refactor) - Full Complete Zip

Files included:
- app/: FastAPI app with APIs and services
- prompts/: prompt files
- requirements.txt

Quick run:
1. set env OPENAI_API_KEY and DEEPGRAM_API_KEY and TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM_NUMBER
2. pip install -r requirements.txt
3. uvicorn app.main:app --reload --host 0.0.0.0 --port 5000

Note: If you already have an existing annie.db, run:
  sqlite3 ./annie.db "ALTER TABLE calls ADD COLUMN twilio_call_sid TEXT;" 

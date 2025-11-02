## Quick context (what this repo is)

- Backend for "Annie" — a FastAPI service that bridges telephony (Twilio/SignalWire) -> Deepgram -> OpenAI to run conversational nurse scripts and persist call transcripts/readings.
- Two main entry points: `uvicorn app.main:app` (API + ws endpoint) and `python server.py` (a small wrapper exposing a /ws endpoint).

## High-level architecture & dataflow

- HTTP API: `app.main` wires routers in `app/api/*` (calls, patients, auth, etc.). See `app/main.py` for router inclusion and CORS.
- WebSocket bridge: `app.services.deepgram_handler.bridge_ws` is the core glue handling WebSocket audio from Twilio -> Deepgram -> back to Twilio. The bridge is invoked from both `app.main` and `server.py` WebSocket endpoints.
- DB: SQLAlchemy models in `app/models.py`, engine/session setup in `app/db.py`. Typical pattern: either use the `get_db()` dependency or manually create `db.SessionLocal()` and close it.
- Prompts: per-agent prompt files live in `prompts/` (files like `annie_RPM.txt`). `prompt_file_for_agent` resolves agent -> prompt file.
- OpenAI usage: `app/services/openai_client.py` contains transcript-to-readings extraction logic used by `app/api/calls.py` when completing calls.

## Project-specific conventions & important patterns

- DB-first agent resolution: The bridge resolves `agent` primarily by looking up the `Call` row in the DB (see `app/services/deepgram_handler.py`). Querystring `agent` is a fallback only when DB has none.
- Prompt selection: Agent names are sanitized then matched to `prompts/{agent}.txt`. If absent, fallback to `prompts/annie_RPM.txt` (see `prompt_file_for_agent`).
- Personalized greeting: Controlled by env `PERSONALIZED_GREETING` (enabled by default). When enabled, the bridge loads patient/org context and prepends a dynamic context block to the prompt.
- Lazy imports to avoid circulars: Many modules do lazy imports (e.g., `from app.services import openai_client` inside functions, and `bridge_ws` is imported lazily). When editing, preserve this pattern or relocate imports carefully.
- Session usage: Some routers use a `get_db()` dependency while others create `db.SessionLocal()` directly and manually close — follow the local pattern when editing a file.

## Key integration points (external services)

- Deepgram (agent websocket) — used in `app/services/deepgram_handler.py` (env: `DEEPGRAM_API_KEY`).
- Twilio / SignalWire — telephony outbound logic & TwiML endpoints in `app/api/calls.py` (env: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `SW_PROJECT_ID`, `SW_API_TOKEN`, `SIGNALWIRE_SPACE`, etc.).
- OpenAI — transcript extraction in `app/services/openai_client.py` (env: `OPENAI_API_KEY`, `OPENAI_MODEL`).

## How to run / developer workflows

- Install: `pip install -r requirements.txt` (project uses FastAPI + SQLAlchemy + OpenAI + Deepgram + uvicorn).
- Run API (recommended for development):
  - `uvicorn app.main:app --reload --host 0.0.0.0 --port 5000`
  - WebSocket clients may connect to `/ws` (the code preserves raw path/query to keep call_id percent-encoding).
- Alternate simple runner for the bridge: `python server.py` (exposes `/ws` and delegates to same bridge implementation).
- Environment: Provide `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, and telephony creds for Twilio/SignalWire. `PUBLIC_HOST` is used to construct stream URLs for outbound calls.

## Common developer tasks & gotchas

- If you add new columns to `app/models.py` and use SQLite, update existing `annie.db` or create migration steps. There is an inline note in `README.md` about adding `twilio_call_sid` via a raw `sqlite3` ALTER TABLE.
- Keep prompt file names simple (alphanumeric, `-`, `_`). `prompt_file_for_agent` sanitizes names and checks `prompts/` for a matching file.
- Audio flow: Twilio sends base64 media frames with event types (`start`, `media`, `stop`) — `bridge_ws` transforms those into Deepgram frames and persists transcript fragments frequently.
- Persisting transcripts/readings: `persist_transcript_fragment`, `_persist_single_readings` and call-completion logic are in `deepgram_handler.py` and `app/api/calls.py`. If you change transcript formatting, update parsing in `openai_client.py` accordingly.

## Files to read first (quick checklist for a new contributor)

- `app/services/deepgram_handler.py` — central call handling + prompt personalization.
- `app/api/calls.py` — telephony routing, TwiML endpoints, call lifecycle and persistence.
- `app/services/openai_client.py` — transcript -> readings extraction logic.
- `app/models.py`, `app/db.py`, `app/schemas.py` — DB model shapes and API schemas.
- `prompts/` — agent scripts; modify or add new agent .txt files here.

## Example snippets to reference

- Outbound call creation (API): `POST /api/calls/outbound` — see `app/api/calls.py` (constructs TwiML `Stream url="wss://<host>/ws/{call_id}?agent=..."`).
- WebSocket bridge: incoming Twilio stream -> `bridge_ws(ws, path_arg)` (preserves raw percent-encoded path so call_id parsing is robust).

If anything above is unclear or you want me to include extra examples (e.g., typical env file, sample curl requests for APIs, or suggested small tests for `openai_client.extract_readings_from_transcript`), tell me which parts to expand and I'll iterate.

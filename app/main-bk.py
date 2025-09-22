from fastapi import FastAPI, WebSocket
from app.api import calls, patients, orgs
from app.db import init_db
from app.services.deepgram_handler import bridge_ws

app = FastAPI(title="Annie Backend (refactor)")

# initialize DB tables
init_db()

app.include_router(calls.router)
app.include_router(patients.router)
app.include_router(orgs.router)

# Mount websocket endpoint that uses the Deepgram bridge.
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    # Pass query string (decoded) to bridge_ws for agent/call_id extraction
    path = str(ws.scope.get("query_string", b"").decode())
    await bridge_ws(ws, path_arg=path)

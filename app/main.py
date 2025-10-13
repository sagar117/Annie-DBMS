# app/main.py
import logging
import json
import traceback
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("annie.main")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Annie Backend")

# CORS (adjust allowed_origins for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Include API routers (defensive import + logging) ---
print("[main] starting router include check...")
try:
    from app.api import calls, patients, orgs, analytics  # type: ignore
    print("[main] imported app.api modules OK")
except Exception as e:
    print("[main] failed importing app.api:", repr(e))
    traceback.print_exc()
    calls = patients = orgs = None  # type: ignore

for name, mod in (("calls", calls), ("patients", patients), ("orgs", orgs), ("analytics", analytics),):
    if mod is None:
        print(f"[main] router module {name} is None, skipping include")
    else:
        try:
            app.include_router(mod.router)
            print(f"[main] included router: /api/{name}")
        except Exception as e:
            print(f"[main] include_router failed for {name}: {repr(e)}")
            traceback.print_exc()

# Simple health-check
@app.get("/health")
def health():
    return {"status": "ok"}


# Robust websocket endpoint:
# Accept both /ws and /ws/<anything...> and preserve raw_path (percent-encoded parts).
@app.websocket("/ws")
@app.websocket("/ws/{tail:path}")
async def websocket_endpoint(websocket: WebSocket, tail: str = ""):
    """
    WebSocket endpoint that preserves the full raw ASGI path + querystring, then
    delegates to bridge_ws(ws, path_arg).
    This ensures percent-encoded segments (like call_id%3D70) are preserved.
    """
    await websocket.accept()

    # Build raw path_arg from ASGI scope to preserve percent-encoding + querystring
    try:
        raw_path = websocket.scope.get("raw_path")
        if isinstance(raw_path, (bytes, bytearray)):
            raw_path = raw_path.decode("utf-8", errors="ignore")
        if not raw_path:
            raw_path = websocket.scope.get("path", "/ws")
        raw_qs = websocket.scope.get("query_string", b"")
        if raw_qs:
            try:
                qs = raw_qs.decode("utf-8", errors="ignore")
                path_arg = raw_path + "?" + qs
            except Exception:
                path_arg = raw_path
        else:
            path_arg = raw_path
    except Exception:
        path_arg = "/ws"

    logger.info("WebSocket connected: path_arg=%s", path_arg)

    # Lazy import of the bridge to avoid circular imports at module load time
    try:
        from app.services.deepgram_handler import bridge_ws  # type: ignore
    except Exception as e:
        logger.exception("bridge_ws import failed: %s", e)
        bridge_ws = None  # type: ignore

    if bridge_ws is None:
        try:
            # send a small JSON error message to client before closing
            await websocket.send_text(json.dumps({"error": "bridge_ws_unavailable"}))
        except Exception:
            pass
        await websocket.close()
        return

    try:
        await bridge_ws(websocket, path_arg)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected (client)")
        try:
            await websocket.close()
        except Exception:
            pass
    except Exception as e:
        logger.exception("Error in bridge_ws: %s", e)
        try:
            await websocket.close()
        except Exception:
            pass

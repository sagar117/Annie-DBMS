#!/usr/bin/env python3
"""
server.py - FastAPI wrapper that exposes a WebSocket /ws endpoint
and delegates WebSocket handling to app.services.deepgram_handler.bridge_ws.

Run: python server.py
or: uvicorn server:app --host 0.0.0.0 --port 8000
"""

import os
import sys
import asyncio
import signal
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import PlainTextResponse
import uvicorn

# ensure project root on sys.path so imports like "from app.services..." work
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Import the bridge function that expects a Starlette/FastAPI WebSocket-like object.
try:
    from app.services.deepgram_handler import bridge_ws
except Exception as e:
    print("Failed importing bridge_ws from app.services.deepgram_handler:", e)
    raise

app = FastAPI(title="Annie Backend - WebSocket bridge")

# Simple health
@app.get("/health")
async def health():
    return PlainTextResponse("ok")

# Expose the websocket endpoint used by Twilio Start <Stream url="wss://.../ws?agent=...&call_id=..."/>
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Accept incoming WebSocket from Twilio (Stream) and hand over to bridge_ws.
    The bridge_ws function should accept the FastAPI WebSocket and the raw query string.
    """
    # Accept the socket
    await websocket.accept()
    # Build path_arg similar to previous code: include raw query string if present
    # request_url provides path and query; WebSocket has .url which is an object in Starlette
    try:
        # FastAPI WebSocket has "url" attribute (starlette.datastructures.URL)
        q = str(websocket.url.query)
        path_arg = f"/ws?{q}" if q else "/ws"
    except Exception:
        # fallback: try to reconstruct from headers
        try:
            qs = websocket.headers.get("sec-websocket-protocol") or ""
            path_arg = f"/ws?{qs}"
        except Exception:
            path_arg = "/ws"

    # Call the bridge; bridge_ws is async and handles lifecycle
    try:
        await bridge_ws(websocket, path_arg)
    except WebSocketDisconnect:
        # normal client disconnect
        try:
            await websocket.close()
        except Exception:
            pass
    except Exception as e:
        # Log error and ensure socket closed
        print("[server.websocket_endpoint] bridge_ws error:", repr(e))
        try:
            await websocket.close()
        except Exception:
            pass

# Optional: an endpoint that echoes query for debugging (useful for Twilio to fetch TwiML host)
@app.get("/debug/echo")
async def debug_echo(request: Request):
    return {
        "client": str(request.client),
        "url": str(request.url),
        "headers": dict(request.headers),
    }

# Run app via `python server.py`
def _run():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("UVICORN_PORT", "8000")))
    # use reload only when executed manually (not in production)
    uvicorn.run("server:app", host=host, port=port, reload=False, log_level="info")

if __name__ == "__main__":
    _run()

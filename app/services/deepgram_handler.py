# app/services/deepgram_handler.py
import asyncio
import base64
import json
import os
from urllib.parse import urlparse, parse_qs
import websockets
from websockets import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed
from websockets.typing import Data
from websockets import InvalidHandshake

# starlette WebSocket is passed in from FastAPI websocket endpoint
from websockets import ConnectionClosedOK as _wscc  # unused but for readability

# Note: The starlette WebSocket object is used as `ws` in the bridge function.
# It exposes await ws.receive(), await ws.send_text(...), await ws.send_bytes(...), await ws.close().

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
DEFAULT_PROMPT_FILE = os.path.join(PROMPTS_DIR, "annie_RPM.txt")
DEEPGRAM_WS = "wss://agent.deepgram.com/v1/agent/converse"


def _extract_agent_from_path(path: str):
    if not path:
        return None
    try:
        # path may be a query string only (e.g., "agent=annie_RPM&call_id=123")
        if "?" not in path and "=" in path:
            qs = parse_qs(path)
        else:
            parsed = urlparse(path)
            qs = parse_qs(parsed.query)
        vals = qs.get("agent") or qs.get("Agent") or qs.get("agent_name")
        if vals:
            return vals[0]
    except Exception:
        return None
    return None


def prompt_file_for_agent(agent_name: str):
    if not agent_name:
        return DEFAULT_PROMPT_FILE
    safe = "".join(ch for ch in agent_name if (ch.isalnum() or ch in ("-", "_")))
    candidate = os.path.join(PROMPTS_DIR, f"{safe}.txt")
    if os.path.isfile(candidate):
        return candidate
    return DEFAULT_PROMPT_FILE


def load_prompt(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def sts_connect():
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY environment variable is not set")
    # Deepgram expects subprotocol token + API key (per prior implementation)
    return websockets.connect(DEEPGRAM_WS, subprotocols=["token", api_key])


async def bridge_ws(ws, path_arg: str = None):
    """
    Bridge between Twilio's WebSocket (FastAPI/Starlette WebSocket) and Deepgram's agent websocket.

    `ws` is a Starlette WebSocket object (not the 'websockets' WebSocket). Use await ws.receive().
    `path_arg` should be the query string (e.g., "agent=annie_RPM&call_id=123") passed from the FastAPI websocket endpoint.
    """
    agent = _extract_agent_from_path(path_arg)
    prompt_path = prompt_file_for_agent(agent)
    prompt_text = load_prompt(prompt_path)
    print(f"[deepgram bridge] agent {agent} prompt {prompt_path}")

    audio_queue = asyncio.Queue()      # bytes -> to send to Deepgram (binary frames)
    streamsid_queue = asyncio.Queue()  # will hold the Twilio streamSid (string)
    should_hangup = asyncio.Event()

    # helper to persist brief debug events into calls.summary (best-effort)
    def persist_debug_event(event_summary: dict):
        try:
            from app import db, models
            qs = parse_qs(path_arg or "")
            vals = qs.get("call_id") or qs.get("CallId") or qs.get("call")
            if not vals:
                return
            try:
                call_id = int(vals[0])
            except Exception:
                return
            session = db.SessionLocal()
            try:
                call_row = session.query(models.Call).filter(models.Call.id == call_id).first()
                if call_row:
                    short = json.dumps(event_summary)[:3000]
                    call_row.summary = (call_row.summary or "") + "\n[DBG_EVENT] " + short
                    session.add(call_row)
                    session.commit()
            finally:
                session.close()
        except Exception as err:
            # never raise from instrumentation
            print("[deepgram instrumentation] persist failed:", repr(err))

    # Connect to Deepgram agent websocket
    try:
        async with sts_connect() as sts_ws:  # sts_ws is a websockets.client.WebSocketClientProtocol
            # send Settings
            config_message = {
                "type": "Settings",
                "audio": {
                    "input": {"encoding": "mulaw", "sample_rate": 8000},
                    "output": {"encoding": "mulaw", "sample_rate": 8000, "container": "none"},
                },
                "agent": {
                    "language": "en",
                    "listen": {"provider": {"type": "deepgram", "model": "nova-3"}},
                    "think": {
                        "provider": {"type": "open_ai", "model": "gpt-4o-mini", "temperature": 0.3},
                        "prompt": prompt_text or "You are a helpful AI nurse helping patient answer their query",
                    },
                    "speak": {"provider": {"type": "deepgram", "model": "aura-2-thalia-en"}},
                    "greeting": "Hello",
                },
            }
            await sts_ws.send(json.dumps(config_message))
            print("[bridge_ws] sent Deepgram Settings")

            # sts_sender: send binary audio chunks (bytes) from audio_queue -> sts_ws (Deepgram)
            async def sts_sender():
                try:
                    while True:
                        chunk = await audio_queue.get()
                        if chunk is None:
                            # end of stream
                            try:
                                await sts_ws.send(b"")  # some servers expect a sentinel; ignore errors
                            except Exception:
                                pass
                            break
                        # Deepgram expects raw audio frames as binary frames
                        try:
                            await sts_ws.send(chunk)
                        except ConnectionClosed:
                            break
                except Exception as e:
                    print("[sts_sender] unexpected:", repr(e))

            # sts_receiver: listen to Deepgram events (text + binary), forward binary audio to Twilio (via Starlette ws)
            async def sts_receiver():
                try:
                    # Wait for the streamSid to be available (provided by Twilio start event)
                    streamsid = None
                    try:
                        streamsid = await asyncio.wait_for(streamsid_queue.get(), timeout=10.0)
                    except asyncio.TimeoutError:
                        # if no streamSid arrives in time, we still continue; Twilio may send it later
                        streamsid = None

                    async for message in sts_ws:
                        # websockets lib yields str for text frames, bytes for binary frames
                        if isinstance(message, str):
                            try:
                                decoded = json.loads(message)
                            except Exception:
                                # log raw textual event
                                print("[deepgram event] raw:", message[:2000])
                                decoded = {"raw": message}
                            ev_type = decoded.get("type", "")
                            print(f"[deepgram event] type={ev_type} keys={list(decoded.keys())}")

                            # persist debug event to DB (best-effort)
                            try:
                                persist_debug_event({"type": ev_type, "keys": list(decoded.keys())})
                            except Exception:
                                pass

                            # continue; textual events may include transcripts or status messages
                            continue

                        # binary audio from Deepgram -> forward to Twilio (via Starlette WebSocket)
                        if isinstance(message, (bytes, bytearray)):
                            # For Twilio WebSocket stream, we must send JSON like:
                            # {"event":"media","streamSid":"<sid>","media":{"payload":"<base64>"}}
                            if not streamsid:
                                # try to get streamsid if previously not present
                                try:
                                    streamsid = streamsid_queue.get_nowait()
                                except Exception:
                                    streamsid = None
                            try:
                                payload_b64 = base64.b64encode(message).decode("ascii")
                                media_message = {
                                    "event": "media",
                                    "streamSid": streamsid or "",
                                    "media": {"payload": payload_b64},
                                }
                                # Starlette WebSocket -> use send_text for JSON
                                await ws.send_text(json.dumps(media_message))
                            except Exception as e:
                                print("[sts_receiver] forward-to-twilio failed:", repr(e))
                                # don't raise — attempt to continue
                                continue
                except Exception as e:
                    print("[sts_receiver] unexpected:", repr(e))

            # twilio_receiver: receive Twilio JSON control frames and inbound audio frames
            async def twilio_receiver():
                BUFFER_SIZE = 5 * 160
                inbuffer = bytearray()
                try:
                    while True:
                        # Starlette/fastapi WebSocket.receive() returns dict
                        msg = await ws.receive()
                        mtype = msg.get("type")
                        if mtype == "websocket.disconnect":
                            # remote closed
                            break

                        text = msg.get("text")
                        data = None
                        if text is not None:
                            try:
                                data = json.loads(text)
                            except Exception:
                                # not JSON — ignore
                                continue
                        else:
                            # There might be bytes frames (rare for Twilio control messages)
                            b = msg.get("bytes")
                            if b:
                                try:
                                    data = json.loads(b.decode("utf-8", errors="ignore"))
                                except Exception:
                                    continue

                        if not data:
                            continue

                        evt = data.get("event")
                        if evt == "start":
                            start = data.get("start", {})
                            streamsid = start.get("streamSid")
                            if streamsid:
                                # publish streamSid for sts_receiver to use
                                streamsid_queue.put_nowait(streamsid)
                        elif evt == "media":
                            media = data.get("media", {})
                            payload_b64 = media.get("payload", "")
                            if not payload_b64:
                                continue
                            chunk = base64.b64decode(payload_b64)
                            # Twilio inbound audio track label is often "inbound"
                            if media.get("track") == "inbound":
                                inbuffer.extend(chunk)
                            # forward fixed-sized chunks to Deepgram
                            while len(inbuffer) >= BUFFER_SIZE:
                                try:
                                    await audio_queue.put(bytes(inbuffer[:BUFFER_SIZE]))
                                except Exception:
                                    # queue may be closed
                                    pass
                                del inbuffer[:BUFFER_SIZE]
                        elif evt == "stop":
                            break

                        if should_hangup.is_set():
                            break
                except Exception as e:
                    print("[twilio_receiver] unexpected:", repr(e))
                finally:
                    # signal sender to finish
                    try:
                        await audio_queue.put(None)
                    except Exception:
                        try:
                            audio_queue.put_nowait(None)
                        except Exception:
                            pass

            # start tasks
            sender_task = asyncio.create_task(sts_sender())
            receiver_task = asyncio.create_task(sts_receiver())
            twilio_task = asyncio.create_task(twilio_receiver())

            # wait until one of tasks finishes
            done, pending = await asyncio.wait(
                [sender_task, receiver_task, twilio_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

    except InvalidHandshake as e:
        print("[bridge_ws] deepgram handshake failed:", repr(e))
        persist_debug_event({"error": "deepgram_handshake", "detail": str(e)})
    except Exception as e:
        print("bridge_ws error:", repr(e))
        persist_debug_event({"error": "bridge_ws_exception", "detail": str(e)})
    finally:
        # ensure the Starlette WebSocket is closed cleanly
        try:
            await ws.close()
        except Exception:
            pass

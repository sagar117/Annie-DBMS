# app/services/deepgram_handler.py
"""
Deepgram <-> Twilio bridge with DB-first agent resolution.

Primary resolution order:
1) Parse call_id from path (handles /ws/NN, /ws/call_id=NN, and percent-encoded forms)
2) Fetch agent from DB using call_id (PRIMARY source of truth)
3) Fallback only to querystring agent if DB did not provide an agent

Then it requests mu-law@8000 from Deepgram and forwards audio both ways (no PCM conversion).
It logs at every decision point to help debug agent resolution issues.
"""

import asyncio
import base64
import json
import os
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime
from typing import Optional

import websockets
from websockets.exceptions import InvalidHandshake, ConnectionClosed

# Project paths
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
DEFAULT_PROMPT_FILE = os.path.join(PROMPTS_DIR, "annie_RPM.txt")

DEEPGRAM_WS = "wss://agent.deepgram.com/v1/agent/converse"


def prompt_file_for_agent(agent_name: Optional[str]) -> str:
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
    except Exception as e:
        print("[load_prompt] failed to load prompt:", e)
        return ""


def sts_connect():
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY environment variable is not set")
    return websockets.connect(DEEPGRAM_WS, subprotocols=["token", api_key])


async def bridge_ws(ws, path_arg: str = None):
    """
    ws: FastAPI WebSocket (has send_text, receive)
    path_arg: raw path + query (e.g. b'/ws/call_id%3D70' on some setups, or '/ws/70?agent=...')
    """
    print(f"[bridge_ws] START: raw path_arg={path_arg!r}")

    call_id = None
    agent = None
    parsed = None
    qs = {}

    # --- Robust parsing for call_id (handles percent-encoding) ---
    try:
        parsed = urlparse(path_arg or "")
        raw_path = parsed.path or ""
        raw_path_decoded = unquote(raw_path)
        print(f"[bridge_ws] raw_path={raw_path!r} decoded_path={raw_path_decoded!r}")

        # parse querystring if present
        try:
            qs = parse_qs(parsed.query)
        except Exception:
            qs = {}
        print(f"[bridge_ws] parsed.query = {qs}")

        # 1) Try querystring call_id
        vals = qs.get("call_id") or qs.get("CallId") or qs.get("call")
        if vals:
            try:
                call_id = int(vals[0])
                print(f"[bridge_ws] call_id parsed from querystring = {call_id}")
            except Exception as e:
                print(f"[bridge_ws] failed parsing call_id from querystring: {vals[0]} -> {e}")

        # 2) If not found, try path segment patterns
        if not call_id:
            parts = raw_path_decoded.strip("/").split("/")
            print(f"[bridge_ws] path parts (decoded) = {parts}")
            if len(parts) >= 2 and parts[0] == "ws":
                candidate = parts[1]
                # pattern: call_id=NN
                if "=" in candidate:
                    k, v = candidate.split("=", 1)
                    if k.lower() in ("call_id", "call", "id") and v.isdigit():
                        try:
                            call_id = int(v)
                            print(f"[bridge_ws] call_id parsed from path segment 'key=value' = {call_id}")
                        except Exception as e:
                            print(f"[bridge_ws] failed parsing numeric v from key=value: {e}")
                else:
                    # numeric segment
                    if candidate.isdigit():
                        try:
                            call_id = int(candidate)
                            print(f"[bridge_ws] call_id parsed from numeric path segment = {call_id}")
                        except Exception as e:
                            print(f"[bridge_ws] failed parsing numeric candidate: {e}")
    except Exception as e:
        print(f"[bridge_ws] error parsing path_arg for call_id: {e}")

    # --- Primary: DB-first agent lookup if call_id available ---
    if call_id is not None:
        try:
            from app import db, models  # lazy import
            session = db.SessionLocal()
            try:
                call_row = session.query(models.Call).filter(models.Call.id == call_id).first()
                if call_row:
                    print(f"[bridge_ws] DB lookup: found call row id={call_id} agent={getattr(call_row, 'agent', None)}")
                    agent = getattr(call_row, "agent", None)
                else:
                    print(f"[bridge_ws] DB lookup: NO call row found for id={call_id}")
            finally:
                session.close()
        except Exception as e:
            print(f"[bridge_ws] DB lookup exception for call_id={call_id}: {e}")
            agent = None
    else:
        print("[bridge_ws] call_id is None; cannot fetch agent from DB as primary source")

    # --- Fallback: check querystring for agent (only if DB didn't produce agent) ---
    if agent is None:
        try:
            agent_vals = qs.get("agent") or qs.get("Agent") or qs.get("agent_name")
            if agent_vals:
                agent = agent_vals[0]
                print(f"[bridge_ws] agent found in querystring fallback = {agent}")
        except Exception:
            pass

    print(f"[bridge_ws] FINAL RESOLVE -> call_id={call_id} agent={agent}")

    # pick prompt file
    prompt_path = prompt_file_for_agent(agent)
    prompt_text = load_prompt(prompt_path)
    print(f"[bridge_ws] using prompt file: {prompt_path} (exists={os.path.isfile(prompt_path)})")

    # --- prepare queues and persistence helpers ---
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()
    should_hangup = asyncio.Event()

    def persist_transcript_fragment(role: str, text: str):
        try:
            if not call_id:
                return
            from app import db, models
            session = db.SessionLocal()
            try:
                call_row = session.query(models.Call).filter(models.Call.id == call_id).first()
                if call_row:
                    call_row.transcript = (call_row.transcript or "") + f"\n[{role}] " + (text or "")
                    session.add(call_row)
                    session.commit()
            finally:
                session.close()
        except Exception as e:
            print(f"[persist_transcript_fragment] failed: {e}")

    def persist_call_start_time():
        try:
            if not call_id:
                return
            from app import db, models
            session = db.SessionLocal()
            try:
                call_row = session.query(models.Call).filter(models.Call.id == call_id).first()
                if call_row and not call_row.start_time:
                    call_row.start_time = datetime.utcnow()
                    call_row.status = "in_progress"
                    session.add(call_row)
                    session.commit()
                    print(f"[persist_call_start_time] set start_time for call_id={call_id}")
            finally:
                session.close()
        except Exception as e:
            print(f"[persist_call_start_time] failed: {e}")

    def persist_call_end_time_and_duration():
        try:
            if not call_id:
                return
            from app import db, models
            session = db.SessionLocal()
            try:
                call_row = session.query(models.Call).filter(models.Call.id == call_id).first()
                if call_row:
                    call_row.end_time = datetime.utcnow()
                    call_row.status = "completed"
                    if call_row.start_time:
                        call_row.duration_seconds = int((call_row.end_time - call_row.start_time).total_seconds())
                    session.add(call_row)
                    session.commit()
                    print(f"[persist_call_end_time_and_duration] set end_time for call_id={call_id}")
            finally:
                session.close()
        except Exception as e:
            print(f"[persist_call_end_time_and_duration] failed: {e}")

    # --- Connect to Deepgram and run bridge tasks ---
    try:
        async with sts_connect() as sts_ws:
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

            async def sts_sender():
                try:
                    while True:
                        chunk = await audio_queue.get()
                        if chunk is None:
                            break
                        try:
                            await sts_ws.send(chunk)
                        except ConnectionClosed:
                            break
                        except Exception as e:
                            print("[sts_sender] send failed:", repr(e))
                            break
                except Exception as e:
                    print("[sts_sender] unexpected:", repr(e))

            async def sts_receiver():
                try:
                    # Wait for streamSid from twilio receiver
                    try:
                        streamsid = await streamsid_queue.get()
                    except Exception:
                        streamsid = None

                    async for message in sts_ws:
                        if isinstance(message, str):
                            try:
                                decoded = json.loads(message)
                            except Exception:
                                decoded = {"raw": message}
                            ev_type = decoded.get("type", "")
                            print(f"[deepgram event] type={ev_type} keys={list(decoded.keys())}")
                            if ev_type == "ConversationText":
                                role = decoded.get("role")
                                content = decoded.get("content") or decoded.get("text") or ""
                                print(f"[deepgram conv] role={role} text={content[:300]}")
                                persist_transcript_fragment(role, content)
                            continue

                        # binary frames -> forward to Twilio
                        try:
                            raw = bytes(message)
                            size = len(raw)
                            print(f"[sts_receiver] received binary frame size={size} bytes; streamSid={streamsid}")
                            payload_b64 = base64.b64encode(raw).decode("ascii")
                            media_message = {
                                "event": "media",
                                "streamSid": streamsid or "",
                                "media": {"payload": payload_b64},
                            }
                            await ws.send_text(json.dumps(media_message, separators=(",", ":")))
                            print("[sts_receiver] forwarded to Twilio OK")
                        except Exception as e:
                            print("[sts_receiver] forward to Twilio failed:", e)

                except Exception as e:
                    print("[sts_receiver] unexpected outer exception:", e)

            async def twilio_receiver():
                BUFFER_SIZE = 5 * 160
                inbuffer = bytearray()
                try:
                    while True:
                        msg = await ws.receive()
                        try:
                            display = {}
                            for k, v in msg.items():
                                if isinstance(v, (str, bytes)):
                                    display[k] = (v[:200] + ("..." if len(v) > 200 else "")) if len(v) > 0 else v
                                else:
                                    display[k] = v
                            print("[twilio_receiver] raw ws.receive ->", display)
                        except Exception:
                            pass

                        mtype = msg.get("type")
                        if mtype == "websocket.disconnect":
                            print("[twilio_receiver] websocket.disconnect received")
                            break

                        text = msg.get("text")
                        data = None
                        if text is not None:
                            try:
                                data = json.loads(text)
                            except Exception:
                                continue
                        else:
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
                                print(f"[twilio_receiver] received start streamSid={streamsid}")
                                streamsid_queue.put_nowait(streamsid)
                                persist_call_start_time()
                                if should_hangup.is_set():
                                    print("[twilio_receiver] hangup already requested at start")
                                    break

                        elif evt == "media":
                            media = data.get("media", {})
                            payload_b64 = media.get("payload", "")
                            if not payload_b64:
                                continue
                            chunk = base64.b64decode(payload_b64)
                            if media.get("track") == "inbound":
                                inbuffer.extend(chunk)
                            while len(inbuffer) >= BUFFER_SIZE:
                                try:
                                    await audio_queue.put(bytes(inbuffer[:BUFFER_SIZE]))
                                except Exception:
                                    pass
                                del inbuffer[:BUFFER_SIZE]

                        elif evt == "stop":
                            print("[twilio_receiver] received stop event")
                            persist_call_end_time_and_duration()
                            break

                        if should_hangup.is_set():
                            print("[twilio_receiver] should_hangup set - breaking")
                            break

                except Exception as e:
                    print("[twilio_receiver] unexpected outer:", e)
                finally:
                    try:
                        await audio_queue.put(None)
                    except Exception:
                        try:
                            audio_queue.put_nowait(None)
                        except Exception:
                            pass

            # run tasks and wait for completion
            sender_task = asyncio.create_task(sts_sender())
            receiver_task = asyncio.create_task(sts_receiver())
            twilio_task = asyncio.create_task(twilio_receiver())
            done, pending = await asyncio.wait(
                [sender_task, receiver_task, twilio_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

    except InvalidHandshake as e:
        print("[bridge_ws] Deepgram handshake failed:", repr(e))
    except Exception as e:
        print("[bridge_ws] UNCAUGHT exception:", repr(e))
    finally:
        try:
            await ws.close()
        except Exception:
            pass
        print(f"[bridge_ws] FINISHED: call_id={call_id} agent={agent}")

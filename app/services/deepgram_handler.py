# app/services/deepgram_handler.py
"""
Deepgram <-> Twilio bridge with DB-first agent resolution + personalized greeting.

Primary resolution order:
1) Parse call_id from path (handles /ws/NN, /ws/call_id=NN, and percent-encoded forms)
2) Fetch agent from DB using call_id (PRIMARY source of truth)
3) Fallback only to querystring agent if DB did not provide an agent

Then it requests mu-law@8000 from Deepgram and forwards audio both ways (no PCM conversion).
It logs at every decision point to help debug agent resolution issues.

NEW:
- If PERSONALIZED_GREETING is enabled (default), when call_id resolves to a patient,
  Annie greets them by first name and the patient/org context is prepended to the prompt.
"""

import asyncio
import base64
import json
import os
from app.services.sms import send_marketing_sms
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime
from typing import Optional

import websockets
from websockets.exceptions import InvalidHandshake, ConnectionClosed

# Env toggle (1/true enabled; 0/false disabled)
PERSONALIZED_GREETING = os.getenv("PERSONALIZED_GREETING", "1").lower() not in ("0", "false", "no")

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


def _first_name(full_name: Optional[str]) -> Optional[str]:
    if not full_name:
        return None
    parts = [p for p in full_name.strip().split() if p]
    return parts[0] if parts else None


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
    patient = None
    org = None
    patient_id = None  # Track patient_id for function calls
    if call_id is not None:
        try:
            from app import db, models  # lazy import
            session = db.SessionLocal()
            try:
                call_row = session.query(models.Call).filter(models.Call.id == call_id).first()
                if call_row:
                    print(f"[bridge_ws] DB lookup: found call row id={call_id} agent={getattr(call_row, 'agent', None)}")
                    agent = getattr(call_row, "agent", None)
                    patient_id = getattr(call_row, "patient_id", None)  # Capture patient_id
                    # Fetch patient + org for personalization
                    try:
                        if patient_id:
                            patient = session.query(models.Patient).filter(models.Patient.id == patient_id).first()
                        org = session.query(models.Organization).filter(models.Organization.id == call_row.org_id).first()
                    except Exception as e:
                        print(f"[bridge_ws] patient/org lookup failed: {e}")
                else:
                    print(f"[bridge_ws] DB lookup: NO call row found for id={call_id}")
            finally:
                session.close()
        except Exception as e:
            print(f"[bridge_ws] DB lookup exception for call_id={call_id}: {e}")
            agent = None
    else:
        print("[bridge_ws] call_id is None; cannot fetch agent from DB as primary source")

    # --- Fallback: check querystring for agent (only if DB didn't produce an agent) ---
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
    base_prompt = load_prompt(prompt_path)
    print(f"[bridge_ws] using prompt file: {prompt_path} (exists={os.path.isfile(prompt_path)})")

    # --- Personalized greeting + dynamic context block ---
    greeting_text = "Hello"
    dynamic_prefix = ""
    if PERSONALIZED_GREETING and patient:
        fname = _first_name(getattr(patient, "name", None)) or getattr(patient, "name", None)
        if fname:
            greeting_text = f"Hello {fname}"
        dob_iso = None
        try:
            if getattr(patient, "dob", None):
                dob_iso = patient.dob.strftime("%Y-%m-%d")
        except Exception:
            dob_iso = None
        org_name = getattr(org, "name", None)
        dyn_lines = [
            "### PATIENT CONTEXT (do not reveal confidential details):",
            f"- patient_legal_name: {getattr(patient, 'name', None) or 'unknown'}",
            f"- patient_first_name: {fname or 'unknown'}",
            f"- patient_id_internal: {getattr(patient, 'patient_id', None) or getattr(patient, 'id', None)}",
            f"- patient_dob: {dob_iso or 'unknown'}",
            f"- organization_name: {org_name or 'unknown'}",
            "",
            "### VOICE & TONE:",
            "- Greet the patient by first name once at the start.",
            "- Be clear, empathetic, professional; avoid repeating their name unnecessarily.",
            "",
            "### TASK:",
            "- Collect vitals: BP (systolic/diastolic), pulse, glucose, weight.",
            "- Confirm understanding and provide a brief summary.",
            "",
        ]
        dynamic_prefix = "\n".join(dyn_lines)

   # prompt_text_final = (dynamic_prefix + (base_prompt or "You are a helpful AI nurse assisting a patient.")).strip()

    # --- prepare queues and persistence helpers ---
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()
    should_hangup = asyncio.Event()

    async def handle_function_call(function_name: str, input_data: dict, call_id_val: int, patient_id_val: int):
        """
        Handle function calls from Deepgram agent (client-side execution).
        Currently supports: detect_emergency
        """
        print(f"[handle_function_call] Executing {function_name} with input={input_data}")
        
        if function_name == "detect_emergency":
            try:
                from app import db, models
                import requests
                
                severity = input_data.get("severity", "high")
                reason = input_data.get("reason", "Emergency detected during call")
                
                # Call emergency API
                payload = {
                    "call_id": call_id_val,
                    "patient_id": patient_id_val,
                    "severity": severity,
                    "signal_text": reason,
                    "detector_info": {
                        "model": "deepgram_function_call",
                        "function": function_name,
                        "severity": severity
                    }
                }
                
                # Use internal API call
                try:
                    # Get base URL from environment or use localhost
                    import os
                    base_url = os.getenv("PUBLIC_HOST", "http://localhost:5000")
                    api_url = f"{base_url}/api/emergency/event"
                    
                    response = requests.post(api_url, json=payload, timeout=5)
                    response.raise_for_status()
                    
                    print(f"[handle_function_call] Emergency event created successfully")
                    return {
                        "success": True,
                        "message": f"Emergency logged with severity {severity}. Medical staff will be notified.",
                        "event_id": response.json().get("id")
                    }
                except Exception as e:
                    print(f"[handle_function_call] API call failed, trying direct DB: {e}")
                    
                    # Fallback: direct DB insertion
                    session = db.SessionLocal()
                    try:
                        patient = session.query(models.Patient).filter(models.Patient.id == patient_id_val).first()
                        if not patient:
                            return {"success": False, "message": "Patient not found"}
                        
                        org_id = getattr(patient, 'org_id', None)
                        
                        emerg_event = models.EmergencyEvent(
                            call_id=call_id_val,
                            patient_id=patient_id_val,
                            org_id=org_id,
                            severity=severity,
                            signal_text=reason,
                            detector_info=json.dumps(payload["detector_info"]),
                            detected_at=datetime.utcnow(),
                            created_at=datetime.utcnow(),
                        )
                        session.add(emerg_event)
                        
                        patient.emergency_flag = 1
                        patient.last_emergency_at = emerg_event.detected_at
                        session.add(patient)
                        
                        session.commit()
                        event_id = emerg_event.id
                        session.close()
                        
                        print(f"[handle_function_call] Emergency event {event_id} created via direct DB")
                        return {
                            "success": True,
                            "message": f"Emergency logged with severity {severity}. Medical staff will be notified.",
                            "event_id": event_id
                        }
                    except Exception as db_err:
                        session.rollback()
                        session.close()
                        print(f"[handle_function_call] Direct DB failed: {db_err}")
                        return {"success": False, "message": f"Failed to log emergency: {str(db_err)}"}
                        
            except Exception as e:
                print(f"[handle_function_call] Error in detect_emergency: {e}")
                return {"success": False, "message": f"Error: {str(e)}"}
        
        return {"success": False, "message": f"Unknown function: {function_name}"}

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
                    print(f"[persist_call_end_time_and_duration] set end_time for call_id={call_id} agent={getattr(call_row,'agent',None)} patient_id={getattr(call_row,'patient_id',None)}")

                    # If this call used the wellcare_marketing agent, send follow-up SMS here
                    try:
                        agent_name = getattr(call_row, 'agent', None)
                        if agent_name == "wellcare_marketing" and getattr(call_row, 'patient_id', None):
                            try:
                                patient = session.query(models.Patient).filter(models.Patient.id == call_row.patient_id).first()
                            except Exception as e:
                                patient = None
                                print(f"[persist_call_end_time_and_duration] patient lookup failed: {e}")

                            if patient and getattr(patient, 'phone', None):
                                to_number = getattr(patient, 'phone')
                                print(f"[marketing][bridge] Sending follow-up SMS to patient {patient.id} phone={to_number} (agent: {agent_name})")
                                try:
                                    ok, err = send_marketing_sms(to_number)
                                    if ok:
                                        print(f"[marketing][bridge] SMS send helper reported OK to {to_number}")
                                    else:
                                        print(f"[marketing][bridge] SMS send helper reported ERROR to {to_number}: {err}")
                                except Exception as e:
                                    print(f"[marketing][bridge] SMS helper raised exception: {e}")
                            else:
                                print(f"[marketing][bridge] No patient or phone for patient_id={getattr(call_row,'patient_id',None)}")
                    except Exception as e:
                        print(f"[persist_call_end_time_and_duration] marketing SMS flow failed: {e}")
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
                        "prompt":  (base_prompt or "You are a helpful AI nurse assisting a patient.").strip() + "\n\nIMPORTANT: If the patient mentions ANY of the following, you MUST immediately call the detect_emergency function:\n- Chest pain, severe chest pain, pressure in chest\n- Can't breathe, difficulty breathing, shortness of breath\n- Calling 911, need emergency help, need ambulance\n- Heart attack, stroke symptoms\n- Severe pain anywhere in the body\n- Feeling dizzy, lightheaded, or faint\n- Any life-threatening situation\n\nCall detect_emergency BEFORE responding to the patient.",
                        "functions": [
                            {
                                "name": "detect_emergency",
                                "description": "MUST be called immediately when patient reports chest pain, difficulty breathing, mentions 911, or any life-threatening symptoms. This is critical for patient safety.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "severity": {
                                            "type": "string",
                                            "enum": ["critical", "high", "medium"],
                                            "description": "critical=chest pain/can't breathe/911/stroke, high=severe pain/dizziness, medium=concerning symptoms"
                                        },
                                        "reason": {
                                            "type": "string",
                                            "description": "Exact quote of what patient said (e.g., 'severe pain in my chest')"
                                        }
                                    },
                                    "required": ["severity", "reason"]
                                }
                            }
                        ]
                    },
                    "speak": {"provider": {"type": "deepgram", "model": "aura-2-thalia-en"}},
   #                 "greeting": greeting_text,
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
                            
                            # Handle function call requests from Deepgram
                            if ev_type == "FunctionCallRequest":
                                # The message format may have 'functions' array or direct properties
                                functions_list = decoded.get("functions", [])
                                
                                # Try to get function details from the message
                                function_call_id = decoded.get("function_call_id") or decoded.get("id")
                                function_name = decoded.get("function_name") or decoded.get("name")
                                input_data = decoded.get("input", {})
                                
                                # If functions array exists, use the first function
                                if functions_list and len(functions_list) > 0:
                                    func = functions_list[0]
                                    function_name = func.get("name")
                                    input_data = func.get("arguments", {})
                                    function_call_id = func.get("call_id") or function_call_id
                                
                                print(f"[function_call] Received request: function={function_name} call_id={function_call_id} input={input_data}")
                                print(f"[function_call] Full message: {decoded}")
                                
                                if not function_name:
                                    print(f"[function_call] Warning: No function_name found in message")
                                    continue
                                
                                # Execute the function and send response
                                try:
                                    result = await handle_function_call(function_name, input_data, call_id, patient_id)
                                    response_msg = {
                                        "type": "FunctionCallResponse",
                                        "function_call_id": function_call_id,
                                        "output": result
                                    }
                                    await sts_ws.send(json.dumps(response_msg))
                                    print(f"[function_call] Sent response for call_id={function_call_id}")
                                except Exception as e:
                                    print(f"[function_call] Error executing {function_name}: {e}")
                                    error_response = {
                                        "type": "FunctionCallResponse",
                                        "function_call_id": function_call_id,
                                        "output": {"error": str(e)}
                                    }
                                    await sts_ws.send(json.dumps(error_response))
                                continue
                            
                            if ev_type == "ConversationText":
                                role = decoded.get("role")
                                content = decoded.get("content") or decoded.get("text") or ""
                                print(f"[deepgram conv] role={role} text={content[:300]}")
                                persist_transcript_fragment(role, content)
                            elif ev_type == "Error":
                                error_desc = decoded.get("description", "Unknown error")
                                error_code = decoded.get("code", "Unknown code")
                                print(f"[deepgram ERROR] code={error_code} description={error_desc}")
                                print(f"[deepgram ERROR] Full message: {decoded}")
                            elif ev_type == "History":
                                # History event - just log it
                                print(f"[deepgram history] {decoded}")
                            else:
                                # Log any other event types we're not handling
                                print(f"[deepgram unhandled] type={ev_type} message={decoded}")
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

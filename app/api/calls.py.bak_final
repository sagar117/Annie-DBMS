# app/api/calls.py
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app import db, models, schemas

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calls", tags=["calls"])


def get_db():
    dbs = db.SessionLocal()
    try:
        yield dbs
    finally:
        dbs.close()


@router.get("/", response_model=List[schemas.CallOut])
def list_calls(
    org_id: int = Query(..., description="Filter by org ID"),
    date: Optional[date] = Query(None, description="Filter calls on this date (YYYY-MM-DD)"),
    from_date: Optional[datetime] = Query(None, description="Filter calls created after this datetime"),
    to_date: Optional[datetime] = Query(None, description="Filter calls created before this datetime"),
    db_session: Session = Depends(get_db),
):
    """
    Get calls for an organization, filterable by exact date or date range.
    """
    q = db_session.query(models.Call).filter(models.Call.org_id == org_id)

    if date:
        start = datetime.combine(date, datetime.min.time())
        end = datetime.combine(date, datetime.max.time())
        q = q.filter(models.Call.created_at >= start, models.Call.created_at <= end)

    if from_date:
        q = q.filter(models.Call.created_at >= from_date)

    if to_date:
        q = q.filter(models.Call.created_at <= to_date)

    rows = q.order_by(models.Call.created_at.desc()).all()
    return rows


# Keep or replace your existing endpoints below (outbound, twiml, complete, get call, readings)
# I include working versions of these to preserve your prior behavior.

@router.post("/outbound")
def outbound_call(request: Request, payload: Dict[str, Any]):
    """
    Create an outbound call record and (optionally) create the Twilio call.
    Uses the incoming request host to build TwiML URL.
    Expected JSON payload:
      { "org_id": <int>, "patient_id": <int|string>, "to_number": "+...", "agent": "annie_RPM" }
    """
    body = payload or {}
    org_id = body.get("org_id")
    patient_id = body.get("patient_id")
    to_number = body.get("to_number")
    agent = body.get("agent") or "annie_RPM"

    if not (org_id and patient_id and to_number):
        raise HTTPException(status_code=400, detail="org_id, patient_id and to_number are required")

    session = db.SessionLocal()
    try:
        new_call = models.Call(
            org_id=org_id,
            patient_id=patient_id,
            twilio_call_sid=None,
            agent=agent,
            status="initiated",
            start_time=None,
            end_time=None,
            transcript=None,
            summary=None,
        )
        session.add(new_call)
        session.commit()
        session.refresh(new_call)
        call_id = new_call.id

        # Build host from the incoming request (ngrok/public host that contacted this API)
        host = request.url.netloc

        # Try create Twilio call (best-effort)
        try:
            import os, html, requests
            TW_SID = os.getenv("TWILIO_ACCOUNT_SID")
            TW_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
            TW_FROM = os.getenv("TWILIO_FROM_NUMBER")

            if TW_SID and TW_TOKEN and TW_FROM:
                twiml_url = f"https://{host}/api/calls/twiml/outbound/{call_id}?agent={html.escape(agent)}"
                resp = requests.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{TW_SID}/Calls.json",
                    auth=(TW_SID, TW_TOKEN),
                    data={"To": to_number, "From": TW_FROM, "Url": twiml_url, "Method": "GET"},
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    tw_sid = data.get("sid")
                    if tw_sid:
                        try:
                            s2 = db.SessionLocal()
                            try:
                                call_row = s2.query(models.Call).filter(models.Call.id == call_id).first()
                                if call_row:
                                    call_row.twilio_call_sid = tw_sid
                                    s2.add(call_row)
                                    s2.commit()
                            finally:
                                s2.close()
                        except Exception as e:
                            logger.exception("failed to save twilio_call_sid: %s", e)
                else:
                    logger.warning("Twilio create call failed: %s %s", resp.status_code, resp.text)
            else:
                logger.info("Twilio creds / FROM number not set. Skipping Twilio call.")
        except Exception as e:
            logger.exception("Twilio call error: %s", e)

        return {"call_id": call_id, "status": "initiated"}
    finally:
        session.close()


@router.api_route("/twiml/outbound/{call_id}", methods=["GET", "POST"])
async def twiml_outbound(call_id: int, request: Request, agent: Optional[str] = None):
    """
    TwiML returned to Twilio for outbound calls. Contains Connect->Stream with call_id and agent.
    Twilio may fetch this via GET or POST.
    """
    # Prefer explicit query param or form field 'agent'
    agent_name = None
    try:
        q_agent = request.query_params.get("agent")
        if q_agent:
            agent_name = q_agent
        else:
            if request.method == "POST":
                form = await request.form()
                agent_name = form.get("agent") or agent
            else:
                agent_name = agent
    except Exception:
        agent_name = agent

    if not agent_name:
        agent_name = "annie_RPM"

    host = request.url.netloc
    # Use path-based stream URL to ensure call_id reaches the websocket reliably
    stream_url = f"wss://{host}/ws/{call_id}?agent={agent_name}"
    stream_url_escaped = stream_url.replace('"', "&quot;")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{stream_url_escaped}"/>
  </Connect>
</Response>"""
    from fastapi.responses import Response
    return Response(content=twiml, media_type="application/xml")


@router.post("/{call_id}/complete")
def complete_call(call_id: int):
    """
    Mark call completed, run OpenAI extraction to get readings & save them to DB.
    Idempotent: safe to call multiple times.
    """
    session = db.SessionLocal()
    try:
        call = session.query(models.Call).filter(models.Call.id == call_id).first()
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")

        if call.status == "completed":
            return {"call_id": call.id, "status": "already_completed"}

        # mark end
        call.end_time = call.end_time or datetime.utcnow()
        call.status = "completed"
        if call.start_time and call.end_time:
            call.duration_seconds = int((call.end_time - call.start_time).total_seconds())
        session.add(call)
        session.commit()

        # run OpenAI extraction (best-effort)
        transcript_text = (call.transcript or "") + "\n" + (call.summary or "")
        try:
            from app.services import openai_client
            parsed = openai_client.extract_readings_from_transcript(transcript_text)
        except Exception as e:
            parsed = {}
            logger.exception("openai extraction failed: %s", e)

        # If parsed contains a 'summary' field, save it
        try:
            if isinstance(parsed, dict) and parsed.get("summary"):
                call.summary = (call.summary or "") + "\n[OA_SUMMARY] " + str(parsed["summary"])[:3000]
                session.add(call)
                session.commit()
        except Exception as e:
            logger.exception("saving summary failed: %s", e)

        # Persist readings into models.Reading (support dict or list)
        try:
            readings = None
            if isinstance(parsed, dict) and parsed.get("readings"):
                readings = parsed["readings"]
            elif isinstance(parsed, dict):
                readings = parsed
            elif isinstance(parsed, list):
                readings = parsed

            if readings:
                import json as _json
                if isinstance(readings, dict):
                    for key, val in readings.items():
                        if val is None:
                            continue
                        rd = models.Reading(
                            patient_id=call.patient_id,
                            call_id=call.id,
                            reading_type=key,
                            value=_json.dumps({"value": val}),
                            units=None,
                            raw_text=str(val),
                            recorded_at=None,
                        )
                        session.add(rd)
                else:
                    for r in readings:
                        rd = models.Reading(
                            patient_id=call.patient_id,
                            call_id=call.id,
                            reading_type=r.get("type") or "unknown",
                            value=_json.dumps(r),
                            units=r.get("units"),
                            raw_text=str(r),
                            recorded_at=r.get("recorded_at"),
                        )
                        session.add(rd)
                session.commit()
        except Exception as e:
            logger.exception("persisting readings failed: %s", e)

        return {"call_id": call.id, "status": "completed"}
    finally:
        session.close()


@router.get("/{call_id}")
def get_call(call_id: int):
    session = db.SessionLocal()
    try:
        call = session.query(models.Call).filter(models.Call.id == call_id).first()
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        return {
            "id": call.id,
            "org_id": call.org_id,
            "patient_id": call.patient_id,
            "agent": call.agent,
            "status": call.status,
            "start_time": call.start_time.isoformat() if call.start_time else None,
            "end_time": call.end_time.isoformat() if call.end_time else None,
            "duration_seconds": call.duration_seconds,
            "transcript": call.transcript,
            "summary": call.summary,
            "twilio_call_sid": call.twilio_call_sid,
        }
    finally:
        session.close()


@router.get("/{call_id}/readings")
def get_call_readings(call_id: int, persist_if_missing: bool = Query(True)):
    """
    Primary: return persisted readings from DB for call_id.
    If none are present and persist_if_missing=True, call OpenAI to extract readings,
    return them and persist them into readings table (best-effort).
    """
    session = db.SessionLocal()
    try:
        # 1) Try to read persisted readings
        rows = session.query(models.Reading).filter(models.Reading.call_id == call_id).all()
        if rows:
            out = {}
            import json as _json
            for r in rows:
                try:
                    val = None
                    if r.value:
                        try:
                            val = _json.loads(r.value)
                        except Exception:
                            val = r.value
                    out.setdefault(r.reading_type, []).append({
                        "id": r.id,
                        "patient_id": r.patient_id,
                        "call_id": r.call_id,
                        "reading_type": r.reading_type,
                        "value": val,
                        "raw_text": r.raw_text,
                        "units": r.units,
                        "recorded_at": r.recorded_at.isoformat() if getattr(r, "recorded_at", None) else None,
                    })
                except Exception:
                    out.setdefault("unknown", []).append({
                        "id": getattr(r, "id", None),
                        "raw": str(r)
                    })
            return {"call_id": call_id, "from_db": True, "readings": out}

        # 2) Nothing in DB -> optionally run OpenAI extraction
        call = session.query(models.Call).filter(models.Call.id == call_id).first()
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")

        if not persist_if_missing:
            try:
                from app.services import openai_client
                res = openai_client.extract_readings_from_transcript((call.transcript or "") + "\n" + (call.summary or ""))
            except Exception as e:
                logger.exception("openai runtime failed: %s", e)
                res = {}
            return {"call_id": call_id, "from_db": False, "readings": res}

        # 3) Run OpenAI extraction and persist results
        try:
            from app.services import openai_client
            parsed = openai_client.extract_readings_from_transcript((call.transcript or "") + "\n" + (call.summary or ""))
        except Exception as e:
            logger.exception("openai extraction failed:", e)
            parsed = {}

        # Persist parsed into readings table if parsed is dict/list
        try:
            import json as _json
            readings = None
            if isinstance(parsed, dict) and parsed.get("readings"):
                readings = parsed["readings"]
            elif isinstance(parsed, dict):
                readings = parsed
            elif isinstance(parsed, list):
                readings = parsed

            if readings:
                if isinstance(readings, dict):
                    for key, val in readings.items():
                        if val is None:
                            continue
                        rd = models.Reading(
                            patient_id=call.patient_id,
                            call_id=call.id,
                            reading_type=key,
                            value=_json.dumps({"value": val}),
                            units=None,
                            raw_text=str(val),
                            recorded_at=None,
                        )
                        session.add(rd)
                else:
                    for r in readings:
                        rd = models.Reading(
                            patient_id=call.patient_id,
                            call_id=call.id,
                            reading_type=r.get("type") or "unknown",
                            value=_json.dumps(r),
                            units=r.get("units"),
                            raw_text=str(r),
                            recorded_at=r.get("recorded_at"),
                        )
                        session.add(rd)
                session.commit()
        except Exception as e:
            logger.exception("get_call_readings persisting parsed readings failed: %s", e)

        # Return parsed result
        return {"call_id": call_id, "from_db": False, "readings": parsed}
    finally:
        session.close()

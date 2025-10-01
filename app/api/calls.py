# app/api/calls.py
import logging
import html
import os
import json as _json
from typing import List, Optional, Dict, Any
from datetime import datetime, date as _date

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, PlainTextResponse
from sqlalchemy.orm import Session

from app import db, models, schemas

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calls", tags=["calls"])


# -------------------------------
# DB session dependency
# -------------------------------
def get_db():
    dbs = db.SessionLocal()
    try:
        yield dbs
    finally:
        dbs.close()


# -------------------------------
# Helpers for daily readings
# -------------------------------
def _extract_normalized_readings(parsed: dict) -> dict:
    """
    Normalize parsed OpenAI readings into:
      {"bp_systolic": int|None, "bp_diastolic": int|None,
       "pulse": int|None, "glucose": int|None, "weight": int|None}
    Accepts shapes like:
      {"readings": {"bp":{"systolic":..,"diastolic":..}, "pulse":.., "glucose":.., "weight":..}}
      {"bp": {"systolic":..,"diastolic":..}, "pulse":.., ...}
      [{"type":"bp","systolic":..,"diastolic":..}, {"type":"pulse","value":..}, ...]
    """
    out = {"bp_systolic": None, "bp_diastolic": None, "pulse": None, "glucose": None, "weight": None}
    if not parsed:
        return out

    src = parsed.get("readings", parsed)

    # dict form
    if isinstance(src, dict):
        bp = src.get("bp") or src.get("blood_pressure")
        if isinstance(bp, dict):
            try:
                if bp.get("systolic") is not None:
                    out["bp_systolic"] = int(float(bp.get("systolic")))
            except Exception:
                pass
            try:
                if bp.get("diastolic") is not None:
                    out["bp_diastolic"] = int(float(bp.get("diastolic")))
            except Exception:
                pass

        for k_api, k_norm in (("pulse", "pulse"), ("glucose", "glucose"), ("weight", "weight")):
            v = src.get(k_api)
            if v is not None:
                try:
                    out[k_norm] = int(float(v))
                except Exception:
                    pass

    # list form
    if isinstance(src, list):
        for item in src:
            typ = (item.get("type") or "").lower()
            if "bp" in typ or "blood" in typ:
                try:
                    if item.get("systolic") is not None:
                        out["bp_systolic"] = int(float(item.get("systolic")))
                    if item.get("diastolic") is not None:
                        out["bp_diastolic"] = int(float(item.get("diastolic")))
                except Exception:
                    pass
            elif "pulse" in typ:
                try:
                    out["pulse"] = int(float(item.get("value")))
                except Exception:
                    pass
            elif "glucose" in typ:
                try:
                    out["glucose"] = int(float(item.get("value")))
                except Exception:
                    pass
            elif "weight" in typ:
                try:
                    out["weight"] = int(float(item.get("value")))
                except Exception:
                    pass

    return out


def _upsert_daily_reading(
    session: Session,
    org_id: int,
    patient_id: int,
    call_id: int,
    call_dt: Optional[datetime],
    normalized: dict,
):
    """
    Upsert into patient_daily_readings (one row per patient per reading_date).
    Update only non-null fields; otherwise keep existing values.
    """
    from app.models import PatientDailyReading  # local import to avoid cycles

    if not call_dt:
        reading_date = _date.today()
    else:
        reading_date = call_dt.date()

    # If all fields are None, skip
    if all(normalized.get(k) is None for k in ("bp_systolic", "bp_diastolic", "pulse", "glucose", "weight")):
        return

    row = (
        session.query(PatientDailyReading)
        .filter(
            PatientDailyReading.patient_id == patient_id,
            PatientDailyReading.reading_date == reading_date,
        )
        .first()
    )

    if row:
        changed = False
        for k in ("bp_systolic", "bp_diastolic", "pulse", "glucose", "weight"):
            v = normalized.get(k)
            if v is not None and getattr(row, k) != v:
                setattr(row, k, v)
                changed = True
        row.source_call_id = call_id
        if changed:
            session.add(row)
            session.commit()
    else:
        row = PatientDailyReading(
            org_id=org_id,
            patient_id=patient_id,
            reading_date=reading_date,
            bp_systolic=normalized.get("bp_systolic"),
            bp_diastolic=normalized.get("bp_diastolic"),
            pulse=normalized.get("pulse"),
            glucose=normalized.get("glucose"),
            weight=normalized.get("weight"),
            source_call_id=call_id,
        )
        session.add(row)
        session.commit()


# -------------------------------
# List calls (by org and date/range)
# -------------------------------
@router.get("/", response_model=List[schemas.CallOut])
def list_calls(
    org_id: int = Query(..., description="Organization ID"),
    date: Optional[_date] = Query(None, description="Filter by date (YYYY-MM-DD)"),
    from_date: Optional[datetime] = Query(None, description="Created at >= (ISO datetime)"),
    to_date: Optional[datetime] = Query(None, description="Created at <= (ISO datetime)"),
    db_session: Session = Depends(get_db),
):
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


# -------------------------------
# Create outbound call
# -------------------------------
@router.post("/outbound")
def outbound_call(request: Request, payload: Dict[str, Any]):
    """
    Create a call record and (best-effort) place a Twilio outbound call.
    Body:
      { "org_id": <int>, "patient_id": <int>, "to_number": "+91...", "agent": "annie_RPM" }
    Returns: { "call_id": <int>, "status": "initiated" }
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
            agent=agent,
            status="initiated",
            twilio_call_sid=None,
            start_time=None,
            end_time=None,
            transcript=None,
            summary=None,
        )
        session.add(new_call)
        session.commit()
        session.refresh(new_call)
        call_id = new_call.id

        # Build host for TwiML URL
        public_host = os.getenv("PUBLIC_HOST")
        host = public_host if public_host else request.url.netloc
        twiml_url = f"https://{host}/api/calls/twiml/outbound/{call_id}?agent={html.escape(agent)}"

        # Twilio call (best-effort)
        try:
            TW_SID = os.getenv("TWILIO_ACCOUNT_SID")
            TW_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
            TW_FROM = os.getenv("TWILIO_FROM_NUMBER") or os.getenv("TWILIO_FROM")  # tolerate alt name
            if TW_SID and TW_TOKEN and TW_FROM:
                resp = requests.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{TW_SID}/Calls.json",
                    auth=(TW_SID, TW_TOKEN),
                    data={"To": to_number, "From": TW_FROM, "Url": twiml_url, "Method": "GET"},
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    sid = data.get("sid")
                    if sid:
                        new_call.twilio_call_sid = sid
                        session.add(new_call)
                        session.commit()
                else:
                    logger.warning("Twilio create call failed: %s %s", resp.status_code, resp.text)
            else:
                logger.info("Twilio credentials or FROM number not set; skipping outbound call.")
        except Exception as e:
            logger.exception("Twilio call error: %s", e)

        return {"call_id": call_id, "status": "initiated"}
    finally:
        session.close()


# -------------------------------
# TwiML for outbound calls (GET or POST)
# -------------------------------
@router.api_route("/twiml/outbound/{call_id}", methods=["GET", "POST"])
async def twiml_outbound(call_id: int, request: Request, agent: Optional[str] = None):
    """
    TwiML returned to Twilio. Connects to our /ws bridge with call_id & agent.
    """
    # Prefer query param agent if present; otherwise accept form or function arg
    agent_name = request.query_params.get("agent") or agent
    if agent_name is None and request.method == "POST":
        try:
            form = await request.form()
            agent_name = form.get("agent")
        except Exception:
            pass
    agent_name = agent_name or "annie_RPM"

    public_host = os.getenv("PUBLIC_HOST")
    host = public_host if public_host else request.url.netloc

    # IMPORTANT: use path-segment for call_id so it’s preserved by Twilio/WS
    stream_url = f"wss://{host}/ws/{call_id}?agent={agent_name}"
    stream_url_escaped = stream_url.replace('"', "&quot;")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{stream_url_escaped}"/>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


# -------------------------------
# Complete call: finalize + extract readings + persist + upsert daily
# -------------------------------
@router.post("/{call_id}/complete")
def complete_call(call_id: int):
    """
    Mark call as completed. If transcript/summary exist, extract readings via OpenAI,
    persist into readings table, and upsert into patient_daily_readings for the call day.
    Idempotent: if already completed, returns status accordingly.
    """
    session = db.SessionLocal()
    try:
        call = session.query(models.Call).filter(models.Call.id == call_id).first()
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")

        if call.status == "completed":
            # Still perform daily upsert in case it was introduced later
            try:
                parsed = {}
                if call.summary or call.transcript:
                    from app.services import openai_client
                    parsed = openai_client.extract_readings_from_transcript(
                        (call.transcript or "") + "\n" + (call.summary or "")
                    ) or {}
                normalized = _extract_normalized_readings(parsed if isinstance(parsed, dict) else {})
                call_dt = call.end_time or call.start_time or datetime.utcnow()
                _upsert_daily_reading(session, call.org_id, call.patient_id, call.id, call_dt, normalized)
            except Exception as e:
                logger.exception("daily upsert on already_completed failed: %s", e)

            return {"call_id": call.id, "status": "already_completed"}

        # mark end & duration
        call.end_time = call.end_time or datetime.utcnow()
        call.status = "completed"
        if call.start_time and call.end_time:
            try:
                call.duration_seconds = int((call.end_time - call.start_time).total_seconds())
            except Exception:
                call.duration_seconds = None
        session.add(call)
        session.commit()

        # Extract readings via OpenAI (best-effort)
        transcript_text = (call.transcript or "") + "\n" + (call.summary or "")
        parsed = {}
        try:
            from app.services import openai_client
            parsed = openai_client.extract_readings_from_transcript(transcript_text) or {}
        except Exception as e:
            logger.exception("openai extraction failed: %s", e)

        # If parsed has 'summary', append into call.summary for audit
        try:
            if isinstance(parsed, dict) and parsed.get("summary"):
                call.summary = ((call.summary or "") + "\n[auto_summary] " + str(parsed["summary"]))[:8000]
                session.add(call)
                session.commit()
        except Exception as e:
            logger.exception("saving parsed summary failed: %s", e)

        # Persist granular readings to readings table
        try:
            readings = None
            if isinstance(parsed, dict) and parsed.get("readings"):
                readings = parsed["readings"]
            elif isinstance(parsed, dict):
                readings = parsed
            elif isinstance(parsed, List) or isinstance(parsed, list):
                readings = parsed

            if readings:
                if isinstance(readings, dict):
                    # dict-of-values form
                    for key, val in readings.items():
                        if val is None:
                            continue
                        row = models.Reading(
                            patient_id=call.patient_id,
                            call_id=call.id,
                            reading_type=str(key),
                            value=_json.dumps(val if isinstance(val, dict) else {"value": val}),
                            units=None,
                            raw_text=str(val),
                            recorded_at=call.end_time or call.start_time or datetime.utcnow(),
                        )
                        session.add(row)
                else:
                    # list-of-objects form
                    for r in readings:
                        row = models.Reading(
                            patient_id=call.patient_id,
                            call_id=call.id,
                            reading_type=(r.get("type") or "unknown"),
                            value=_json.dumps(r),
                            units=r.get("units"),
                            raw_text=str(r),
                            recorded_at=r.get("recorded_at") or (call.end_time or call.start_time or datetime.utcnow()),
                        )
                        session.add(row)
                session.commit()
        except Exception as e:
            logger.exception("persisting detailed readings failed: %s", e)

        # Upsert consolidated day-wise row
        try:
            normalized = _extract_normalized_readings(parsed if isinstance(parsed, dict) else {})
            call_dt = call.end_time or call.start_time or datetime.utcnow()
            _upsert_daily_reading(
                session=session,
                org_id=call.org_id,
                patient_id=call.patient_id,
                call_id=call.id,
                call_dt=call_dt,
                normalized=normalized,
            )
        except Exception as e:
            logger.exception("daily reading upsert failed: %s", e)

        return {"call_id": call.id, "status": "completed"}
    finally:
        session.close()


# -------------------------------
# Get call details
# -------------------------------
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
            "agent": getattr(call, "agent", None),
            "status": call.status,
            "start_time": call.start_time.isoformat() if call.start_time else None,
            "end_time": call.end_time.isoformat() if call.end_time else None,
            "duration_seconds": call.duration_seconds,
            "transcript": call.transcript,
            "summary": call.summary,
            "twilio_call_sid": call.twilio_call_sid,
            "created_at": call.created_at.isoformat() if getattr(call, "created_at", None) else None,
        }
    finally:
        session.close()


# -------------------------------
# Get transcript (plain text)
# -------------------------------
@router.get("/{call_id}/transcript")
def get_transcript(call_id: int):
    session = db.SessionLocal()
    try:
        call = session.query(models.Call).filter(models.Call.id == call_id).first()
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        text = (call.transcript or "").strip()
        return PlainTextResponse(text or "(empty)")
    finally:
        session.close()


# -------------------------------
# Get readings for a call
# -------------------------------
@router.get("/{call_id}/readings")
def get_call_readings(call_id: int, persist_if_missing: bool = Query(True)):
    """
    Prefer persisted readings; if not found and persist_if_missing=True, run OpenAI extraction,
    return parsed values and persist them; also upsert the daily row.
    """
    session = db.SessionLocal()
    try:
        # 1) check DB
        rows = session.query(models.Reading).filter(models.Reading.call_id == call_id).all()
        if rows:
            out = {}
            for r in rows:
                try:
                    val = None
                    if r.value:
                        try:
                            val = _json.loads(r.value)
                        except Exception:
                            val = r.value
                    out.setdefault(r.reading_type, []).append(
                        {
                            "id": r.id,
                            "patient_id": r.patient_id,
                            "call_id": r.call_id,
                            "reading_type": r.reading_type,
                            "value": val,
                            "raw_text": r.raw_text,
                            "units": r.units,
                            "recorded_at": r.recorded_at.isoformat() if getattr(r, "recorded_at", None) else None,
                        }
                    )
                except Exception:
                    out.setdefault("unknown", []).append({"id": getattr(r, "id", None), "raw": str(r)})
            return {"call_id": call_id, "from_db": True, "readings": out}

        # 2) fallback to extractor
        call = session.query(models.Call).filter(models.Call.id == call_id).first()
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")

        from app.services import openai_client
        parsed = {}
        try:
            parsed = openai_client.extract_readings_from_transcript((call.transcript or "") + "\n" + (call.summary or "")) or {}
        except Exception as e:
            logger.exception("openai runtime extract failed: %s", e)
            if not persist_if_missing:
                return {"call_id": call_id, "from_db": False, "readings": {}}

        # 3) persist if requested
        if persist_if_missing and parsed:
            try:
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
                            row = models.Reading(
                                patient_id=call.patient_id,
                                call_id=call.id,
                                reading_type=str(key),
                                value=_json.dumps(val if isinstance(val, dict) else {"value": val}),
                                units=None,
                                raw_text=str(val),
                                recorded_at=call.end_time or call.start_time or datetime.utcnow(),
                            )
                            session.add(row)
                    else:
                        for r in readings:
                            row = models.Reading(
                                patient_id=call.patient_id,
                                call_id=call.id,
                                reading_type=(r.get("type") or "unknown"),
                                value=_json.dumps(r),
                                units=r.get("units"),
                                raw_text=str(r),
                                recorded_at=r.get("recorded_at") or (call.end_time or call.start_time or datetime.utcnow()),
                            )
                            session.add(row)
                    session.commit()
            except Exception as e:
                logger.exception("persist parsed readings (get_call_readings) failed: %s", e)

            # also upsert daily
            try:
                normalized = _extract_normalized_readings(parsed if isinstance(parsed, dict) else {})
                call_dt = call.end_time or call.start_time or datetime.utcnow()
                _upsert_daily_reading(session, call.org_id, call.patient_id, call.id, call_dt, normalized)
            except Exception as e:
                logger.exception("daily upsert from get_call_readings failed: %s", e)

        return {"call_id": call_id, "from_db": False, "readings": parsed or {}}
    finally:
        session.close()




@router.get("/by-patient/{patient_id}", response_model=List[schemas.CallOut])
def list_calls_by_patient(
    patient_id: int,
    date: Optional[_date] = Query(None, description="Filter by calendar date (YYYY-MM-DD)"),
    from_date: Optional[datetime] = Query(None, description="start_time >= (ISO datetime)"),
    to_date: Optional[datetime] = Query(None, description="end_time <= (ISO datetime)"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db_session: Session = Depends(get_db),
):
    """
    Return calls for a given patient (most recent first).
    Supports: ?date=YYYY-MM-DD OR ?from_date=&to_date=, plus simple pagination.
    """
    q = db_session.query(models.Call).filter(models.Call.patient_id == patient_id)

    if date:
        start = datetime.combine(date, datetime.min.time())
        end = datetime.combine(date, datetime.max.time())
        q = q.filter(models.Call.start_time >= start, models.Call.start_time <= end)

    if from_date:
        q = q.filter(models.Call.start_time >= from_date)
    if to_date:
        q = q.filter(models.Call.end_time <= to_date)

    rows = (
        q.order_by(models.Call.start_time.desc().nullslast(), models.Call.created_at.desc().nullslast())
         .offset((page - 1) * limit)
         .limit(limit)
         .all()
    )
    return rows

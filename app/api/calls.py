# app/api/calls.py
import os
import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from xml.sax.saxutils import escape as xml_escape
from datetime import datetime

from app import db, models, schemas
from app.services import openai_client

router = APIRouter(prefix="/api/calls", tags=["calls"])


def get_db():
    dbs = db.SessionLocal()
    try:
        yield dbs
    finally:
        dbs.close()


@router.post("/", response_model=schemas.CallOut)
def create_call(call_in: schemas.CallCreate, db_session: Session = Depends(get_db)):
    org = db_session.query(models.Organization).filter(models.Organization.id == call_in.org_id).first()
    if not org:
        raise HTTPException(status_code=400, detail="Organization not found")
    call = models.Call(org_id=call_in.org_id, patient_id=call_in.patient_id, status="queued", start_time=None)
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)
    return call


@router.get("/{call_id}", response_model=schemas.CallOut)
def get_call(call_id: int, db_session: Session = Depends(get_db)):
    call = db_session.query(models.Call).filter(models.Call.id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@router.post("/{call_id}/complete")
def complete_call(call_id: int, payload: dict, db_session: Session = Depends(get_db)):
    """
    Expected payload: {"transcript": "<text>", "duration_seconds": 123}
    This endpoint stores transcript, asks OpenAI to produce summary & readings, and stores readings.
    """
    call = db_session.query(models.Call).filter(models.Call.id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    transcript = payload.get("transcript", "")
    duration = payload.get("duration_seconds")
    call.transcript = transcript
    call.status = "completed"
    call.end_time = datetime.utcnow()
    if duration:
        try:
            call.duration_seconds = int(duration)
        except Exception:
            call.duration_seconds = None
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    # Extract summary & readings via OpenAI
    res = openai_client.extract_readings_from_transcript(transcript)
    summary = res.get("summary") or ""
    readings = res.get("readings") or []
    call.summary = summary
    db_session.add(call)
    db_session.commit()

    # save readings
    for r in readings:
        import json
        reading_type = r.get("type") or r.get("reading_type") or "unknown"
        units = r.get("units")
        created_at = r.get("recorded_at")
        val_obj = {k: r[k] for k in r.keys() if k not in ("type", "units", "recorded_at")}
        rd = models.Reading(
            patient_id=call.patient_id or 0,
            call_id=call.id,
            reading_type=reading_type,
            value=json.dumps(val_obj),
            units=units,
            raw_text=str(r),
            recorded_at=created_at,
        )
        db_session.add(rd)
    db_session.commit()

    return {"ok": True, "summary": summary, "readings_count": len(readings)}


# ---- Outbound endpoint (create DB row + dial via Twilio in one request) ----
class OutboundCallCreate(BaseModel):
    org_id: int
    patient_id: Optional[int] = None
    to_number: str
    from_number: Optional[str] = None
    agent: Optional[str] = "annie_RPM"


@router.post("/outbound", response_model=schemas.CallOut)
def outbound_call(payload: OutboundCallCreate, db_session: Session = Depends(get_db)):
    # Validate org
    org = db_session.query(models.Organization).filter(models.Organization.id == payload.org_id).first()
    if not org:
        raise HTTPException(status_code=400, detail="Organization not found")

    # Create call row immediately
    call = models.Call(org_id=payload.org_id, patient_id=payload.patient_id, status="dialing")
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    # Twilio creds (env vars)
    TW_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TW_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    DEFAULT_FROM = os.getenv("TWILIO_FROM_NUMBER")
    if not (TW_SID and TW_TOKEN):
        # mark failed
        call.status = "failed"
        db_session.add(call)
        db_session.commit()
        raise HTTPException(status_code=500, detail="Twilio credentials missing")

    from_number = payload.from_number or DEFAULT_FROM
    if not from_number:
        call.status = "failed"
        db_session.add(call)
        db_session.commit()
        raise HTTPException(status_code=400, detail="No from_number provided and TWILIO_FROM_NUMBER not set")

    # Construct the stream URL (use your correct host: app.carify.health)
    stream_url = f"wss://19d1a5aa84a9.ngrok-free.app/ws?agent={payload.agent}&call_id={call.id}"
    # Escape for XML attribute (convert & -> &amp; etc.)
    stream_url_escaped = xml_escape(stream_url, {'"': "&quot;"})

    twiml = f"""<Response>
  <Start>
    <Stream url="{stream_url_escaped}"/>
  </Start>
  <Say voice="alice">Connecting you to Annie for a short health check.</Say>
</Response>"""

    twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{TW_SID}/Calls.json"
    data = {
        "To": payload.to_number,
        "From": from_number,
        "Twiml": twiml,
    }

    try:
        resp = requests.post(twilio_url, auth=(TW_SID, TW_TOKEN), data=data, timeout=15)
    except Exception as e:
        call.status = "failed"
        db_session.add(call)
        db_session.commit()
        raise HTTPException(status_code=500, detail=f"Twilio request error: {str(e)}")

    if resp.status_code not in (200, 201):
        # Twilio call creation failed
        call.status = "failed"
        db_session.add(call)
        db_session.commit()
        raise HTTPException(status_code=500, detail=f"Twilio call failed: {resp.status_code} {resp.text}")

    j = resp.json()
    twilio_sid = j.get("sid")

    # Save provider call SID
    call.twilio_call_sid = twilio_sid
    call.status = "in_progress"
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    return call

# app/api/email_transcripts.py
import os
import smtplib
from email.message import EmailMessage
from typing import List, Optional, Union, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, validator
from sqlalchemy.orm import Session

from app import db, models

router = APIRouter(prefix="/api/email_transcripts", tags=["email"])

def get_db():
    d = db.SessionLocal()
    try:
        yield d
    finally:
        d.close()

# ---------- SMTP config via env ----------
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_USE_TLS = (os.getenv("SMTP_USE_TLS", "1").lower() in ("1", "true", "yes"))
SMTP_FROM_DEFAULT = os.getenv("EMAIL_FROM_DEFAULT", "no-reply@carify.health")

# ---------- Request schema ----------
class EmailTranscriptRequest(BaseModel):
    to: Union[EmailStr, List[EmailStr]] = Field(..., description="Recipient email(s) from frontend")
    subject: Optional[str] = Field(None, description="Email subject (optional)")
    from_email: Optional[EmailStr] = Field(None, description="Override default From")
    include_summary: bool = Field(True, description="Include call summary in body")
    attach_txt: bool = Field(True, description="Attach transcript as .txt")
    extra_headers: Optional[Dict[str, Any]] = None

    @validator("to")
    def normalize_to_list(cls, v):
        return v if isinstance(v, list) else [v]

def _fmt(dt: Optional[datetime]) -> str:
    if not dt:
        return "-"
    try:
        return dt.isoformat(sep=" ", timespec="seconds")
    except Exception:
        return str(dt)

def _build_body(call: "models.Call",
                patient: Optional["models.Patient"],
                org: Optional["models.Organization"],
                include_summary: bool) -> str:
    parts = []
    parts.append("Annie Call Transcript\n")
    if org:
        parts.append(f"Organization : {org.name}")
    parts.append(f"Call ID      : {call.id}")
    parts.append(f"Patient ID   : {call.patient_id}")
    if patient:
        parts.append(f"Patient Name : {patient.name}")
        if patient.phone:
            parts.append(f"Patient Phone: {patient.phone}")
        if getattr(patient, 'email', None):
            parts.append(f"Patient Email: {patient.email}")
    parts.append(f"Status       : {call.status}")
    parts.append(f"Start Time   : {_fmt(call.start_time)}")
    parts.append(f"End Time     : {_fmt(call.end_time)}")
    if call.duration_seconds is not None:
        parts.append(f"Duration     : {call.duration_seconds} sec")
    parts.append("")
    if include_summary and (call.summary or "").strip():
        parts.append("=== Summary ===")
        parts.append((call.summary or "").strip())
        parts.append("")
    parts.append("=== Transcript ===")
    transcript = (call.transcript or "").strip() or "(no transcript available)"
    parts.append(transcript)
    parts.append("")
    return "\n".join(parts)

def _attachment_txt(call: "models.Call") -> bytes:
    text = (call.transcript or "").strip() or "(no transcript available)"
    return text.encode("utf-8")

def _send_email(msg: EmailMessage):
    if not SMTP_HOST:
        raise RuntimeError("SMTP_HOST is not configured")
    if SMTP_USE_TLS:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            if SMTP_USER and SMTP_PASS:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            if SMTP_USER and SMTP_PASS:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)

@router.post("/calls/{call_id}")
def send_transcript(call_id: int, req: EmailTranscriptRequest, db_session: Session = Depends(get_db)):
    call = db_session.query(models.Call).filter(models.Call.id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    patient = db_session.query(models.Patient).filter(models.Patient.id == call.patient_id).first() if call.patient_id else None
    org = db_session.query(models.Organization).filter(models.Organization.id == call.org_id).first() if call.org_id else None

    from_email = req.from_email or SMTP_FROM_DEFAULT
    subject = req.subject or f"Annie Transcript — Call #{call.id}" + (f" — {patient.name}" if patient and patient.name else "")

    body = _build_body(call, patient, org, include_summary=req.include_summary)

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = ", ".join(req.to)
    msg["Subject"] = subject
    if req.extra_headers:
        for k, v in req.extra_headers.items():
            try:
                msg[k] = str(v)
            except Exception:
                pass
    msg.set_content(body)

    if req.attach_txt:
        att = _attachment_txt(call)
        msg.add_attachment(att, maintype="text", subtype="plain", filename=f"annie-transcript-call-{call.id}.txt")

    try:
        _send_email(msg)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SMTP send failed: {e}")

    return {"status": "sent", "call_id": call_id, "to": req.to}

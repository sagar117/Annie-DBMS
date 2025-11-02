# app/api/calls.py
import logging
import html
import os
import requests
import json as _json
from urllib.parse import urlparse
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, date as _date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

def send_marketing_sms(to_number: str) -> Tuple[bool, Optional[str]]:
    """Send Twilio SMS with HealthAssist marketing message.
    Returns (success, error_message)."""
    
    logger.info("[marketing_sms] Starting SMS send process to %s", to_number)
    
    if not to_number:
        logger.error("[marketing_sms] No phone number provided")
        return False, "No phone number provided"

    try:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        from_number = os.getenv("TWILIO_FROM_NUMBER")

        logger.debug("[marketing_sms] Credentials check - SID: %s, From: %s", 
                    "present" if account_sid else "missing",
                    "present" if from_number else "missing")

        if not (account_sid and auth_token and from_number):
            logger.error("[marketing_sms] Missing credentials - SID: %s, Token: %s, From: %s",
                        bool(account_sid), bool(auth_token), bool(from_number))
            return False, "Missing Twilio credentials"

        message = ("Hi, this is Annie from HealthAssist.\n"
                  "Upgrade from the old pendant — get your smart Samsung watch with 24/7 safety & health monitoring.\n"
                  "Special offer: $29.95/mo (use code SPECIAL).\n"
                  "www.wellcaretoday.com")

        logger.info("[marketing_sms] Attempting to send SMS to %s from %s", to_number, from_number)
        
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={
                "To": to_number,
                "From": from_number,
                "Body": message
            },
            timeout=10
        )
        
        try:
            response_data = resp.json()
            logger.debug("[marketing_sms] Twilio response: %s", response_data)
            
            if resp.status_code in (200, 201):
                message_sid = response_data.get('sid')
                logger.info("[marketing_sms] Successfully sent to %s (SID: %s)", to_number, message_sid)
                return True, None
            
            error_code = response_data.get('code')
            error_message = response_data.get('message')
            logger.error("[marketing_sms] Failed to send: Status: %s, Code: %s, Message: %s", 
                        resp.status_code, error_code, error_message)
            return False, f"SMS send failed: {error_code} - {error_message}"
        except ValueError as e:
            logger.error("[marketing_sms] Failed to parse Twilio response: %s, Response text: %s", 
                        str(e), resp.text)
            return False, f"SMS send failed: {resp.status_code}"

    except Exception as e:
        logger.exception("[marketing_sms] Error sending to %s: %s", to_number, str(e))
        return False, f"SMS error: {str(e)}"

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
# Host normalization
# -------------------------------
def _normalize_host(h: str | None) -> str:
    if not h:
        return ""
    h = h.strip()
    if h.startswith("http://") or h.startswith("https://"):
        return urlparse(h).netloc
    return h


# -------------------------------
# Simple country detector from E.164 (+<cc>...)
# -------------------------------
_CC_MAP = {
    "91": "IN", "1": "US", "44": "GB", "61": "AU", "81": "JP", "49": "DE",
    "33": "FR", "39": "IT", "34": "ES", "971": "AE", "65": "SG", "852": "HK",
}
_CC_ORDER = sorted(_CC_MAP.keys(), key=len, reverse=True)


def _detect_country_e164(e164: str) -> Optional[str]:
    if not e164 or not e164.startswith("+"):
        return None
    digits = e164[1:]
    for cc in _CC_ORDER:
        if digits.startswith(cc):
            return _CC_MAP.get(cc)
    return None


# -------------------------------
# Telephony provider abstraction
# -------------------------------
class _TelephonyProvider:
    name: str
    def create_call(self, to_number: str, from_number: str, url: str, method: str = "GET") -> Tuple[bool, Optional[str], str]:
        raise NotImplementedError


class _TwilioProvider(_TelephonyProvider):
    name = "twilio"
    def __init__(self):
        self.sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.token = os.getenv("TWILIO_AUTH_TOKEN")
        self.from_number = os.getenv("TWILIO_FROM_NUMBER") or os.getenv("TWILIO_FROM")

    def create_call(self, to_number: str, from_number: str, url: str, method: str = "GET") -> Tuple[bool, Optional[str], str]:
        if not (self.sid and self.token and (from_number or self.from_number)):
            return (False, None, "Twilio credentials or FROM missing")
        _from = from_number or self.from_number
        try:
            resp = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls.json",
                auth=(self.sid, self.token),
                data={"To": to_number, "From": _from, "Url": url, "Method": method},
                timeout=20,
            )
            if resp.status_code in (200, 201):
                sid = resp.json().get("sid")
                return (True, sid, "")
            return (False, None, f"{resp.status_code} {resp.text}")
        except Exception as e:
            return (False, None, str(e))


class _SignalWireProvider(_TelephonyProvider):
    name = "signalwire"
    def __init__(self):
        self.project_id = os.getenv("SW_PROJECT_ID") or os.getenv("SIGNALWIRE_PROJECT_ID")
        self.api_token = os.getenv("SW_API_TOKEN") or os.getenv("SIGNALWIRE_API_TOKEN")
        self.space = os.getenv("SW_SPACE") or os.getenv("SIGNALWIRE_SPACE")
        self.from_number = os.getenv("SW_FROM_NUMBER") or os.getenv("SIGNALWIRE_FROM_NUMBER")
        if self.space and not self.space.endswith(".signalwire.com"):
            self.space = f"{self.space}.signalwire.com"

    def create_call(self, to_number: str, from_number: str, url: str, method: str = "GET") -> Tuple[bool, Optional[str], str]:
        if not (self.project_id and self.api_token and self.space and (from_number or self.from_number)):
            return (False, None, "SignalWire credentials/space or FROM missing")
        _from = from_number or self.from_number
        try:
            base = f"https://{self.space}/api/laml/2010-04-01/Accounts/{self.project_id}/Calls.json"
            resp = requests.post(
                base,
                auth=(self.project_id, self.api_token),
                data={"To": to_number, "From": _from, "Url": url, "Method": method},
                timeout=20,
            )
            if resp.status_code in (200, 201):
                sid = resp.json().get("sid") or resp.json().get("Sid")
                return (True, sid, "")
            return (False, None, f"{resp.status_code} {resp.text}")
        except Exception as e:
            return (False, None, str(e))


def _provider_by_name(name: str) -> _TelephonyProvider:
    n = (name or "").strip().lower()
    if n in ("signalwire", "sw", "signal_wire"):
        return _SignalWireProvider()
    return _TwilioProvider()


def _get_default_provider() -> _TelephonyProvider:
    return _provider_by_name((os.getenv("TELEPHONY_PROVIDER") or "twilio").strip().lower())


def _select_provider_for_number(to_number: str) -> _TelephonyProvider:
    """
    Country-based routing if enabled via env:
      - ENABLE_COUNTRY_ROUTING=1/true
      - ROUTE_PROVIDER_IN=twilio (default)
      - ROUTE_PROVIDER_DEFAULT=signalwire (default)
    """
    enabled = (os.getenv("ENABLE_COUNTRY_ROUTING") or "").lower() in ("1", "true", "yes")
    if not enabled:
        return _get_default_provider()
    iso = _detect_country_e164(to_number or "")
    if iso == "IN":
        return _provider_by_name(os.getenv("ROUTE_PROVIDER_IN", "twilio"))
    return _provider_by_name(os.getenv("ROUTE_PROVIDER_DEFAULT", "signalwire"))


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
    return q.order_by(models.Call.created_at.desc()).all()


# -------------------------------
# Create outbound call (country routing + PUBLIC_HOST)
# -------------------------------
@router.post("/outbound")
def outbound_call(request: Request, payload: Dict[str, Any]):
    body = payload or {}
    org_id = body.get("org_id")
    patient_id = body.get("patient_id")
    to_number = body.get("to_number")
    agent = body.get("agent") or "annie_RPM"
    from_number_override = body.get("from_number")

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

        public_host = _normalize_host(os.getenv("PUBLIC_HOST"))
        host = public_host if public_host else _normalize_host(request.url.netloc)
        twiml_url = f"https://{host}/api/calls/twiml/outbound/{call_id}?agent={html.escape(agent)}"

        provider = _select_provider_for_number(to_number)
        logger.info("[outbound] provider=%s to=%s url=%s", provider.name, to_number, twiml_url)

        ok, sid, err = provider.create_call(
            to_number=to_number,
            from_number=from_number_override or "",
            url=twiml_url,
            method="GET",
        )
        if ok and sid:
            new_call.twilio_call_sid = sid  # reuse column for either provider
            session.add(new_call)
            session.commit()
        else:
            logger.warning("[%s] create call failed: %s", provider.name, err)

        return {"call_id": call_id, "status": "initiated", "provider": provider.name}
    finally:
        session.close()


# -------------------------------
# TwiML/LaML for outbound
# -------------------------------
@router.api_route("/twiml/outbound/{call_id}", methods=["GET", "POST"])
async def twiml_outbound(call_id: int, request: Request, agent: Optional[str] = None):
    agent_name = request.query_params.get("agent") or agent or "annie_RPM"
    public_host = _normalize_host(os.getenv("PUBLIC_HOST"))
    host = public_host if public_host else _normalize_host(request.url.netloc)
    stream_url = f"wss://{host}/ws/{call_id}?agent={agent_name}"
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{stream_url.replace('"','&quot;')}"/>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


# -------------------------------
# Helper: persist single 'readings' row (delete old 'summary'/'readings')
# -------------------------------
def _persist_single_readings(session: Session, call: models.Call, parsed: Any):
    """
    Normalize `parsed` and persist exactly one row in readings table:
      - reading_type = 'readings'
      - value = JSON string: {"value": <to_store>}
    Deletes previous rows for this call with types 'summary' or 'readings' to keep single-row invariant.
    """
    try:
        # Determine what to store
        to_store = None
        if parsed is None:
            to_store = []
        elif isinstance(parsed, dict) and parsed.get("readings") is not None:
            to_store = parsed.get("readings")
        elif isinstance(parsed, (dict, list)):
            to_store = parsed
        else:
            # scalar or unknown -> wrap in list
            to_store = [parsed]

        # Normalize empty dict -> empty list for consistency
        if isinstance(to_store, dict) and not to_store:
            to_store = []

        # Delete any existing rows of type 'summary' or 'readings' for this call
        try:
            session.query(models.Reading).filter(models.Reading.call_id == call.id,
                                                 models.Reading.reading_type.in_(["summary", "readings"])
                                                 ).delete(synchronize_session=False)
            session.commit()
        except Exception:
            session.rollback()

        # Always persist a single 'readings' row (even if empty list)
        row_value = to_store
        # ensure JSON-serializable: wrap scalars into {"value": scalar} earlier; lists/dicts kept as-is
        if not isinstance(row_value, (dict, list)):
            row_value = {"value": row_value}

        now = datetime.utcnow()
        rd = models.Reading(
            patient_id=call.patient_id,
            call_id=call.id,
            reading_type="readings",
            value=_json.dumps({"value": row_value}),
            units=None,
            raw_text=str(row_value),
            recorded_at=call.end_time or call.start_time or now,
            created_at=now,
        )
        session.add(rd)
        session.commit()
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        logger.exception("persist_single_readings failed: %s", e)


# -------------------------------
# Complete call (persist readings only)
# -------------------------------
@router.post("/{call_id}/complete")
def complete_call(call_id: int):
    session = db.SessionLocal()
    # visibility: log entry immediately so we can see the endpoint was invoked
    logger.info("[complete_call] invoked with call_id=%s", call_id)
    try:
        call = session.query(models.Call).filter(models.Call.id == call_id).first()
        if not call:
            logger.warning("[complete_call] call not found: %s", call_id)
            raise HTTPException(status_code=404, detail="Call not found")

        # Log call details for debugging SMS flow
        logger.info("[complete_call] call found id=%s agent=%s status=%s patient_id=%s twilio_call_sid=%s",
                    getattr(call, 'id', None), getattr(call, 'agent', None), getattr(call, 'status', None),
                    getattr(call, 'patient_id', None), getattr(call, 'twilio_call_sid', None))
        # Also print to stdout to help capture logs in environments where logging handlers are not showing
        try:
            print(f"[complete_call] call={call.id} agent={call.agent} status={call.status} patient_id={call.patient_id} twilio_call_sid={call.twilio_call_sid}")
        except Exception:
            pass

        if call.status != "completed":
            call.end_time = call.end_time or datetime.utcnow()
            call.status = "completed"
            if call.start_time and call.end_time:
                try:
                    call.duration_seconds = int((call.end_time - call.start_time).total_seconds())
                except Exception:
                    call.duration_seconds = None
            session.add(call)
            session.commit()

        transcript_text = (call.transcript or "") + "\n" + (call.summary or "")
        parsed = {}
        try:
            from app.services import openai_client
            parsed = openai_client.extract_readings_from_transcript(transcript_text) or {}
        except Exception as e:
            logger.exception("openai extraction failed: %s", e)

        # Save parsed summary into call.summary (optional) but DO NOT create 'summary' reading rows.
        try:
            if isinstance(parsed, dict) and parsed.get("summary"):
                call.summary = ((call.summary or "") + "\n[auto_summary] " + str(parsed["summary"]))[:8000]
                session.add(call)
                session.commit()
        except Exception as e:
            logger.exception("saving parsed summary failed: %s", e)

        # Persist parsed readings into a single readings row
        try:
            _persist_single_readings(session, call, parsed)
        except Exception as e:
            logger.exception("persisting single readings failed: %s", e)

        # For wellcare_marketing agent, send SMS follow-up
        logger.info("[marketing] Checking agent type: %s", call.agent)
        if call.agent == "wellcare_marketing" and call.patient_id:
            try:
                patient = session.query(models.Patient).filter(models.Patient.id == call.patient_id).first()
                if patient and patient.phone:
                    logger.info("[marketing] Sending follow-up SMS to patient %s (agent: %s)", patient.id, call.agent)
                    ok, err = send_marketing_sms(patient.phone)
                    if not ok:
                        logger.error("[marketing] SMS failed for agent %s: %s", call.agent, err)
                else:
                    logger.warning("[marketing] Patient %s has no phone number (agent: %s)", call.patient_id, call.agent)
            except Exception as e:
                logger.exception("[marketing] Error in SMS flow: %s", e)

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
# Readings for a call (persist if missing = default)
# -------------------------------
@router.get("/{call_id}/readings")
def get_call_readings(call_id: int, persist_if_missing: bool = Query(True)):
    session = db.SessionLocal()
    try:
        rows = session.query(models.Reading).filter(models.Reading.call_id == call_id).all()
        if rows:
            out = {}
            for r in rows:
                try:
                    val = _json.loads(r.value) if r.value else None
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
            return {"call_id": call_id, "from_db": True, "readings": out}

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

        if persist_if_missing:
            try:
                _persist_single_readings(session, call, parsed)
            except Exception as e:
                logger.exception("persist parsed readings (get_call_readings) failed: %s", e)

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

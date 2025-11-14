from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime
import json as _json

from app import db, models, schemas

router = APIRouter(prefix="/api/emergency", tags=["emergency"])

def get_db():
    session = db.SessionLocal()
    try:
        yield session
    finally:
        session.close()

@router.post("/event", response_model=schemas.EmergencyEventOut)
def create_emergency_event(event: schemas.EmergencyEventCreate, db_session: Session = Depends(get_db)):
    # Validate patient
    patient = db_session.query(models.Patient).filter(models.Patient.id == event.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail=f"Patient {event.patient_id} not found")

    # Get org_id from patient if not provided
    org_id = getattr(patient, 'org_id', None)

    # Create event
    emerg_event = models.EmergencyEvent(
        call_id=event.call_id,
        patient_id=event.patient_id,
        org_id=org_id,
        severity=event.severity,
        signal_text=event.signal_text,
        detector_info=_json.dumps(event.detector_info) if event.detector_info else None,
        detected_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    db_session.add(emerg_event)

    # Update patient emergency flag and last_emergency_at
    patient.emergency_flag = 1
    patient.last_emergency_at = emerg_event.detected_at
    db_session.add(patient)

    try:
        db_session.commit()
        db_session.refresh(emerg_event)
    except Exception as e:
        db_session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create emergency event: {str(e)}")

    # Return with detector_info as dict
    emerg_event.detector_info = _json.loads(emerg_event.detector_info) if emerg_event.detector_info else None
    return emerg_event

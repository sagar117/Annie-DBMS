# app/api/patients.py
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import db, models, schemas

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/patients", tags=["patients"])


def get_db():
    dbs = db.SessionLocal()
    try:
        yield dbs
    finally:
        dbs.close()


@router.post("/", response_model=schemas.PatientOut)
def create_patient(p_in: schemas.PatientCreate, db_session: Session = Depends(get_db)):
    org = db_session.query(models.Organization).filter(models.Organization.id == p_in.org_id).first()
    if not org:
        raise HTTPException(status_code=400, detail="Org not found")
    patient = models.Patient(
        org_id=p_in.org_id,
        patient_id=p_in.patient_id,
        name=p_in.name,
        fname=p_in.fname,
        lname=p_in.lname,
        phone=p_in.phone,
        dob=p_in.dob,
        email=p_in.email,
    )
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return patient


@router.get("/", response_model=List[schemas.PatientOut])
def list_patients(
    org_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
):
    """
    List patients. Optional filter by org_id and simple pagination.
    """
    session = db.SessionLocal()
    try:
        q = session.query(models.Patient)
        if org_id is not None:
            q = q.filter(models.Patient.org_id == org_id)
        items = q.offset((page - 1) * limit).limit(limit).all()
        return items
    finally:
        session.close()


@router.get("/{patient_id}/readings", response_model=List[schemas.ReadingOut])
def get_readings(
    patient_id: int,
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    reading_type: Optional[str] = None,
    db_session: Session = Depends(get_db),
):
    q = db_session.query(models.Reading).filter(models.Reading.patient_id == patient_id)
    if reading_type:
        q = q.filter(models.Reading.reading_type.ilike(f"%{reading_type}%"))
    if from_date:
        q = q.filter(models.Reading.recorded_at >= from_date)
    if to_date:
        q = q.filter(models.Reading.recorded_at <= to_date)
    rows = q.order_by(models.Reading.recorded_at.desc()).all()
    return rows


@router.get("/{patient_id}", response_model=schemas.PatientOut)
def get_patient(patient_id: int):
    session = db.SessionLocal()
    try:
        p = session.query(models.Patient).filter(models.Patient.id == patient_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Patient not found")
        return p
    finally:
        session.close()


@router.put("/{patient_id}", response_model=schemas.PatientOut)
def update_patient(patient_id: int, payload: Dict[str, Any], db_session: Session = Depends(get_db)):

    patient = db_session.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    allowed = {"patient_id", "name","fname","lname", "phone", "dob","address","email"}
    updated = False
    for k, v in payload.items():
        if k in allowed:
            setattr(patient, k, v)
            updated = True

    if updated:
        db_session.add(patient)
        db_session.commit()
        db_session.refresh(patient)

    return patient



@router.get("/{patient_id}/daily/{reading_date}")
def get_patient_daily_reading(patient_id: int, reading_date: str, db_session: Session = Depends(get_db)):
    from app.models import PatientDailyReading
    try:
        y, m, d = map(int, reading_date.split("-"))
        dt = datetime(y, m, d).date()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    row = (
        db_session.query(PatientDailyReading)
        .filter(PatientDailyReading.patient_id == patient_id,
                PatientDailyReading.reading_date == dt)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="No daily reading for this date")

    return {
        "patient_id": patient_id,
        "reading_date": row.reading_date.isoformat(),
        "bp": {"systolic": row.bp_systolic, "diastolic": row.bp_diastolic},
        "pulse": row.pulse,
        "glucose": row.glucose,
        "weight": row.weight,
        "source_call_id": row.source_call_id,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


"""
@router.put("/{patient_id}", response_model=schemas.PatientOut)
def update_patient(patient_id: int, payload: schemas.PatientUpdate, db_session: Session = Depends(get_db)):
    patient = db_session.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if payload.name is not None:
        patient.name = payload.name
    if payload.phone is not None:
        patient.phone = payload.phone
    if payload.dob is not None:
        patient.dob = payload.dob  # dob is already `date` if schema defines it as `date`

    db_session.commit()
    db_session.refresh(patient)
    return patient

"""

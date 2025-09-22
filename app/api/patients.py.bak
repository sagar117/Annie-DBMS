from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from app import db, models, schemas

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
    patient = models.Patient(org_id=p_in.org_id, patient_id=p_in.patient_id, name=p_in.name, phone=p_in.phone, dob=p_in.dob)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return patient

@router.get("/{patient_id}/readings", response_model=List[schemas.ReadingOut])
def get_readings(patient_id: int, from_date: Optional[datetime] = Query(None), to_date: Optional[datetime] = Query(None),
                 reading_type: Optional[str] = None, db_session: Session = Depends(get_db)):
    q = db_session.query(models.Reading).filter(models.Reading.patient_id == patient_id)
    if reading_type:
        q = q.filter(models.Reading.reading_type.ilike(f"%{reading_type}%"))
    if from_date:
        q = q.filter(models.Reading.recorded_at >= from_date)
    if to_date:
        q = q.filter(models.Reading.recorded_at <= to_date)
    rows = q.order_by(models.Reading.recorded_at.desc()).all()
    return rows

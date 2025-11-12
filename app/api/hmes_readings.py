from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, date as _date
import json as _json
import logging

from app import db, models, schemas

router = APIRouter(prefix="/api/hmes_readings", tags=["hmes_readings"])
logger = logging.getLogger(__name__)


def get_db():
    session = db.SessionLocal()
    try:
        yield session
    finally:
        session.close()


# -------------------------------
# Create single HMES reading
# -------------------------------
@router.post("/", response_model=schemas.HMESReadingOut)
def create_hmes_reading(reading: schemas.HMESReadingCreate, db_session: Session = Depends(get_db)):
    """
    Create a single HMES reading for a patient.
    """
    # Verify org exists
    org = db_session.query(models.Organization).filter(models.Organization.id == reading.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail=f"Organization {reading.org_id} not found")
    
    # Verify patient exists
    patient = db_session.query(models.Patient).filter(models.Patient.id == reading.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail=f"Patient {reading.patient_id} not found")
    
    # Convert readings to JSON string
    readings_json = _json.dumps(reading.readings.dict())
    
    hmes_reading = models.HMESReading(
        org_id=reading.org_id,
        patient_id=reading.patient_id,
        readings_date=reading.readings_date,
        readings=readings_json
    )
    
    db_session.add(hmes_reading)
    db_session.commit()
    db_session.refresh(hmes_reading)
    
    # Parse readings back to dict for response
    hmes_reading.readings = _json.loads(hmes_reading.readings)
    
    return hmes_reading


# -------------------------------
# Bulk upload HMES readings
# -------------------------------
@router.post("/bulk", response_model=dict)
def bulk_create_hmes_readings(bulk_data: schemas.HMESReadingBulkCreate, db_session: Session = Depends(get_db)):
    """
    Bulk upload multiple HMES readings.
    Returns count of successful and failed inserts.
    """
    success_count = 0
    failed_count = 0
    errors = []
    
    for idx, reading in enumerate(bulk_data.readings):
        try:
            # Verify org exists
            org = db_session.query(models.Organization).filter(models.Organization.id == reading.org_id).first()
            if not org:
                failed_count += 1
                errors.append(f"Row {idx}: Organization {reading.org_id} not found")
                continue
            
            # Verify patient exists
            patient = db_session.query(models.Patient).filter(models.Patient.id == reading.patient_id).first()
            if not patient:
                failed_count += 1
                errors.append(f"Row {idx}: Patient {reading.patient_id} not found")
                continue
            
            # Convert readings to JSON string
            readings_json = _json.dumps(reading.readings.dict())
            
            hmes_reading = models.HMESReading(
                org_id=reading.org_id,
                patient_id=reading.patient_id,
                readings_date=reading.readings_date,
                readings=readings_json
            )
            
            db_session.add(hmes_reading)
            success_count += 1
            
        except Exception as e:
            failed_count += 1
            errors.append(f"Row {idx}: {str(e)}")
            logger.error(f"Failed to insert HMES reading at index {idx}: {e}")
    
    try:
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        raise HTTPException(status_code=500, detail=f"Bulk insert failed: {str(e)}")
    
    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "errors": errors if errors else None
    }


# -------------------------------
# Get HMES reading by ID
# -------------------------------
@router.get("/{reading_id}", response_model=schemas.HMESReadingOut)
def get_hmes_reading(reading_id: int, db_session: Session = Depends(get_db)):
    """
    Get a single HMES reading by ID.
    """
    reading = db_session.query(models.HMESReading).filter(models.HMESReading.id == reading_id).first()
    if not reading:
        raise HTTPException(status_code=404, detail="HMES reading not found")
    
    # Parse readings JSON
    reading.readings = _json.loads(reading.readings)
    
    return reading


# -------------------------------
# List HMES readings by patient
# -------------------------------
@router.get("/patient/{patient_id}", response_model=List[schemas.HMESReadingOut])
def list_hmes_readings_by_patient(
    patient_id: int,
    date: Optional[_date] = Query(None, description="Filter by specific date (YYYY-MM-DD)"),
    from_date: Optional[datetime] = Query(None, description="readings_date >= (ISO datetime)"),
    to_date: Optional[datetime] = Query(None, description="readings_date <= (ISO datetime)"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db_session: Session = Depends(get_db)
):
    """
    Get HMES readings for a patient with optional date filtering and pagination.
    """
    # Verify patient exists
    patient = db_session.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
    
    q = db_session.query(models.HMESReading).filter(models.HMESReading.patient_id == patient_id)
    
    # Apply date filters
    if date:
        start = datetime.combine(date, datetime.min.time())
        end = datetime.combine(date, datetime.max.time())
        q = q.filter(models.HMESReading.readings_date >= start, models.HMESReading.readings_date <= end)
    
    if from_date:
        q = q.filter(models.HMESReading.readings_date >= from_date)
    if to_date:
        q = q.filter(models.HMESReading.readings_date <= to_date)
    
    # Order by most recent first
    rows = (
        q.order_by(models.HMESReading.readings_date.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    
    # Parse readings JSON for each row
    for row in rows:
        row.readings = _json.loads(row.readings)
    
    return rows


# -------------------------------
# Update HMES reading
# -------------------------------
@router.put("/{reading_id}", response_model=schemas.HMESReadingOut)
def update_hmes_reading(
    reading_id: int,
    reading_update: schemas.HMESReadingUpdate,
    db_session: Session = Depends(get_db)
):
    """
    Update an existing HMES reading.
    """
    reading = db_session.query(models.HMESReading).filter(models.HMESReading.id == reading_id).first()
    if not reading:
        raise HTTPException(status_code=404, detail="HMES reading not found")
    
    # Update fields if provided
    if reading_update.readings_date is not None:
        reading.readings_date = reading_update.readings_date
    
    if reading_update.readings is not None:
        reading.readings = _json.dumps(reading_update.readings.dict())
    
    reading.updated_at = datetime.utcnow()
    
    db_session.add(reading)
    db_session.commit()
    db_session.refresh(reading)
    
    # Parse readings back to dict for response
    reading.readings = _json.loads(reading.readings)
    
    return reading


# -------------------------------
# Delete HMES reading
# -------------------------------
@router.delete("/{reading_id}")
def delete_hmes_reading(reading_id: int, db_session: Session = Depends(get_db)):
    """
    Delete an HMES reading by ID.
    """
    reading = db_session.query(models.HMESReading).filter(models.HMESReading.id == reading_id).first()
    if not reading:
        raise HTTPException(status_code=404, detail="HMES reading not found")
    
    db_session.delete(reading)
    db_session.commit()
    
    return {"message": "HMES reading deleted successfully", "id": reading_id}

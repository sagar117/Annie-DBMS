# app/api/patients_import.py

import random
import datetime
import pandas as pd
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from app import db, models

router = APIRouter(prefix="/api/patients", tags=["patients-import"])

def get_db():
    dbs = db.SessionLocal()
    try:
        yield dbs
    finally:
        dbs.close()


def generate_patient_id(org_id: int) -> str:
    """Generate patient ID = <org_id><YYMMDD><4-digit random>"""
    today = datetime.datetime.utcnow().strftime("%y%m%d")
    rand = str(random.randint(1000, 9999))
    return f"{org_id}{today}{rand}"


@router.post("/import")
async def import_patients(
    org_id: int = Query(..., description="Organization ID"),
    file: UploadFile = File(...),
    dry_run: bool = Query(False, description="If true, validate but don't insert"),
    db_session: Session = Depends(get_db),
):
    """
    Import patients from Excel sheet.
    Expected headers: First Name, Last Name, phone, dob, email
    """
    try:
        df = pd.read_excel(file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read Excel: {e}")

    required_headers = ["First Name", "Last Name", "phone", "dob", "email"]
    for col in required_headers:
        if col not in df.columns:
            raise HTTPException(status_code=400, detail=f"Missing required column: {col}")

    inserted, skipped, errors = 0, 0, []

    for idx, row in df.iterrows():
        try:
            fname = str(row["First Name"]).strip() if pd.notna(row["First Name"]) else None
            lname = str(row["Last Name"]).strip() if pd.notna(row["Last Name"]) else None
            phone = str(row["phone"]).strip() if pd.notna(row["phone"]) else None
            dob_val = None
            if pd.notna(row["dob"]):
                if isinstance(row["dob"], (datetime.date, datetime.datetime)):
                    dob_val = row["dob"]
                else:
                    dob_val = datetime.datetime.strptime(str(row["dob"]), "%Y-%m-%d").date()
            email = str(row["email"]).strip() if pd.notna(row["email"]) else None

            if not fname or not lname or not phone:
                skipped += 1
                continue

            gen_pid = generate_patient_id(org_id)
            full_name = f"{fname} {lname}".strip()

            patient = models.Patient(
                org_id=org_id,
                patient_id=gen_pid,
                fname=fname,
                lname=lname,
                name=full_name,
                phone=phone,
                dob=dob_val,
                email=email,
            )

            if not dry_run:
                db_session.add(patient)
                db_session.commit()
                db_session.refresh(patient)
                inserted += 1
        except Exception as e:
            errors.append({"row": idx + 2, "error": str(e)})
            skipped += 1

    return {
        "org_id": org_id,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
        "total_rows": len(df),
        "required_headers": required_headers,
        "id_pattern": "<org_id><YYMMDD><4-digit random>",
    }

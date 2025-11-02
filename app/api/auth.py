# app/api/auth.py
import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
from sqlalchemy.orm import Session
from app import db, models, schemas

router = APIRouter(prefix="/api/auth", tags=["auth"])

def get_db():
    d = db.SessionLocal()
    try:
        yield d
    finally:
        d.close()

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    ok: bool
    org: schemas.OrgOut
    # Optional role info when a user (Admin/Nurse) logs in
    role_id: Optional[int] = None
    role: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db_session: Session = Depends(get_db)):
    # 1) Try organization-level login (org email)
    org = db_session.query(models.Organization).filter(models.Organization.email == req.email).first()
    if org and org.password:
        try:
            if bcrypt.checkpw(req.password.encode("utf-8"), org.password.encode("utf-8")):
                return {"ok": True, "org": org}
        except Exception:
            # Fall through to role check
            pass

    # 2) Fallback: try Role (Admin/Nurse) login by email within any org
    role_row = db_session.query(models.Role).filter(models.Role.email == req.email).first()
    if role_row and role_row.password:
        try:
            if bcrypt.checkpw(req.password.encode("utf-8"), role_row.password.encode("utf-8")):
                # Load organization for this role
                org_for_role = db_session.query(models.Organization).filter(models.Organization.id == role_row.org_id).first()
                if not org_for_role:
                    raise HTTPException(status_code=401, detail="Invalid credentials")
                return {
                    "ok": True,
                    "org": org_for_role,
                    "role_id": role_row.id,
                    "role": role_row.role,
                    "first_name": role_row.first_name,
                    "last_name": role_row.last_name,
                }
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid credentials")

    # If neither matched, reject
    raise HTTPException(status_code=401, detail="Invalid credentials")

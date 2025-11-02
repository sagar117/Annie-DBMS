# app/api/auth.py
import bcrypt
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
from sqlalchemy.orm import Session
from app import db, models, schemas

logger = logging.getLogger(__name__)

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
    logger.info("[login] Attempting login for email=%s", req.email)
    
    # 1) Try organization-level login (org email)
    org = db_session.query(models.Organization).filter(models.Organization.email == req.email).first()
    if org and org.password:
        try:
            if bcrypt.checkpw(req.password.encode("utf-8"), org.password.encode("utf-8")):
                logger.info("[login] Successful organization login for email=%s", req.email)
                return {"ok": True, "org": org}
        except Exception:
            logger.warning("[login] Organization bcrypt check failed for email=%s", req.email)
            pass

    # 2) Try Role (Admin/Nurse) login by email
    role_row = db_session.query(models.Role).filter(models.Role.email == req.email).first()
    if not role_row:
        logger.warning("[login] No Role found for email=%s", req.email)
    elif not role_row.password:
        logger.warning("[login] Role found but no password set for email=%s", req.email)

    if role_row and role_row.password:
        try:
            plain = req.password.encode("utf-8")
            hashed = role_row.password
            # Handle both bcrypt-hashed and plain text passwords for now
            is_match = False
            try:
                # First try bcrypt check (if password was properly hashed)
                is_match = bcrypt.checkpw(plain, hashed.encode("utf-8"))
            except Exception as e:
                logger.warning("[login] bcrypt check failed, trying plain text: %s", e)
                # Fallback: plain text comparison (temporary!)
                is_match = (req.password == role_row.password)
            
            if is_match:
                # Load organization for this role
                org_for_role = db_session.query(models.Organization).filter(models.Organization.id == role_row.org_id).first()
                if not org_for_role:
                    logger.error("[login] Role %s org_id=%s not found!", role_row.id, role_row.org_id)
                    raise HTTPException(status_code=401, detail="Invalid credentials")

                logger.info("[login] Successful role login for email=%s role=%s", req.email, role_row.role)
                return {
                    "ok": True,
                    "org": org_for_role,
                    "role_id": role_row.id,
                    "role": role_row.role,
                    "first_name": role_row.first_name,
                    "last_name": role_row.last_name,
                }
        except Exception as e:
            logger.exception("[login] Role auth failed for email=%s: %s", req.email, str(e))
            raise HTTPException(status_code=401, detail="Invalid credentials")

    # If neither matched, reject
    logger.warning("[login] All auth methods failed for email=%s", req.email)
    raise HTTPException(status_code=401, detail="Invalid credentials")

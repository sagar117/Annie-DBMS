# app/api/auth.py
import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
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

@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db_session: Session = Depends(get_db)):
    org = db_session.query(models.Organization).filter(models.Organization.email == req.email).first()
    if not org or not org.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    try:
        if not bcrypt.checkpw(req.password.encode("utf-8"), org.password.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {"ok": True, "org": org}

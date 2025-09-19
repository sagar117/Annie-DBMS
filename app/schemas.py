from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class OrgCreate(BaseModel):
    name: str
    address: Optional[str] = None
    logo: Optional[str] = None

class OrgOut(BaseModel):
    id: int
    name: str
    address: Optional[str]
    logo: Optional[str]

    class Config:
        orm_mode = True

class PatientCreate(BaseModel):
    org_id: int
    patient_id: str
    name: str
    phone: Optional[str] = None
    dob: Optional[datetime] = None

class PatientOut(BaseModel):
    id: int
    org_id: int
    patient_id: str
    name: str
    phone: Optional[str]
    dob: Optional[datetime]

    class Config:
        orm_mode = True

class CallCreate(BaseModel):
    org_id: int
    patient_id: Optional[int] = None
    script_agent: Optional[str] = None  # agent name (maps to prompts)
    metadata: Optional[dict] = {}

class CallOut(BaseModel):
    id: int
    org_id: int
    patient_id: Optional[int]
    status: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    duration_seconds: Optional[int]
    transcript: Optional[str]
    summary: Optional[str]
    twilio_call_sid: Optional[str] = None

    class Config:
        orm_mode = True

class ReadingOut(BaseModel):
    id: int
    patient_id: int
    call_id: Optional[int]
    reading_type: str
    value: str
    units: Optional[str]
    recorded_at: Optional[datetime]
    raw_text: Optional[str]

    class Config:
        orm_mode = True

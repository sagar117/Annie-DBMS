from pydantic import BaseModel,EmailStr
from typing import Optional, List
from datetime import datetime

class OrgCreate(BaseModel):
    name: str
    address: Optional[str] = None
    logo: Optional[str] = None
    password: Optional[str] = None
    email: Optional[EmailStr] = None

class OrgOut(BaseModel):
    id: int
    name: str
    address: Optional[str]
    logo: Optional[str]
    email: Optional[EmailStr] = None

    class Config:
        orm_mode = True

class PatientCreate(BaseModel):
    org_id: int
    patient_id: str
    fname:Optional[str]
    lname: Optional[str]
    name: str
    phone: Optional[str] = None
    dob: Optional[datetime] = None
    email: str | None = None 
    caregiver_name: Optional[str] = None
    caregiver_email: Optional[EmailStr] = None
    caregiver_phone: Optional[str] = None

class PatientOut(BaseModel):
    id: int
    org_id: int
    patient_id: str
    name: str
    fname: str
    lname: str
    phone: Optional[str]
    dob: Optional[datetime]
    email: str | None = None 
    caregiver_name: Optional[str] = None
    caregiver_email: Optional[EmailStr] = None
    caregiver_phone: Optional[str] = None
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
    agent: Optional[str] 
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


class RoleCreate(BaseModel):
    org_id: int
    first_name: str
    last_name: Optional[str] = None
    role: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: Optional[str] = None
    address: Optional[str] = None


class RoleOut(BaseModel):
    id: int
    org_id: int
    first_name: str
    last_name: Optional[str]
    role: str
    email: Optional[EmailStr]
    phone: Optional[str]
    address: Optional[str]
    created_at: Optional[datetime]

    class Config:
        orm_mode = True


# HMES Reading Schemas
class HMESReadingsData(BaseModel):
    steps: Optional[int] = None
    heart_rate: Optional[int] = None
    blood_oxygen: Optional[int] = None
    sleep: Optional[float] = None  # hours


class HMESReadingCreate(BaseModel):
    org_id: int
    patient_id: int
    readings_date: datetime
    readings: HMESReadingsData


class HMESReadingUpdate(BaseModel):
    readings_date: Optional[datetime] = None
    readings: Optional[HMESReadingsData] = None


class HMESReadingOut(BaseModel):
    id: int
    org_id: int
    patient_id: int
    readings_date: datetime
    readings: dict  # JSON data
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


class HMESReadingBulkCreate(BaseModel):
    readings: List[HMESReadingCreate]


class EmergencyEventCreate(BaseModel):
    call_id: int | None = None
    patient_id: int
    severity: str
    signal_text: str | None = None
    detector_info: dict | None = None

class EmergencyEventOut(BaseModel):
    id: int
    call_id: int | None
    patient_id: int
    severity: str | None
    detected_at: datetime
    signal_text: str | None
    detector_info: dict | None
    created_at: datetime

    class Config:
        orm_mode = True

from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from .db import Base
from sqlalchemy.sql import func

class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=True)
    password = Column(String, nullable=True)
    email = Column(String, nullable=True, index=True)  # unique via index created above
    logo = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    patients = relationship("Patient", back_populates="org")
    calls = relationship("Call", back_populates="org")
    # Roles (users) belonging to this organization (e.g., Admin, Nurse)
    roles = relationship("Role", back_populates="org")

class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(String, nullable=False, unique=True)
    fname = Column(String, nullable = True)
    lname = Column(String, nullable = True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    # Caregiver contact info
    caregiver_name = Column(String, nullable=True)
    caregiver_email = Column(String, nullable=True, index=True)
    caregiver_phone = Column(String, nullable=True)
    dob = Column(DateTime, nullable=True)
    email = Column(String, nullable=True, index=True)  # <-- NEW


    emergency_flag = Column(Integer, default=0)
    last_emergency_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    org = relationship("Organization", back_populates="patients")
    calls = relationship("Call", back_populates="patient")
    readings = relationship("Reading", back_populates="patient")
    hmes_readings = relationship("HMESReading", back_populates="patient")

# EmergencyEvent model for emergency_events table
class EmergencyEvent(Base):
    __tablename__ = "emergency_events"
    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(Integer, ForeignKey("calls.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    severity = Column(String, nullable=True)  # critical | high | medium | low
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    signal_text = Column(Text, nullable=True)  # excerpt from transcript or model output
    detector_info = Column(String, nullable=True)  # JSON: {model:, score:, rule:}
    created_at = Column(DateTime, default=datetime.utcnow)

    call = relationship("Call")
    patient = relationship("Patient")

    org = relationship("Organization", back_populates="patients")
    calls = relationship("Call", back_populates="patient")
    readings = relationship("Reading", back_populates="patient")
    hmes_readings = relationship("HMESReading", back_populates="patient")

class Call(Base):
    __tablename__ = "calls"
    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    deepgram_call_id = Column(String, nullable=True)
    twilio_call_sid = Column(String, nullable=True)
    status = Column(String, default="queued")  # queued, in_progress, completed, failed
    start_time = Column(DateTime, nullable=True)
    agent = Column(String, nullable=True)
    end_time = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    transcript = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    org = relationship("Organization", back_populates="calls")
    patient = relationship("Patient", back_populates="calls")
    readings = relationship("Reading", back_populates="call")

class Reading(Base):
    __tablename__ = "readings"
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    call_id = Column(Integer, ForeignKey("calls.id"), nullable=True)
    reading_type = Column(String, nullable=False)  # BP, pulse, glucose, weight
    value = Column(String, nullable=False)  # store as JSON string for complex types
    units = Column(String, nullable=True)
    recorded_at = Column(DateTime, nullable=True)
    raw_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="readings")
    call = relationship("Call", back_populates="readings")


class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=True)
    role = Column(String, nullable=False)  # e.g., 'Admin', 'Nurse'
    email = Column(String, nullable=True, index=True)
    phone = Column(String, nullable=True)
    password = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    org = relationship("Organization", back_populates="roles")



class PatientDailyReading(Base):
    __tablename__ = "patient_daily_readings"
    id = Column(Integer, primary_key=True, index=True)

    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)

    # one row per patient per calendar day (UTC or your app’s canonical TZ)
    reading_date = Column(Date, nullable=False)

    # normalized fields (NULL when not reported)
    bp_systolic = Column(Integer, nullable=True)
    bp_diastolic = Column(Integer, nullable=True)
    pulse = Column(Integer, nullable=True)
    glucose = Column(Integer, nullable=True)
    weight = Column(Integer, nullable=True)

    # traceability
    source_call_id = Column(Integer, ForeignKey("calls.id"), nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("patient_id", "reading_date", name="uq_patient_daily_reading"),
    )


class SchedulerSetting(Base):
    __tablename__ = "scheduler_setting"
    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, unique=True)
    # Store hours in EST local as integer hours (0-23)
    start_time = Column(Integer, nullable=True)
    end_time = Column(Integer, nullable=True)
    # callback interval in minutes
    callback_interval = Column(Integer, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # optional relationship not strictly required
    # org = relationship("Organization", back_populates="scheduler_setting")


class HMESReading(Base):
    __tablename__ = "hmes_readings"
    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    readings_date = Column(DateTime, nullable=False, index=True)
    readings = Column(String, nullable=False)  # JSON string with Steps, Heart Rate, Blood Oxygen, Sleep
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    org = relationship("Organization")
    patient = relationship("Patient", back_populates="hmes_readings")

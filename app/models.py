from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .db import Base

class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=True)
    logo = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    patients = relationship("Patient", back_populates="org")
    calls = relationship("Call", back_populates="org")

class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    dob = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    org = relationship("Organization", back_populates="patients")
    calls = relationship("Call", back_populates="patient")
    readings = relationship("Reading", back_populates="patient")

class Call(Base):
    __tablename__ = "calls"
    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    deepgram_call_id = Column(String, nullable=True)
    twilio_call_sid = Column(String, nullable=True)
    status = Column(String, default="queued")  # queued, in_progress, completed, failed
    start_time = Column(DateTime, nullable=True)
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

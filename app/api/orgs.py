from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from app import db, models, schemas
from typing import Optional

router = APIRouter(prefix="/api/orgs", tags=["orgs"])

def get_db():
    dbs = db.SessionLocal()
    try:
        yield dbs
    finally:
        dbs.close()

@router.post("/", response_model=schemas.OrgOut)
def create_org(payload: schemas.OrgCreate, db_session: Session = Depends(get_db)):
    org = models.Organization(name=payload.name, address=payload.address, logo=payload.logo)
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    return org

@router.get("/{org_id}/stats")
def org_stats(org_id: int, from_date: Optional[datetime] = Query(None), to_date: Optional[datetime] = Query(None),
              db_session: Session = Depends(get_db)):
    org = db_session.query(models.Organization).filter(models.Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    q = db_session.query(models.Call).filter(models.Call.org_id == org_id)
    if from_date:
        q = q.filter(models.Call.created_at >= from_date)
    if to_date:
        q = q.filter(models.Call.created_at <= to_date)
    calls = q.all()
    total_calls = len(calls)
    total_duration = sum((c.duration_seconds or 0) for c in calls)
    avg_duration = (total_duration / total_calls) if total_calls else 0
    statuses = {}
    for c in calls:
        statuses[c.status] = statuses.get(c.status, 0) + 1
    return {
        "org_id": org_id,
        "total_calls": total_calls,
        "total_duration_seconds": total_duration,
        "average_call_duration_seconds": avg_duration,
        "by_status": statuses
    }

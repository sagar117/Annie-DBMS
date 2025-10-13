# app/api/analytics.py
import logging
import os
from datetime import date as _date, datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])

MINUTES_PER_PATIENT_MONTH = int(os.getenv("MINUTES_PER_PATIENT_MONTH", "45"))  # set to 245 in env if desired


def get_db():
    s = db.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------- existing endpoints (unchanged) ----------------
@router.get("/readings-collected")
def readings_collected(
    org_id: int = Query(...),
    date: Optional[_date] = Query(None),
    date_from: Optional[_date] = Query(None),
    date_to: Optional[_date] = Query(None),
    db_session: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    if date and (date_from or date_to):
        raise HTTPException(status_code=400, detail="Use either ?date= or (?date_from=&date_to=), not both.")

    base_sql = """
        SELECT date(c.created_at) AS day, c.patient_id
        FROM calls c
        WHERE c.org_id = :org_id
          AND EXISTS (SELECT 1 FROM readings r WHERE r.call_id = c.id)
    """
    params = {"org_id": org_id}

    if date:
        base_sql += " AND date(c.created_at) = :d"
        params["d"] = date.isoformat()
    else:
        if date_from:
            base_sql += " AND date(c.created_at) >= :df"
            params["df"] = date_from.isoformat()
        if date_to:
            base_sql += " AND date(c.created_at) <= :dt"
            params["dt"] = date_to.isoformat()

    final_sql = f"""
        WITH per_call AS (
            {base_sql}
        )
        SELECT day, COUNT(DISTINCT patient_id) AS patients_with_readings
        FROM per_call
        GROUP BY day
        ORDER BY day ASC
    """
    rows = db_session.execute(text(final_sql), params).mappings().all()
    return [{"date": r["day"], "patients_with_readings": r["patients_with_readings"]} for r in rows]


@router.get("/completed-calls")
def completed_calls(
    org_id: int = Query(...),
    date: Optional[_date] = Query(None),
    date_from: Optional[_date] = Query(None),
    date_to: Optional[_date] = Query(None),
    db_session: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    if date and (date_from or date_to):
        raise HTTPException(status_code=400, detail="Use either ?date= or (?date_from=&date_to=), not both.")

    base_sql = """
        SELECT date(c.created_at) AS day, c.patient_id
        FROM calls c
        WHERE c.org_id = :org_id
          AND c.status = 'completed'
    """
    params = {"org_id": org_id}

    if date:
        base_sql += " AND date(c.created_at) = :d"
        params["d"] = date.isoformat()
    else:
        if date_from:
            base_sql += " AND date(c.created_at) >= :df"
            params["df"] = date_from.isoformat()
        if date_to:
            base_sql += " AND date(c.created_at) <= :dt"
            params["dt"] = date_to.isoformat()

    final_sql = f"""
        WITH per_call AS (
            {base_sql}
        )
        SELECT day, COUNT(DISTINCT patient_id) AS patients_with_completed_calls
        FROM per_call
        GROUP BY day
        ORDER BY day ASC
    """
    rows = db_session.execute(text(final_sql), params).mappings().all()
    return [{"date": r["day"], "patients_with_completed_calls": r["patients_with_completed_calls"]} for r in rows]


# ---------------- helper ----------------
def _daterange(start: _date, end: _date):
    cur = start
    while cur <= end:
        yield cur
        cur = cur + timedelta(days=1)


# ---------------- combined dashboard endpoint (ENHANCED) ----------------
@router.get("/dashboard_analytics")
@router.get("/dashoard_analytics")  # alias for common typo
def dashboard_analytics(
    org_id: int = Query(..., description="Organization id"),
    date: Optional[_date] = Query(None, description="Single day (YYYY-MM-DD)"),
    date_from: Optional[_date] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[_date] = Query(None, description="End date inclusive (YYYY-MM-DD)"),
    db_session: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Returns:
      {
        "org_id": ...,
        "date_range": {"from": "...", "to": "..."},
        "totals": {
            "total_patients": N,
            "total_minutes_this_month": X,
            "minutes_cap_this_month": total_patients * MINUTES_PER_PATIENT_MONTH,
            "minutes_per_patient_month": MINUTES_PER_PATIENT_MONTH
        },
        "daily": [
          {"date": "YYYY-MM-DD",
           "patients_with_readings": a,
           "patients_with_completed_calls": b,
           "total_calls_attempted": c,
           "distinct_patients_attempted": d
          }, ...
        ]
      }
    - Always returns all dates in the requested span (zero-filled if no data).
    - If no date range provided, defaults to today's date.
    """
    if date and (date_from or date_to):
        raise HTTPException(status_code=400, detail="Use either ?date= or (?date_from=&date_to=), not both.")

    # Resolve date range
    if date:
        start_d = end_d = date
    else:
        today = datetime.utcnow().date()
        start_d = date_from or today
        end_d = date_to or today
        if start_d > end_d:
            raise HTTPException(status_code=400, detail="date_from cannot be after date_to")

    # ---------- totals ----------
    # total patients in org
    total_patients_row = db_session.execute(
        text("SELECT COUNT(*) AS c FROM patients WHERE org_id = :org_id"),
        {"org_id": org_id},
    ).mappings().first()
    total_patients = int(total_patients_row["c"]) if total_patients_row else 0

    # total minutes this calendar month for this org
    # SQLite: compare strftime('%Y-%m', created_at) to current UTC y-m
    y_m = datetime.utcnow().strftime("%Y-%m")
    minutes_row = db_session.execute(
        text("""
            SELECT COALESCE(SUM(c.duration_seconds), 0) AS secs
            FROM calls c
            WHERE c.org_id = :org_id
              AND strftime('%Y-%m', c.created_at) = :ym
        """),
        {"org_id": org_id, "ym": y_m},
    ).mappings().first()
    total_minutes_this_month = round((int(minutes_row["secs"]) if minutes_row and minutes_row["secs"] is not None else 0) / 60, 2)
    minutes_cap_this_month = total_patients * MINUTES_PER_PATIENT_MONTH

    # ---------- per-day aggregates (3 queries) ----------
    params_base = {
        "org_id": org_id,
        "df": start_d.isoformat(),
        "dt": end_d.isoformat()
    }

    # 1) DISTINCT patients with readings
    rows_readings = db_session.execute(
        text("""
            SELECT date(c.created_at) AS day, COUNT(DISTINCT c.patient_id) AS cnt
            FROM calls c
            WHERE c.org_id = :org_id
              AND date(c.created_at) >= :df AND date(c.created_at) <= :dt
              AND EXISTS (SELECT 1 FROM readings r WHERE r.call_id = c.id)
            GROUP BY day
        """),
        params_base,
    ).mappings().all()
    map_readings = {r["day"]: int(r["cnt"]) for r in rows_readings}

    # 2) DISTINCT patients with completed calls
    rows_completed = db_session.execute(
        text("""
            SELECT date(c.created_at) AS day, COUNT(DISTINCT c.patient_id) AS cnt
            FROM calls c
            WHERE c.org_id = :org_id
              AND date(c.created_at) >= :df AND date(c.created_at) <= :dt
              AND c.status = 'completed'
            GROUP BY day
        """),
        params_base,
    ).mappings().all()
    map_completed = {r["day"]: int(r["cnt"]) for r in rows_completed}

    # 3a) total calls attempted per day
    rows_attempts = db_session.execute(
        text("""
            SELECT date(c.created_at) AS day, COUNT(*) AS cnt
            FROM calls c
            WHERE c.org_id = :org_id
              AND date(c.created_at) >= :df AND date(c.created_at) <= :dt
            GROUP BY day
        """),
        params_base,
    ).mappings().all()
    map_attempts = {r["day"]: int(r["cnt"]) for r in rows_attempts}

    # 3b) distinct patients attempted per day (any call, any status)
    rows_pat_attempts = db_session.execute(
        text("""
            SELECT date(c.created_at) AS day, COUNT(DISTINCT c.patient_id) AS cnt
            FROM calls c
            WHERE c.org_id = :org_id
              AND date(c.created_at) >= :df AND date(c.created_at) <= :dt
            GROUP BY day
        """),
        params_base,
    ).mappings().all()
    map_pat_attempts = {r["day"]: int(r["cnt"]) for r in rows_pat_attempts}

    # ---------- build zero-filled daily list ----------
    daily: List[Dict[str, Any]] = []
    for d in _daterange(start_d, end_d):
        ds = d.isoformat()
        daily.append({
            "date": ds,
            "patients_with_readings": map_readings.get(ds, 0),
            "patients_with_completed_calls": map_completed.get(ds, 0),
            "total_calls_attempted": map_attempts.get(ds, 0),
            "distinct_patients_attempted": map_pat_attempts.get(ds, 0),
        })

    return {
        "org_id": org_id,
        "date_range": {"from": start_d.isoformat(), "to": end_d.isoformat()},
        "totals": {
            "total_patients": total_patients,
            "total_minutes_this_month": total_minutes_this_month,
            "minutes_cap_this_month": minutes_cap_this_month,
            "minutes_per_patient_month": MINUTES_PER_PATIENT_MONTH,
        },
        "daily": daily,
    }

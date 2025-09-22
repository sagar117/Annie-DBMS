#!/usr/bin/env python3
"""
Seed dummy data for Wellcare Today LLC into the SQLite DB at ./annie.db

- Deletes any existing org with the same name and its related patients/calls/readings.
- Inserts org, patients, daily calls per patient between 2025-08-22 and 2025-09-20 inclusive.
- For each call, sets status completed, random duration, transcript/summary and inserts readings.
"""

import sqlite3
import random
import json
from datetime import datetime, timedelta, timezone, date, time

DB_PATH = "./annie.db"   # change if your DB file is at a different path
ORG_NAME = "Wellcare Today LLC"
PATIENTS = [
    {"name": "Alex Joe", "age": 76, "phone": "+917620622893"},
    {"name": "David Bowie", "age": 66, "phone": "+917620622893"},
    {"name": "Ben Stoke", "age": 45, "phone": "+917620622893"},
]
AGENT = "annie_RPM"
START_DATE = date(2025, 8, 22)
END_DATE = date(2025, 9, 20)  # inclusive
READING_PRESENCE_PROB = 0.85  # probability that readings were collected for a call

def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    # produce timezone-naive ISO to match typical DB usage
    return dt.isoformat(sep=' ')

def random_bp(age):
    # rough plausible values by age
    base_sys = 110 + (0 if age < 50 else 5) + random.randint(-8, 12)
    base_dia = 70 + random.randint(-6, 8)
    # Add some hypertensive values occasionally
    if random.random() < 0.12:
        base_sys += random.randint(10, 30)
        base_dia += random.randint(5, 15)
    return base_sys, base_dia

def random_pulse(age):
    p = 70 + random.randint(-10, 15)
    if random.random() < 0.05:
        p = 50 + random.randint(-10, 10)
    return p

def random_glucose():
    return 90 + random.randint(-15, 40)

def random_weight(age):
    # just generate a plausible weight
    base = 60 + random.randint(-10, 30)
    return base

def ensure_tables_exist(conn):
    cur = conn.cursor()
    # Check for expected tables
    tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    needed = {"calls", "readings", "patients", "organizations"}
    alt_needed = {"calls", "readings", "patients", "orgs", "organizations", "organization"}
    print("Found tables:", tables)
    # We won't block if 'organizations' not found — we'll try to detect name below.
    return tables

def detect_table_name(conn, candidates):
    cur = conn.cursor()
    for t in candidates:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,))
        if cur.fetchone():
            return t
    return None

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # find table names (flexible)
    org_table = detect_table_name(conn, ["organizations", "orgs", "organization"])
    patient_table = detect_table_name(conn, ["patients", "patient"])
    calls_table = detect_table_name(conn, ["calls", "call"])
    readings_table = detect_table_name(conn, ["readings", "reading"])

    if not (org_table and patient_table and calls_table and readings_table):
        print("ERROR: Could not find expected tables in DB.")
        print("Detected tables:", [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
        print("Expected at least: organizations OR orgs, patients, calls, readings")
        conn.close()
        return

    print("Using tables:", org_table, patient_table, calls_table, readings_table)

    # 1) Delete existing org with name OR insert new one after cleanup
    cur.execute(f"SELECT id FROM {org_table} WHERE name = ?", (ORG_NAME,))
    row = cur.fetchone()
    if row:
        org_id = row["id"]
        print(f"Found existing org id={org_id} with name {ORG_NAME}, deleting related data...")
        # delete readings -> calls -> patients -> org
        # readings refer to call_id and patient_id; delete by joins where possible
        # safe approach: delete readings where call_id in calls of this org OR patient_id in patients of this org
        try:
            # get patient ids for org
            patient_ids = [r["id"] for r in cur.execute(f"SELECT id FROM {patient_table} WHERE org_id = ?", (org_id,)).fetchall()]
            # get call ids for org
            call_ids = [r["id"] for r in cur.execute(f"SELECT id FROM {calls_table} WHERE org_id = ?", (org_id,)).fetchall()]
            if patient_ids:
                cur.executemany(f"DELETE FROM {readings_table} WHERE patient_id = ?", [(pid,) for pid in patient_ids])
            if call_ids:
                cur.executemany(f"DELETE FROM {readings_table} WHERE call_id = ?", [(cid,) for cid in call_ids])
            cur.execute(f"DELETE FROM {calls_table} WHERE org_id = ?", (org_id,))
            cur.execute(f"DELETE FROM {patient_table} WHERE org_id = ?", (org_id,))
            cur.execute(f"DELETE FROM {org_table} WHERE id = ?", (org_id,))
            conn.commit()
            print("Deleted previous org and related rows.")
        except Exception as e:
            print("Warning: deletion of existing org failed:", e)
            conn.rollback()
            conn.close()
            return
    # Insert org
    print("Inserting org:", ORG_NAME)
    # handle different column names set: name, org_name etc.
    # common columns: id (auto), name, address, logo
    cur.execute(f"INSERT INTO {org_table} (name, address, logo) VALUES (?, ?, ?)", (ORG_NAME, "123 Mock Street", ""))
    conn.commit()
    cur.execute(f"SELECT id FROM {org_table} WHERE name = ?", (ORG_NAME,))
    org_row = cur.fetchone()
    org_id = org_row["id"]
    print("Inserted org id:", org_id)

    # Insert patients
    patient_ids = {}
    for p in PATIENTS:
        patient_id_str = p["name"].split()[0].upper() + str(random.randint(100,999))
        dob_year = 2025 - p["age"]
        dob = f"{dob_year}-01-01"
        print("Inserting patient:", p["name"], "DOB:", dob)
        cur.execute(
            f"INSERT INTO {patient_table} (org_id, patient_id, name, phone, dob) VALUES (?, ?, ?, ?, ?)",
            (org_id, patient_id_str, p["name"], p["phone"], dob),
        )
        conn.commit()
        new_id = cur.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
        patient_ids[p["name"]] = new_id
        print(" -> patient.id =", new_id)

    # Create calls & readings across date range
    current = START_DATE
    total_calls = 0
    total_readings = 0
    while current <= END_DATE:
        for p in PATIENTS:
            pid = patient_ids[p["name"]]
            # pick a random time around 10:00-16:00
            hour = random.choice([9,10,11,13,14,15])
            minute = random.randint(0,59)
            start_dt = datetime.combine(current, time(hour, minute))
            # random duration seconds between 60 and 600
            duration = random.randint(60, 600)
            end_dt = start_dt + timedelta(seconds=duration)
            status = "completed"
            transcript = f"[assistant] Hello {p['name']}, I'm Annie. [user] Hello. [user] My BP is {random.randint(110,140)}/{random.randint(70,90)} and pulse {random.randint(60,90)}."
            summary = f"Patient reported BP and pulse on {current.isoformat()}."

            # Insert call
            try:
                cur.execute(
                    f"""INSERT INTO {calls_table} (org_id, patient_id, agent, status, start_time, end_time, duration_seconds, transcript, summary, twilio_call_sid)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (org_id, pid, AGENT, status, start_dt.isoformat(sep=' '), end_dt.isoformat(sep=' '), duration, transcript, summary, "")
                )
                conn.commit()
                call_id = cur.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
                total_calls += 1
            except Exception as e:
                print("Failed to insert call row (schema mismatch?). Error:", e)
                conn.rollback()
                conn.close()
                return

            # Decide whether to insert readings for this call
            if random.random() <= READING_PRESENCE_PROB:
                # create bp, pulse, glucose, weight
                sys_bp, dia_bp = random_bp(p["age"])
                pulse = random_pulse(p["age"])
                glucose = random_glucose()
                weight = random_weight(p["age"])

                readings_to_insert = [
                    ("bp", {"systolic": sys_bp, "diastolic": dia_bp}, f"{sys_bp}/{dia_bp}", None),
                    ("pulse", {"value": pulse}, str(pulse), "bpm"),
                    ("glucose", {"value": glucose}, str(glucose), "mg/dL"),
                    ("weight", {"value": weight}, str(weight), "kg"),
                ]

                for rtype, val_obj, raw_text, units in readings_to_insert:
                    try:
                        cur.execute(
                            f"""INSERT INTO {readings_table} (patient_id, call_id, reading_type, value, raw_text, units, recorded_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (pid, call_id, rtype, json.dumps(val_obj), raw_text, units, start_dt.isoformat(sep=' '))
                        )
                        conn.commit()
                        total_readings += 1
                    except Exception as e:
                        print("Failed to insert reading:", e)
                        conn.rollback()
                        conn.close()
                        return
            # else: simulate missing readings
        current = current + timedelta(days=1)

    print("Done seeding.")
    print("Total calls inserted:", total_calls)
    print("Total readings inserted:", total_readings)
    conn.close()


if __name__ == "__main__":
    main()

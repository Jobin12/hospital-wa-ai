import sqlite3
import uuid
import logging
from datetime import datetime
from flask import current_app

from app.utils.clinic_info import ACTIVE_STATUSES


def _db_path():
    return current_app.config.get("DB_PATH", "clinic.db")


def get_db_connection():
    try:
        conn = sqlite3.connect(_db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enforce reasonable concurrency behaviour for the background webhook threads.
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn
    except Exception as e:
        logging.error(f"Error connecting to database: {e}")
        return None


def init_db():
    """Create the appointments table if it doesn't exist yet."""
    conn = get_db_connection()
    if not conn:
        logging.error("init_db: could not open database connection.")
        return
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                appointment_id    TEXT PRIMARY KEY,
                patient_name      TEXT NOT NULL,
                patient_phone     TEXT NOT NULL,
                patient_email     TEXT,
                consultation_type TEXT NOT NULL,
                appointment_date  TEXT NOT NULL,   -- YYYY-MM-DD
                appointment_time  TEXT NOT NULL,   -- HH:MM (24h slot start)
                reason            TEXT,
                appointment_status TEXT NOT NULL DEFAULT 'BOOKED',  -- 'BOOKED' | 'CANCELLED'
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_appt_phone
                ON appointments (patient_phone);
            CREATE INDEX IF NOT EXISTS idx_appt_slot
                ON appointments (appointment_date, appointment_time, appointment_status);
            """
        )
        conn.commit()
        logging.info(f"SQLite schema ready at {_db_path()}")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")
    finally:
        conn.close()


def _serialize_appointment(row):
    """Normalize an appointments row (sqlite3.Row) into a plain dict for JSON."""
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def get_booked_times_db(appointment_date, exclude_appointment_id=None):
    """
    Return the set of appointment_time slots that are already occupied by an active
    (slot-consuming) appointment on the given date. When re-checking availability
    for an existing appointment being rescheduled, pass its id as
    exclude_appointment_id so it doesn't count against itself.
    Returns a set of 'HH:MM' strings, or None on DB error (so callers fail safe).
    """
    conn = get_db_connection()
    if not conn:
        return None

    placeholders = ", ".join(["?"] * len(ACTIVE_STATUSES))
    query = (
        "SELECT DISTINCT appointment_time FROM appointments "
        "WHERE appointment_date = ? "
        f"AND appointment_status IN ({placeholders})"
    )
    params = [appointment_date, *ACTIVE_STATUSES]
    if exclude_appointment_id:
        query += " AND appointment_id <> ?"
        params.append(exclude_appointment_id)
    try:
        cur = conn.execute(query, params)
        return {r["appointment_time"] for r in cur.fetchall() if r["appointment_time"]}
    except Exception as e:
        logging.error(f"Error fetching booked times: {e}")
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Appointments CRUD
# ---------------------------------------------------------------------------

def create_appointment_db(
    patient_name,
    patient_phone,
    consultation_type,
    appointment_date,
    appointment_time,
    patient_email=None,
    reason=None,
):
    """Insert a BOOKED appointment and return the serialized row (with appointment_id)."""
    conn = get_db_connection()
    if not conn:
        return None

    appointment_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    query = (
        "INSERT INTO appointments "
        "(appointment_id, patient_name, patient_phone, patient_email, consultation_type, "
        " appointment_date, appointment_time, reason, appointment_status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'BOOKED', ?, ?)"
    )
    params = (
        appointment_id,
        patient_name,
        patient_phone,
        patient_email,
        consultation_type,
        appointment_date,
        appointment_time,
        reason,
        now,
        now,
    )
    try:
        conn.execute(query, params)
        conn.commit()
        cur = conn.execute(
            "SELECT * FROM appointments WHERE appointment_id = ?", (appointment_id,)
        )
        return _serialize_appointment(cur.fetchone())
    except Exception as e:
        conn.rollback()
        logging.error(f"Error creating appointment: {e}")
        return None
    finally:
        conn.close()


def get_appointments_db(patient_phone, appointment_id=None):
    """
    Retrieve appointments for a patient, scoped by patient_phone so a caller can only
    ever see their own appointments. Optionally narrow to a single appointment_id.
    """
    conn = get_db_connection()
    if not conn:
        return []

    query = "SELECT * FROM appointments WHERE patient_phone = ?"
    params = [patient_phone]
    if appointment_id:
        query += " AND appointment_id = ?"
        params.append(appointment_id)
    query += " ORDER BY appointment_date DESC, appointment_time DESC"

    try:
        cur = conn.execute(query, params)
        return [_serialize_appointment(r) for r in cur.fetchall()]
    except Exception as e:
        logging.error(f"Error retrieving appointments: {e}")
        return []
    finally:
        conn.close()


def update_appointment_db(appointment_id, patient_phone, fields):
    """
    Update allowed fields on an appointment, scoped by appointment_id AND
    patient_phone so a caller can never modify another patient's appointment.
    `fields` is a dict of column -> value. Returns the updated serialized row,
    or None if not found.
    """
    if not fields:
        return None

    conn = get_db_connection()
    if not conn:
        return None

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    query = (
        f"UPDATE appointments SET {set_clause}, updated_at = ? "
        "WHERE appointment_id = ? AND patient_phone = ?"
    )
    params = [*fields.values(), datetime.utcnow().isoformat(), appointment_id, patient_phone]
    try:
        cur = conn.execute(query, params)
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM appointments WHERE appointment_id = ?", (appointment_id,)
        ).fetchone()
        return _serialize_appointment(row)
    except Exception as e:
        conn.rollback()
        logging.error(f"Error updating appointment {appointment_id}: {e}")
        return None
    finally:
        conn.close()


def cancel_appointment_db(appointment_id, patient_phone):
    """
    Mark an appointment CANCELLED (never delete), scoped by appointment_id AND
    patient_phone. Returns the updated serialized row, or None if not found / on error.
    """
    conn = get_db_connection()
    if not conn:
        return None

    try:
        cur = conn.execute(
            "UPDATE appointments SET appointment_status = 'CANCELLED', updated_at = ? "
            "WHERE appointment_id = ? AND patient_phone = ?",
            (datetime.utcnow().isoformat(), appointment_id, patient_phone),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM appointments WHERE appointment_id = ?", (appointment_id,)
        ).fetchone()
        return _serialize_appointment(row)
    except Exception as e:
        conn.rollback()
        logging.error(f"Error cancelling appointment {appointment_id}: {e}")
        return None
    finally:
        conn.close()

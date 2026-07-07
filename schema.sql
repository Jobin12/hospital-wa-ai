-- Dr Jamshi's Homoeopathy - WhatsApp Assistant - SQLite schema
-- The app creates this automatically on startup (see app/utils/db_utils.py:init_db).
-- To bootstrap manually:  sqlite3 clinic.db < schema.sql

-- ---------------------------------------------------------------------------
-- appointments: one row per appointment request.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS appointments (
    appointment_id     TEXT PRIMARY KEY,           -- uuid4
    patient_name       TEXT NOT NULL,
    patient_phone      TEXT NOT NULL,              -- taken from the WhatsApp sender
    patient_email      TEXT,
    consultation_type  TEXT NOT NULL,              -- 'In-Clinic Consultation' | 'Online Consultation'
    appointment_date   TEXT NOT NULL,              -- YYYY-MM-DD
    appointment_time   TEXT NOT NULL,              -- HH:MM (24h slot start)
    reason             TEXT,                        -- free-text chief complaint / note
    appointment_status TEXT NOT NULL DEFAULT 'BOOKED',  -- 'BOOKED' | 'CANCELLED'
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_appt_phone
    ON appointments (patient_phone);
CREATE INDEX IF NOT EXISTS idx_appt_slot
    ON appointments (appointment_date, appointment_time, appointment_status);

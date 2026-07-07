# Dr Jamshi's Homoeopathy — WhatsApp AI Assistant

A WhatsApp Cloud API chatbot for **Dr Jamshi's Homoeopathy & Fertility Centre**, Kondotty, Kerala.
Patients can message the clinic's WhatsApp number to:

- **Book / reschedule / cancel appointments** (in-clinic or online consultations)
- Ask basic questions — **location, directions, working hours, contact, services, what the clinic treats**
- Get connected to a human — the bot **escalates to clinic staff** whenever a patient
  asks for medical advice, completes a booking, asks about fees, or explicitly asks for a person

It is built on the same architecture as the `resort-booking-wa-ai` project: a Flask webhook,
a LangChain tool-calling agent (OpenAI or Gemini, switchable via `.env`), and per-user
conversation memory. Unlike the resort project it uses a local **SQLite** database (demo setup),
so there is nothing external to provision.

## What's inside

```
app/
  __init__.py            create_app(): loads config, inits SQLite, registers webhook
  config.py              env → Flask config
  views.py               /webhook GET (verify) + POST (message), background processing
  decorators/security.py X-Hub-Signature-256 validation
  utils/
    clinic_info.py       single source of truth: clinic facts, hours, slot logic
    db_utils.py          SQLite appointments store (auto-creates schema)
    whatsapp_utils.py    WhatsApp Cloud API send/receive + dedup
  services/
    llm_factory.py       OpenAI / Gemini selector
    agent_service.py     system prompt + tools (the agent brain)
schema.sql               reference SQLite schema (created automatically at startup)
```

## Agent tools

| Tool | Purpose |
|------|---------|
| `get_current_datetime` | Resolve "today/tomorrow/now" to an exact IST date; answer "is a slot free now?" |
| `check_availability` | List free 30-min slots for a date |
| `book_appointment` | Create an appointment after confirmation |
| `retrieve_appointments` | List the patient's own appointments |
| `reschedule_appointment` | Move an appointment to a new date/time |
| `cancel_appointment` | Cancel (soft) an appointment |
| `escalate_to_human` | Notify clinic staff (medical advice, bookings, fees, human request) |

The assistant **never gives medical advice** — clinical questions are always steered to a
booking and escalated to a human.

## Setup

```bash
cd hospital-wa-ai
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env    # then fill in your WhatsApp + LLM credentials
python run.py           # starts on http://0.0.0.0:8000, creates clinic.db
```

Point your Meta WhatsApp webhook at `https://<your-host>/webhook` using the same
`VERIFY_TOKEN` you set in `.env`. Set `OPERATOR_WAID` to the staff WhatsApp number that
should receive escalation summaries.

### LLM provider

Switch between OpenAI and Gemini with a single env var — no code change:

```
LLM_PROVIDER=openai   # uses OPENAI_API_KEY / OPENAI_MODEL
LLM_PROVIDER=gemini   # uses GOOGLE_API_KEY / GEMINI_MODEL
```

## Clinic reference (from drjamshishomoeopathy.dialndial.com)

- **Doctor:** Dr. Jamsheena Puthalath (BHMS, CRH)
- **Address:** Pazhayangadi Road, Kondotty, Malappuram, Kerala 673638 (near Marxist Party Office)
- **Hours:** 9:30 AM – 7:00 PM, all days (Sunday appointments available)
- **Phone:** 9633661111 / 7012272950 · **Email:** drjamshishomeopathy@gmail.com
- **Focus:** fertility/infertility, PCOD, thyroid, ovarian cysts, fibroids, and general homoeopathy

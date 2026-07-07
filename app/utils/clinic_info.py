"""
Static clinic knowledge - the single source of truth for non-appointment facts.

Everything the assistant needs to describe the clinic (name, doctor, address,
working hours, consultation types, treatment areas) and the rules used to compute
appointment slots live here, so the agent and the server-side validators share one
definition and never invent values.

Source: https://drjamshishomoeopathy.dialndial.com/
"""
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Clinic identity & contact
# ---------------------------------------------------------------------------
CLINIC_NAME = "Dr Jamshi's Homoeopathy & Fertility Centre"
DOCTOR_NAME = "Dr. Jamsheena Puthalath"
DOCTOR_QUALIFICATION = "BHMS, CRH (Medvarsity)"

ADDRESS = (
    "Pazhayangadi Road, Kondotty (near Marxist Party Office), "
    "Malappuram District, Kerala 673638"
)
GOOGLE_MAPS_HINT = "Kondotty, on Pazhayangadi Road, near the Marxist Party Office."

PHONE_NUMBERS = ["9633661111", "7012272950"]
WHATSAPP_NUMBER = "919633661111"
EMAIL = "drjamshishomeopathy@gmail.com"

INSTAGRAM = "@drjamshis_homoeopathy"
FACEBOOK = "Dr jamshis-homeopathy-kondotty"

# ---------------------------------------------------------------------------
# Working hours & appointment slot configuration
# ---------------------------------------------------------------------------
# The clinic is open every day (Sunday appointments available).
OPEN_TIME = "09:30"      # 9:30 AM
CLOSE_TIME = "19:00"     # 7:00 PM
SLOT_MINUTES = 30        # each consultation slot is 30 minutes
OPEN_ON_SUNDAY = True    # Sunday appointments available

WORKING_HOURS_TEXT = "9:30 AM to 7:00 PM, all days (Sunday appointments available)"

# Appointment statuses that consume a slot (an active appointment occupies a time).
ACTIVE_STATUSES = ("BOOKED",)

# ---------------------------------------------------------------------------
# Consultation types
# ---------------------------------------------------------------------------
# Both types share the single doctor's schedule, so a booked time blocks that
# slot regardless of type. The type is recorded on the appointment for the staff.
CONSULTATION_TYPES = {
    "In-Clinic Consultation": {
        "description": "Visit the clinic in Kondotty for an in-person consultation with the doctor.",
    },
    "Online Consultation": {
        "description": "Consult the doctor remotely over a video/phone call; medicines can be couriered to you.",
    },
}

# ---------------------------------------------------------------------------
# Treatment areas (for describing what the clinic treats - NOT medical advice)
# ---------------------------------------------------------------------------
PRIMARY_SPECIALTIES = [
    "Female and male infertility",
    "Ovarian cysts",
    "Uterine fibroids",
    "Thyroid disorders",
    "PCOD and menstrual irregularities",
    "Low sperm count and sperm abnormalities",
]

OTHER_CONDITIONS = [
    "Allergies",
    "Asthma and persistent cough",
    "Migraines",
    "Skin conditions (acne, eczema, psoriasis)",
    "Hair loss",
    "Digestive issues",
    "Mental health concerns",
    "Lifestyle-related diseases",
]

SERVICES = [
    "In-clinic consultations",
    "Online consultations",
    "Medicine courier delivery",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_hhmm(value):
    """Parse a 'HH:MM' 24-hour string into a (hour, minute) tuple."""
    hh, mm = value.strip().split(":")
    return int(hh), int(mm)


def all_slots():
    """
    Return the ordered list of appointment slot start times ('HH:MM', 24-hour)
    the clinic offers on any given day, derived from OPEN_TIME/CLOSE_TIME/SLOT_MINUTES.
    A slot is valid only if it fully fits before CLOSE_TIME.
    """
    open_h, open_m = _parse_hhmm(OPEN_TIME)
    close_h, close_m = _parse_hhmm(CLOSE_TIME)
    start = datetime(2000, 1, 1, open_h, open_m)
    end = datetime(2000, 1, 1, close_h, close_m)
    slots = []
    cur = start
    step = timedelta(minutes=SLOT_MINUTES)
    while cur + step <= end:
        slots.append(cur.strftime("%H:%M"))
        cur += step
    return slots


def normalize_consultation_type(name):
    """Map a free-text consultation type to its canonical key, or None if unknown."""
    if not name:
        return None
    cleaned = name.strip().lower()
    for key in CONSULTATION_TYPES:
        k = key.lower()
        if k == cleaned or k in cleaned or cleaned in k:
            return key
    # Common shorthands.
    if "online" in cleaned or "video" in cleaned or "phone" in cleaned or "call" in cleaned:
        return "Online Consultation"
    if "clinic" in cleaned or "in person" in cleaned or "in-person" in cleaned or "visit" in cleaned:
        return "In-Clinic Consultation"
    return None


def normalize_time(value):
    """
    Map a free-text time ('10', '10:00', '10 am', '2:30 PM', '14:30') to a canonical
    'HH:MM' 24-hour slot string, or None if it can't be parsed / isn't a valid slot.
    """
    if not value:
        return None
    raw = str(value).strip().lower().replace(".", ":")
    is_pm = "pm" in raw
    is_am = "am" in raw
    raw = raw.replace("am", "").replace("pm", "").strip()

    if ":" in raw:
        parts = raw.split(":")
    else:
        parts = [raw, "00"]
    try:
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
    except (ValueError, IndexError):
        return None

    if is_pm and hh < 12:
        hh += 12
    if is_am and hh == 12:
        hh = 0

    candidate = f"{hh:02d}:{mm:02d}"
    return candidate if candidate in all_slots() else None


def is_valid_slot(value):
    """True if 'HH:MM' is one of the clinic's bookable slots."""
    return value in all_slots()


def _to_date(value):
    """Parse an ISO date string (YYYY-MM-DD) or pass through a date/datetime."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()

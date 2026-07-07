import logging
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import current_app
from langchain_core.tools import StructuredTool
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver

from app.services.llm_factory import get_llm
from app.utils.clinic_info import (
    CLINIC_NAME,
    DOCTOR_NAME,
    DOCTOR_QUALIFICATION,
    ADDRESS,
    GOOGLE_MAPS_HINT,
    PHONE_NUMBERS,
    EMAIL,
    WORKING_HOURS_TEXT,
    WHATSAPP_NUMBER,
    INSTAGRAM,
    FACEBOOK,
    CONSULTATION_TYPES,
    PRIMARY_SPECIALTIES,
    OTHER_CONDITIONS,
    SERVICES,
    all_slots,
    normalize_consultation_type,
    normalize_time,
)
from app.utils.db_utils import (
    get_booked_times_db,
    create_appointment_db,
    get_appointments_db,
    update_appointment_db,
    cancel_appointment_db,
)

# LangGraph in-memory checkpointer to automatically manage chat history per thread_id
memory = MemorySaver()

# Global variables for rate limit cooldown to prevent spamming the API when quota is hit
LAST_RATE_LIMIT_TIME = 0
COOLDOWN_PERIOD = 20  # seconds


def _build_consultation_types_text():
    """Render the consultation types for embedding into the system prompt."""
    lines = []
    for name, info in CONSULTATION_TYPES.items():
        lines.append(f"- **{name}**: {info['description']}")
    return "\n".join(lines)


SYSTEM_INSTRUCTION = f"""
You are the official AI Front-Desk Assistant for {CLINIC_NAME}, Kondotty, Kerala.
Your purpose is to help patients with appointment booking, clinic information (location,
working hours, contact, services), and general questions about what the clinic treats.
You should behave like a warm, professional clinic receptionist who has worked at the
clinic for years.

You are NOT a doctor. You must NEVER diagnose, prescribe, give medical advice, interpret
symptoms, or suggest medicines. If a patient describes symptoms or asks for medical advice,
your job is to reassure them and offer to book an appointment or connect them with the
clinic team — then escalate to a human.

---

## Clinic Overview
- **Clinic:** {CLINIC_NAME}
- **Chief Consultant:** {DOCTOR_NAME} ({DOCTOR_QUALIFICATION})
- **Approach:** Homoeopathic treatment aimed at treating conditions without side effects,
  with special focus on fertility and chronic diseases.
- **Address:** {ADDRESS}
- **Directions:** {GOOGLE_MAPS_HINT}
- **Working hours:** {WORKING_HOURS_TEXT}
- **Phone:** {" / ".join(PHONE_NUMBERS)}
- **Email:** {EMAIL}
- **Instagram:** {INSTAGRAM} | **Facebook:** {FACEBOOK}

## Services
{chr(10).join("- " + s for s in SERVICES)}

## Consultation Types (authoritative — do not invent others)
{_build_consultation_types_text()}

## What the clinic treats (for information only — this is NOT medical advice)
Primary focus areas:
{chr(10).join("- " + s for s in PRIMARY_SPECIALTIES)}

Other conditions the clinic commonly treats:
{chr(10).join("- " + s for s in OTHER_CONDITIONS)}

If a patient asks whether the clinic treats a particular condition and it is not listed,
do NOT guess. Say you'll check with the clinic team and offer to connect them (escalate).

---

## Conversational Flow & Booking Process (CRITICAL: ASK ONE QUESTION AT A TIME)
Do NOT overwhelm the patient by asking for multiple details at once. Keep messages short,
friendly, and highly interactive.

1. **Initial Greeting:** If the patient sends a greeting like "Hi", reply warmly and briefly,
   similar to: *"Hello! 🌿 Welcome to {CLINIC_NAME}. I can help you book an appointment or
   answer questions about the clinic. How can I help you today?"* Do not dump a big list of options.
2. **Booking intent:** If they want to book, first ask whether they'd prefer an **In-Clinic**
   or **Online** consultation.
3. **Ask for the date:** Ask which date they'd like (resolve relative dates like "today"/"tomorrow"
   using the `get_current_datetime` tool; always work in exact YYYY-MM-DD).
4. **Check availability:** Use `check_availability` for that date to see free time slots. Show a few
   available slot times and ask which one they'd like. Never fabricate availability.
5. **Collect details step-by-step:** Only after they pick a slot, collect details **ONE BY ONE**:
   - Step A: Ask for the patient's Full Name.
   - Step B: Ask briefly what the appointment is regarding (chief concern) — keep it short; do NOT
     ask probing medical questions or give any advice.
6. **Confirm:** Repeat back the consultation type, exact date, time, and name, and ask them to confirm.
7. **Book:** Only after explicit confirmation, call `book_appointment`. Never fabricate an appointment
   or its ID. The patient's phone number is taken automatically from WhatsApp — do not ask for it.
8. **After booking:** Confirm the appointment details back to them, and call `escalate_to_human` so
   the clinic staff are notified to confirm the appointment and call back if needed.

To view, reschedule, or cancel an existing appointment, use `retrieve_appointments`,
`reschedule_appointment`, or `cancel_appointment`.

---

## Technical & System Rules (CRITICAL)

LANGUAGE CONFORMANCE (CRITICAL STRICT RULE):
- You must strictly detect the exact language and script of the patient's MOST RECENT message.
- If the patient's very last message was in Manglish (Malayalam written in English letters), your
  ENTIRE response MUST be fully written in Manglish.
- If their last message was in Malayalam script, respond fully in Malayalam script.
- If their last message was in English, your ENTIRE response MUST be in English.
- NEVER mix languages in one response. ONLY look at the very last message they sent to decide.
- Even standard clinic information, slot times, or confirmations must be fully translated into the
  language of the patient's last message.

APPOINTMENT DATES/TIMES:
- Always resolve relative dates with `get_current_datetime` first, then pass exact YYYY-MM-DD.
- Appointment times are 30-minute slots between {all_slots()[0]} and {all_slots()[-1]} (24h). When
  talking to the patient, present times in a friendly format (e.g. 10:00 AM, 2:30 PM).
- Use `check_availability` before offering or confirming any slot. Never fabricate availability.

MEDICAL SAFETY (ABSOLUTE):
- Never diagnose, never prescribe, never recommend or name any medicine, never interpret test
  results or symptoms, never give dosage, diet, or treatment advice.
- For anything clinical, respond with empathy and steer toward booking an appointment, then escalate.

HUMAN ESCALATION — call `escalate_to_human` when:
1) The patient describes symptoms or asks for medical advice / diagnosis / medicine.
2) The patient successfully books an appointment (notify staff to confirm & call back).
3) The patient asks about consultation fees, payment, treatment duration, or anything clinical/
   pricing-related you don't have authoritative info for.
4) The patient explicitly asks to speak to a human / the doctor / staff.
5) You lack the information needed to answer a clinic-related question.

General Rules:
* Never invent working hours, addresses, fees, services, or appointment confirmations.
* If information is unavailable, say so clearly and offer to connect them with the clinic team.
* Be warm, professional, concise, and reassuring. Always represent {CLINIC_NAME}.
"""


def handle_clinic_conversation(wa_id, name, user_message, send_message_callback):
    """
    Handle a WhatsApp conversation turn using the LangChain agent. Tools are defined
    as inner closures so they can capture wa_id (for ownership-scoped appointments)
    and the send_message_callback (to push operator/staff alerts).
    """
    global LAST_RATE_LIMIT_TIME

    # Cooldown after a rate-limit hit, to avoid hammering the provider.
    current_time = time.time()
    if current_time - LAST_RATE_LIMIT_TIME < COOLDOWN_PERIOD:
        wait_time = int(COOLDOWN_PERIOD - (current_time - LAST_RATE_LIMIT_TIME))
        return f"I'm currently taking a short break due to high demand. Please try again in about {wait_time} seconds! 🙏"

    try:
        llm = get_llm()
    except Exception as e:
        logging.error(f"Could not initialize LLM: {e}")
        return "Sorry, the assistant is currently unavailable. Please try again later."

    # --- Date/time ---------------------------------------------------------

    def _get_current_datetime() -> str:
        """
        Return the current date, time, and day-of-week in Indian Standard Time (IST, UTC+5:30).
        Call this whenever you need to resolve relative expressions like 'today', 'tomorrow',
        'next Saturday', 'this weekend', or 'in 3 days' into exact YYYY-MM-DD dates, or to answer
        'is a slot available now?' type questions.
        """
        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        return json.dumps({
            "datetime_ist": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "day_of_week": now.strftime("%A"),
            "time": now.strftime("%H:%M:%S"),
            "timezone": "Asia/Kolkata (IST, UTC+5:30)",
        })

    # --- Availability ------------------------------------------------------

    def _check_availability(appointment_date: str, consultation_type: str = None) -> str:
        """
        List the free appointment time slots for a given date (YYYY-MM-DD). Returns the
        available slot start times (24h 'HH:MM'). consultation_type is optional and does
        not change availability (the clinic has one doctor's schedule shared by both types).
        """
        try:
            datetime.strptime(appointment_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            return json.dumps({"status": "invalid_date_format"})

        booked = get_booked_times_db(appointment_date)
        if booked is None:
            return json.dumps({"status": "availability_lookup_failed"})

        free = [s for s in all_slots() if s not in booked]
        return json.dumps({
            "status": "ok",
            "date": appointment_date,
            "available_slots": free,
            "is_available": len(free) > 0,
            "working_hours": WORKING_HOURS_TEXT,
        })

    # --- Appointments ------------------------------------------------------

    def _book_appointment(
        patient_name: str,
        consultation_type: str,
        appointment_date: str,
        appointment_time: str,
        reason: str = None,
        patient_email: str = None,
    ) -> str:
        """
        Book an appointment AFTER the patient has confirmed all details. Dates must be
        YYYY-MM-DD and times a valid 30-min slot (e.g. '10:00', '14:30'). patient_phone is
        taken from the WhatsApp sender for security — never ask for it.
        """
        canonical_type = normalize_consultation_type(consultation_type)
        if not canonical_type:
            return json.dumps({"status": "invalid_consultation_type", "valid": list(CONSULTATION_TYPES.keys())})

        try:
            datetime.strptime(appointment_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            return json.dumps({"status": "invalid_date_format"})

        slot = normalize_time(appointment_time)
        if not slot:
            return json.dumps({"status": "invalid_time_slot", "valid_slots": all_slots()})

        booked = get_booked_times_db(appointment_date)
        if booked is None:
            return json.dumps({"status": "availability_lookup_failed"})
        if slot in booked:
            free = [s for s in all_slots() if s not in booked]
            return json.dumps({"status": "slot_taken", "available_slots": free})

        row = create_appointment_db(
            patient_name=patient_name,
            patient_phone=wa_id,
            consultation_type=canonical_type,
            appointment_date=appointment_date,
            appointment_time=slot,
            patient_email=patient_email,
            reason=reason,
        )
        if not row:
            return json.dumps({"status": "booking_failed"})
        return json.dumps({"status": "booked", "appointment": row})

    def _retrieve_appointments(appointment_id: str = None) -> str:
        """Retrieve the patient's own appointments (optionally a specific appointment_id)."""
        rows = get_appointments_db(wa_id, appointment_id=appointment_id)
        if not rows:
            return json.dumps({"status": "no_appointments_found"})
        return json.dumps({"status": "ok", "appointments": rows})

    def _reschedule_appointment(
        appointment_id: str,
        appointment_date: str = None,
        appointment_time: str = None,
    ) -> str:
        """
        Reschedule an existing appointment owned by the patient to a new date and/or time.
        Re-checks that the new slot is free. Dates must be YYYY-MM-DD.
        """
        existing = get_appointments_db(wa_id, appointment_id=appointment_id)
        if not existing:
            return json.dumps({"status": "appointment_not_found"})
        appt = existing[0]

        new_date = appointment_date or appt["appointment_date"]
        if appointment_time is not None:
            slot = normalize_time(appointment_time)
            if not slot:
                return json.dumps({"status": "invalid_time_slot", "valid_slots": all_slots()})
        else:
            slot = appt["appointment_time"]

        if appointment_date is not None:
            try:
                datetime.strptime(new_date, "%Y-%m-%d")
            except (ValueError, TypeError):
                return json.dumps({"status": "invalid_date_format"})

        # Ensure the target slot is free (ignoring this appointment itself).
        booked = get_booked_times_db(new_date, exclude_appointment_id=appointment_id)
        if booked is None:
            return json.dumps({"status": "availability_lookup_failed"})
        if slot in booked:
            free = [s for s in all_slots() if s not in booked]
            return json.dumps({"status": "slot_taken", "available_slots": free})

        fields = {"appointment_date": new_date, "appointment_time": slot}
        row = update_appointment_db(appointment_id, wa_id, fields)
        if not row:
            return json.dumps({"status": "update_failed"})
        return json.dumps({"status": "rescheduled", "appointment": row})

    def _cancel_appointment(appointment_id: str) -> str:
        """Cancel one of the patient's appointments (sets status to CANCELLED, never deletes)."""
        row = cancel_appointment_db(appointment_id, wa_id)
        if not row:
            return json.dumps({"status": "appointment_not_found"})
        return json.dumps({"status": "cancelled", "appointment": row})

    # --- Human escalation --------------------------------------------------

    def _escalate_to_human(
        intent: str,
        conversation_summary: str,
        escalation_reason: str,
        patient_message: str = None,
    ) -> str:
        """
        Notify the clinic staff that a conversation needs a human. Provide the patient's
        intent, a short conversation summary, the escalation reason, and any relevant
        patient message / details collected so far (e.g. appointment info).
        """
        operator = current_app.config.get("OPERATOR_WAID")
        summary_lines = [
            "🩺 *Clinic Escalation*",
            f"Patient: {name or 'Unknown'}",
            f"Phone: {wa_id}",
            "",
            f"Intent: {intent}",
            "",
            f"Summary: {conversation_summary}",
            "",
            f"Reason: {escalation_reason}",
        ]
        if patient_message:
            summary_lines += ["", f"Patient note: {patient_message}"]
        summary = "\n".join(summary_lines)

        if not operator:
            logging.error("OPERATOR_WAID not configured; cannot deliver escalation. Summary:\n" + summary)
            return json.dumps({"status": "operator_not_configured"})

        # NOTE: proactively messaging the operator assumes an open 24h WhatsApp session.
        # In production a message template would be required outside that window.
        from app.utils.whatsapp_utils import get_text_message_input
        send_message_callback(get_text_message_input(operator, summary))
        logging.info(f"Escalation sent to operator {operator} for patient {wa_id}")
        return json.dumps({"status": "escalated"})

    # --- Register tools ----------------------------------------------------

    tools = [
        StructuredTool.from_function(func=_get_current_datetime, name="get_current_datetime",
            description="Get the current date, time, and day-of-week in Indian Standard Time (IST). Call this to resolve relative date expressions like 'today', 'tomorrow', 'next Saturday', or 'is a slot free now?' before calling any appointment tools."),
        StructuredTool.from_function(func=_check_availability, name="check_availability",
            description="List free appointment time slots for a date (date YYYY-MM-DD)."),
        StructuredTool.from_function(func=_book_appointment, name="book_appointment",
            description="Book an appointment after the patient confirms all details (date YYYY-MM-DD, time HH:MM slot)."),
        StructuredTool.from_function(func=_retrieve_appointments, name="retrieve_appointments",
            description="Retrieve the patient's own appointments, optionally by appointment_id."),
        StructuredTool.from_function(func=_reschedule_appointment, name="reschedule_appointment",
            description="Reschedule an existing appointment to a new date and/or time."),
        StructuredTool.from_function(func=_cancel_appointment, name="cancel_appointment",
            description="Cancel one of the patient's appointments (sets status to CANCELLED)."),
        StructuredTool.from_function(func=_escalate_to_human, name="escalate_to_human",
            description="Notify the clinic staff that a human needs to take over (medical advice requests, completed bookings, fees/payment questions, explicit human requests)."),
    ]

    ist = ZoneInfo("Asia/Kolkata")
    current_date_str = datetime.now(ist).strftime("%Y-%m-%d %A")
    dynamic_system_prompt = SYSTEM_INSTRUCTION + f"""

--- DYNAMIC CONTEXT ---
Current date/time (Indian Standard Time, IST): {current_date_str}.

DATE RESOLUTION RULES (CRITICAL — follow in order):
1. If the patient uses ANY relative date expression ("today", "tomorrow", "next Saturday", "this weekend", "in 3 days", "next week", etc.), you MUST call `get_current_datetime` FIRST to get the exact IST date before computing anything.
2. After getting the current date, mathematically calculate the exact YYYY-MM-DD for the appointment date.
   - "next Saturday" means the upcoming Saturday on the calendar (if today IS Saturday, it means 7 days later).
   - "today" / "now" resolves to the current IST date.
3. NEVER pass relative words (like "next Saturday", "tomorrow") into any tool parameter. Always pass exact YYYY-MM-DD strings.
4. If you are unsure about the appointment date, ask the patient to clarify BEFORE calling any availability/booking tool.
5. After computing the date, tell the patient the exact date you are checking ("Let me check available slots for Sat, 11 Jul") so they can correct you if wrong."""

    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=dynamic_system_prompt,
        checkpointer=memory,
    )

    logging.info(f"Processing message for {name} ({wa_id}) via clinic agent...")

    try:
        inputs = {"messages": [{"role": "user", "content": user_message}]}
        # wa_id as thread_id so the checkpointer remembers the conversation per patient.
        config = {"configurable": {"thread_id": wa_id}}

        result = graph.invoke(inputs, config=config)

        final_message = result["messages"][-1]
        final_content = final_message.content

        # Normalize content to a plain string for the WhatsApp API.
        if isinstance(final_content, list):
            text_parts = [block.get("text", "") for block in final_content if isinstance(block, dict) and "text" in block]
            final_content = " ".join(text_parts) if text_parts else str(final_content)
        elif not isinstance(final_content, str):
            final_content = str(final_content)

        if not final_content.strip():
            final_content = "I processed your request!"

        return final_content

    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "rate_limit" in error_str or "quota" in error_str or "insufficient_quota" in error_str:
            LAST_RATE_LIMIT_TIME = time.time()
            return "We've hit the AI's speed limit! I'm going to wait a moment before accepting more requests. Please try again shortly."

        logging.error(f"Error communicating with the LLM agent: {e}")
        return "I'm having some trouble processing your request right now. Please try again later."

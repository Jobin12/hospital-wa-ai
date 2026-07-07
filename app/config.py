import sys
import os
from dotenv import load_dotenv
import logging

def load_configurations(app):
    load_dotenv()
    app.config["ACCESS_TOKEN"] = os.getenv("ACCESS_TOKEN")
    app.config["APP_ID"] = os.getenv("APP_ID")
    app.config["APP_SECRET"] = os.getenv("APP_SECRET")
    app.config["VERSION"] = os.getenv("VERSION")
    app.config["PHONE_NUMBER_ID"] = os.getenv("PHONE_NUMBER_ID")
    app.config["VERIFY_TOKEN"] = os.getenv("VERIFY_TOKEN")

    # LLM provider selection. Switch seamlessly between providers via the .env file.
    # LLM_PROVIDER=openai (default) or LLM_PROVIDER=gemini
    app.config["LLM_PROVIDER"] = os.getenv("LLM_PROVIDER", "openai").lower()
    app.config["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
    app.config["OPENAI_MODEL"] = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")
    app.config["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY")
    app.config["GEMINI_MODEL"] = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # WhatsApp number of the clinic staff who receives human-escalation summaries.
    app.config["OPERATOR_WAID"] = os.getenv("OPERATOR_WAID")

    # SQLite database file path (local demo store for appointments).
    app.config["DB_PATH"] = os.getenv("DB_PATH", "clinic.db")

def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )

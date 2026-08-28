import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings:
    DB_PATH = os.getenv("DB_PATH", "/var/lib/cklabScheduler/scheduler.db")

    REG_STATUS_HOST = os.getenv("REG_STATUS_HOST", "")
    COMMAND_HOST = os.getenv("COMMAND_HOST", "")

    API_USER = os.getenv("MGMT_USER", "")
    API_PASS = os.getenv("MGMT_PASS", "")

    SECRET_KEY = os.getenv("SECRET_KEY", "")

    VERIFY_TLS = os.getenv("VERIFY_TLS", "true").lower() == "true"
    REG_VERIFY_TLS = os.getenv("REG_VERIFY_TLS", str(os.getenv("VERIFY_TLS", "true"))).lower() == "true"
    COMMAND_VERIFY_TLS = os.getenv("COMMAND_VERIFY_TLS", str(os.getenv("VERIFY_TLS", "true"))).lower() == "true"

    HOST_PIN = os.getenv("HOST_PIN", "")
    CONTROL_DISPLAY_NAME = os.getenv("CONTROL_DISPLAY_NAME", "Scheduler")
    DIAL_PROTOCOL = os.getenv("DIAL_PROTOCOL", "auto")

    APP_DISPLAY_NAME = os.getenv("APP_DISPLAY_NAME", "CKlabs Scheduler")

    ABOUT_TO_START_MINUTES = int(os.getenv("ABOUT_TO_START_MINUTES", "1"))
    DEFAULT_EXTEND_MINUTES = int(os.getenv("DEFAULT_EXTEND_MINUTES", "15"))
    POLL_SECONDS = int(os.getenv("POLL_SECONDS", "3"))

    WEBRTC_BASE_URL = os.getenv("WEBRTC_BASE_URL", "")

    O365_ENABLED = os.getenv("O365_ENABLED", "false").lower() == "true"
    O365_TENANT_ID = os.getenv("O365_TENANT_ID", "")
    O365_CLIENT_ID = os.getenv("O365_CLIENT_ID", "")
    O365_CLIENT_SECRET = os.getenv("O365_CLIENT_SECRET", "")
    O365_FROM_MAILBOX = os.getenv("O365_FROM_MAILBOX", "")
    O365_SAVE_TO_SENT_ITEMS = os.getenv("O365_SAVE_TO_SENT_ITEMS", "true").lower() == "true"
    O365_EMAIL_SUBJECT = os.getenv("O365_EMAIL_SUBJECT", "Your Secure Virtual Consultation")
    O365_INCLUDE_ICS = os.getenv("O365_INCLUDE_ICS", "true").lower() == "true"
    O365_TIMEZONE = os.getenv("O365_TIMEZONE", "America/New_York")
    O365_ORGANIZER_NAME = os.getenv("O365_ORGANIZER_NAME", "Pexip Scheduler")
    O365_LOCATION = os.getenv("O365_LOCATION", "Secure Virtual Session")
    O365_ALLOW_PROPOSE_NEW_TIME = os.getenv("O365_ALLOW_PROPOSE_NEW_TIME", "false").lower() == "true"

    @classmethod
    def validate_web(cls):
        missing = []
        for name, value in [
            ("REG_STATUS_HOST", cls.REG_STATUS_HOST),
            ("COMMAND_HOST", cls.COMMAND_HOST),
            ("MGMT_USER", cls.API_USER),
            ("MGMT_PASS", cls.API_PASS),
            ("SECRET_KEY", cls.SECRET_KEY),
        ]:
            if not value:
                missing.append(name)
        if cls.O365_ENABLED:
            for name, value in [
                ("O365_TENANT_ID", cls.O365_TENANT_ID),
                ("O365_CLIENT_ID", cls.O365_CLIENT_ID),
                ("O365_CLIENT_SECRET", cls.O365_CLIENT_SECRET),
                ("O365_FROM_MAILBOX", cls.O365_FROM_MAILBOX),
            ]:
                if not value:
                    missing.append(name)
        if missing:
            raise RuntimeError(
                f"Missing required configuration variables: {', '.join(missing)}"
            )

    @classmethod
    def validate_worker(cls):
        missing = []
        if not cls.COMMAND_HOST:
            missing.append("COMMAND_HOST")
        if missing:
            raise RuntimeError(
                f"Missing required worker configuration variables: {', '.join(missing)}"
            )

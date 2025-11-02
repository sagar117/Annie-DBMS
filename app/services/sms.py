import os
import logging
from typing import Optional, Tuple
import requests

logger = logging.getLogger(__name__)


def send_marketing_sms(to_number: str) -> Tuple[bool, Optional[str]]:
    """Send Twilio SMS with HealthAssist marketing message.
    Returns (success, error_message). Logs details for debugging.
    """
    logger.info("[sms.helper] start send_marketing_sms to=%s", to_number)

    if not to_number:
        logger.error("[sms.helper] no to_number provided")
        return False, "No phone number provided"

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_NUMBER") or os.getenv("TWILIO_FROM")

    if not (account_sid and auth_token and from_number):
        logger.error("[sms.helper] missing Twilio credentials (sid=%s, from=%s)", bool(account_sid), bool(from_number))
        return False, "Missing Twilio credentials"

    message = (
        "Hi, this is Annie from HealthAssist.\n"
        "Upgrade from the old pendant — get your smart Samsung watch with 24/7 safety & health monitoring.\n"
        "Special offer: $29.95/mo (use code SPECIAL).\n"
        "www.wellcaretoday.com"
    )

    try:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={"To": to_number, "From": from_number, "Body": message},
            timeout=10,
        )

        try:
            data = resp.json()
        except Exception:
            data = None

        logger.debug("[sms.helper] twilio resp status=%s data=%s text=%s", resp.status_code, data, resp.text)

        if resp.status_code in (200, 201):
            sid = (data or {}).get("sid") if isinstance(data, dict) else None
            logger.info("[sms.helper] SMS sent to %s sid=%s", to_number, sid)
            return True, None

        # Twilio error
        err_code = (data or {}).get("code") if isinstance(data, dict) else None
        err_msg = (data or {}).get("message") if isinstance(data, dict) else resp.text
        logger.error("[sms.helper] SMS failed status=%s code=%s msg=%s", resp.status_code, err_code, err_msg)
        return False, f"{err_code or resp.status_code} - {err_msg}"

    except Exception as e:
        logger.exception("[sms.helper] exception sending SMS to %s: %s", to_number, e)
        return False, str(e)

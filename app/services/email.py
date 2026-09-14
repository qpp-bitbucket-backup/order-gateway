"""Email service using the SendGrid v3 Web API."""
import logging
import os
import ssl
from typing import Optional

import certifi
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

SENDGRID_SEND_URL = "https://api.sendgrid.com/v3/mail/send"


def _build_ssl_context() -> ssl.SSLContext:
    """
    certifi + the OS trust store (+ optional SSL_CERT_FILE bundle).

    Corporate networks commonly TLS-intercept HTTPS with a self-signed root
    CA that is pushed into the OS store but absent from certifi — with
    certifi alone every SendGrid call fails with CERTIFICATE_VERIFY_FAILED.
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        ctx.load_default_certs()  # additive on Windows; picks up enterprise CAs
    except ssl.SSLError:  # pragma: no cover - defensive, platform-dependent
        pass
    extra_bundle = os.environ.get("SSL_CERT_FILE")
    if extra_bundle and os.path.exists(extra_bundle):
        ctx.load_verify_locations(extra_bundle)
    return ctx


_SSL_CONTEXT = _build_ssl_context()


class EmailService:
    """Send transactional emails via the SendGrid API."""

    def is_configured(self) -> bool:
        """Whether a SendGrid API key has been provided."""
        return bool(settings.SENDGRID_API_KEY)

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
    ) -> bool:
        """
        Send an email via SendGrid.

        Returns True on success (2xx), False otherwise. Failures are
        logged but never raised, so callers can treat email as best-effort.
        """
        if not self.is_configured():
            logger.error("SENDGRID_API_KEY is not configured; cannot send email to %s", to_email)
            return False

        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {
                "email": settings.SENDGRID_FROM_EMAIL,
                "name": settings.SENDGRID_FROM_NAME,
            },
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": text_content or html_content},
                {"type": "text/html", "value": html_content},
            ],
        }

        try:
            response = httpx.post(
                SENDGRID_SEND_URL,
                headers={
                    "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10.0,
                verify=_SSL_CONTEXT,
            )
        except httpx.HTTPError as e:
            logger.error("SendGrid request failed for %s: %s", to_email, e)
            return False

        if response.status_code < 300:
            logger.info(
                "Email sent to %s via SendGrid (status=%s, message_id=%s)",
                to_email,
                response.status_code,
                response.headers.get("x-message-id", "-"),
            )
            return True

        logger.error(
            "SendGrid returned %s for %s: %s",
            response.status_code,
            to_email,
            response.text[:500],
        )
        return False

    def send_templated_email(
        self,
        to_email: str,
        template_id: str,
        dynamic_template_data: dict,
    ) -> bool:
        """
        Send via a SendGrid Dynamic Transactional Template.

        The subject/body live in the SendGrid console template; only the
        template id and the handlebars data dict are sent. Returns True on
        success (2xx), False otherwise.
        """
        if not self.is_configured():
            logger.error("SENDGRID_API_KEY is not configured; cannot send email to %s", to_email)
            return False

        payload = {
            "personalizations": [
                {
                    "to": [{"email": to_email}],
                    "dynamic_template_data": dynamic_template_data,
                }
            ],
            "from": {
                "email": settings.SENDGRID_FROM_EMAIL,
                "name": settings.SENDGRID_FROM_NAME,
            },
            "template_id": template_id,
        }

        try:
            response = httpx.post(
                SENDGRID_SEND_URL,
                headers={
                    "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10.0,
                verify=_SSL_CONTEXT,
            )
        except httpx.HTTPError as e:
            logger.error("SendGrid request failed for %s: %s", to_email, e)
            return False

        if response.status_code < 300:
            logger.info(
                "Templated email sent to %s via SendGrid (template=%s, status=%s, message_id=%s)",
                to_email,
                template_id,
                response.status_code,
                response.headers.get("x-message-id", "-"),
            )
            return True

        logger.error(
            "SendGrid returned %s for %s (template=%s): %s",
            response.status_code,
            to_email,
            template_id,
            response.text[:500],
        )
        return False


email_service = EmailService()

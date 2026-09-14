"""
Test script for the forgot-password email flow (SendGrid).

Two modes:

  full (default):
    Calls user_service.forgot_password() end-to-end: looks the user up in
    the DB (must exist and be active), generates a REAL reset token, and
    sends the actual reset email via SendGrid. The link in the received
    email can then be used against POST /api/platform/users/reset-password.

  --direct:
    Skips the DB entirely and sends a reset-style test email straight to
    the given address with a throwaway token. Verifies only the SendGrid
    side: SENDGRID_API_KEY validity, sender address verification, delivery.

  --dry-run:
    Works with either mode: renders the email and prints it instead of
    sending, and skips no other logic. Useful to check config/token
    generation without spending SendGrid quota.

Usage:
    python scripts/test_forgot_password.py --email someone@example.com
    python scripts/test_forgot_password.py --email someone@example.com --direct
    python scripts/test_forgot_password.py --email someone@example.com --direct --dry-run
    python scripts/test_forgot_password.py --email someone@example.com --dry-run
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _print_email(to_email: str, subject: str, html_content: str, text_content: str) -> None:
    """Dry-run stand-in for email_service.send_email: dump the email."""
    # Windows consoles often use legacy codepages (cp950/cp950) that choke on
    # characters like © — degrade gracefully instead of crashing the script.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    print("\n" + "=" * 70)
    print(f"[DRY-RUN] Email NOT sent. Would send via SendGrid:")
    print(f"  From:    <configured SENDGRID_FROM_EMAIL>")
    print(f"  To:      {to_email}")
    print(f"  Subject: {subject}")
    print("-" * 70)
    print("[text part]")
    print(text_content)
    print("-" * 70)
    print("[html part]")
    print(html_content)
    print("=" * 70 + "\n")


def test_full(email: str, dry_run: bool) -> None:
    """Full flow: DB lookup + real reset token + send (or print) the email."""
    from app.core.database import get_session
    from app.services import user as user_module
    from app.services.user import user_service
    from app.services.email import email_service

    if dry_run:
        original = user_module.email_service.send_email

        def _capture(to_email, subject, html_content, text_content=None):
            _print_email(to_email, subject, html_content, text_content or html_content)
            return True

        user_module.email_service.send_email = _capture

    try:
        session = next(get_session())
        try:
            sent = user_service.forgot_password(session, email)
        finally:
            session.close()
    finally:
        if dry_run:
            user_module.email_service.send_email = original

    if sent:
        logger.info(
            "Reset email sent (or rendered in dry-run). Check the inbox of '%s'.", email
        )
    else:
        logger.warning(
            "No reset email sent: user '%s' not found/inactive, or SendGrid failed "
            "(check logs above; run with --direct to isolate the SendGrid side).",
            email,
        )


def test_direct(email: str, dry_run: bool) -> None:
    """DB-less SendGrid check: send a reset-style email with a throwaway token.

    Reuses the local template from email_templates (with a [TEST] subject
    prefix) so the test exercises the exact rendering used in production.
    If a SendGrid dynamic template is configured, --direct also honors it.
    """
    from app.core.config import settings
    from app.core.security import create_password_reset_token
    from app.services.email import email_service
    from app.services.email_templates import (
        password_reset_template_data,
        render_password_reset_email,
    )
    from app.services.user import build_reset_link

    if not email_service.is_configured():
        logger.error("SENDGRID_API_KEY is not set in .env — nothing to test.")
        sys.exit(1)

    token = create_password_reset_token(0)  # throwaway token, user_id=0
    reset_link = build_reset_link(settings.PASSWORD_RESET_URL, token)
    expire_minutes = settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
    template_id = settings.SENDGRID_RESET_PASSWORD_TEMPLATE_ID

    if template_id:
        logger.info("Using SendGrid dynamic template: %s", template_id)

        if dry_run:
            _print_email(
                email,
                "[from SendGrid template]",
                f"<dynamic_template_data: {password_reset_template_data('test-user', reset_link, expire_minutes)}>",
                str(password_reset_template_data("test-user", reset_link, expire_minutes)),
            )
            return

        sent = email_service.send_templated_email(
            to_email=email,
            template_id=template_id,
            dynamic_template_data=password_reset_template_data(
                display_name="SendGrid Test",
                reset_link=reset_link,
                expire_minutes=expire_minutes,
            ),
        )
    else:
        subject, text_content, html_content = render_password_reset_email(
            display_name="SendGrid Test (throwaway token, cannot reset any real account)",
            reset_link=reset_link,
            expire_minutes=expire_minutes,
            subject_prefix="[TEST] ",
        )

        logger.info(
            "Sending test email: from=%s to=%s (reset link expires in %s minutes)",
            settings.SENDGRID_FROM_EMAIL,
            email,
            expire_minutes,
        )

        if dry_run:
            _print_email(email, subject, html_content, text_content)
            return

        sent = email_service.send_email(
            to_email=email, subject=subject, html_content=html_content, text_content=text_content
        )

    if sent:
        logger.info("SendGrid accepted the email — check the inbox of '%s'.", email)
    else:
        logger.error(
            "SendGrid send failed. Common causes: invalid API key, sender address "
            "not verified (Single Sender Verification), or network blocking api.sendgrid.com "
            "(run app/tmp/diag_sendgrid.py to narrow it down)."
        )
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test forgot-password email flow (SendGrid)")
    parser.add_argument("--email", type=str, required=True, help="Recipient email address")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Skip the DB and send a reset-style test email directly (SendGrid check only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render and print the email instead of sending it",
    )
    args = parser.parse_args()

    if args.direct:
        logger.info("=== Testing forgot-password email (direct SendGrid) to %s ===", args.email)
        test_direct(args.email, args.dry_run)
    else:
        logger.info("=== Testing forgot-password full flow for %s ===", args.email)
        test_full(args.email, args.dry_run)

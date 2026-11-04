"""Operational commands for initial administrator provisioning."""

import argparse
import asyncio
import re
import smtplib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal, initialize_database
from .mail import send_account_email
from .models import Account, Invitation, utcnow
from .security import generate_account_token


def bootstrap_admin(email: str) -> None:
    settings = get_settings()
    settings.validate_secrets()
    if not settings.smtp_configured:
        raise SystemExit("Configure SMTP before inviting the initial administrator.")
    email = email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise SystemExit("Invalid email address.")

    initialize_database()
    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.email == email))
        if account:
            account.role = "admin"
            account.is_active = True
            account.verified_at = account.verified_at or utcnow()
            account.session_version += 1
            db.commit()
            print("Existing account promoted to admin. Sign in again.")
            return

        token, token_hash = generate_account_token()
        invitation = Invitation(
            id=uuid4().hex,
            email=email,
            role="admin",
            token_hash=token_hash,
            invited_by_account_id=None,
            expires_at=utcnow() + timedelta(days=7),
        )
        db.add(invitation)
        db.commit()

    link = f"{settings.public_base_url}/accept-invitation#token={token}"
    try:
        asyncio.run(
            asyncio.to_thread(
                send_account_email,
                settings,
                email,
                "Invitación de administrador a MarkItDown",
                f"Completa tu cuenta de administrador: {link}",
            )
        )
    except (OSError, TimeoutError, smtplib.SMTPException) as exc:
        print(f"No se pudo enviar el correo de invitación ({type(exc).__name__}).")
        print("El enlace de invitación sigue siendo válido durante 7 días:")
        print(link)
        return
    print("Administrator invitation sent; it expires in 7 days.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap-admin", help="Invite or promote the initial admin"
    )
    bootstrap.add_argument("email")
    args = parser.parse_args()
    if args.command == "bootstrap-admin":
        bootstrap_admin(args.email)


if __name__ == "__main__":
    main()

import smtplib
import ssl
from email.message import EmailMessage

from .config import Settings


def send_account_email(
    settings: Settings, recipient: str, subject: str, link: str
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message.set_content(
        f"Abre este enlace para continuar: {link}\n\nSi no solicitaste esta acción, ignora este mensaje."
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
        if settings.smtp_starttls:
            client.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)

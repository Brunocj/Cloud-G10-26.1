# app/services/mailer.py
"""
Envío de correos (REQ-US-01: correo de validación al aprobar una cuenta).

Configuración por variables de entorno (docker-compose del SliceManager):
    SMTP_HOST     — ej. smtp.gmail.com
    SMTP_PORT     — 587 (STARTTLS) o 465 (SSL)
    SMTP_USER     — cuenta emisora
    SMTP_PASSWORD — contraseña o app-password
    SMTP_FROM     — remitente visible (default: SMTP_USER)

Si SMTP_HOST no está configurado, el mailer se degrada a no-op con log
(la plataforma sigue funcionando sin correo).
"""
import asyncio
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("SliceManager.Mailer")

SMTP_HOST     = os.getenv("SMTP_HOST", "")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM     = os.getenv("SMTP_FROM", SMTP_USER or "no-reply@pucp-cloud.local")


def _send_sync(to: str, subject: str, html: str) -> bool:
    if not SMTP_HOST:
        logger.warning("[MAIL] SMTP_HOST no configurado — correo a %s omitido ('%s')", to, subject)
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, [to], msg.as_string())
        server.quit()
        logger.info("[MAIL] Correo enviado a %s: '%s'", to, subject)
        return True
    except Exception as exc:
        logger.error("[MAIL] Fallo enviando a %s: %s", to, exc)
        return False


async def send_email(to: str, subject: str, html: str) -> bool:
    """Versión async (no bloquea el event loop)."""
    if not to:
        return False
    return await asyncio.to_thread(_send_sync, to, subject, html)


def account_approved_html(fullname: str, username: str) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;border:1px solid #ddd;border-radius:12px;padding:28px">
      <h2 style="color:#2e7d32;margin-top:0">✅ ¡Tu cuenta fue aprobada!</h2>
      <p>Hola <b>{fullname or username}</b>,</p>
      <p>Un administrador validó tu solicitud de registro en <b>PUCP Private Cloud Orchestrator</b>.
         Ya puedes iniciar sesión con tu usuario <b>{username}</b> y desplegar tus topologías.</p>
      <p style="color:#888;font-size:12px">Este es un mensaje automático — no respondas a este correo.</p>
    </div>"""


def account_rejected_html(fullname: str, reason: str = "") -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;border:1px solid #ddd;border-radius:12px;padding:28px">
      <h2 style="color:#c62828;margin-top:0">Solicitud de cuenta rechazada</h2>
      <p>Hola <b>{fullname}</b>,</p>
      <p>Tu solicitud de registro en PUCP Private Cloud Orchestrator fue rechazada.
         {f"Motivo: <b>{reason}</b>." if reason else ""}</p>
      <p>Si crees que es un error, contacta al administrador de tu curso.</p>
      <p style="color:#888;font-size:12px">Este es un mensaje automático — no respondas a este correo.</p>
    </div>"""

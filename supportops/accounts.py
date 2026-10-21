"""Email verification and single-use account recovery. Delivery is configured by the operator."""

from datetime import timedelta
from email.message import EmailMessage
import secrets
import smtplib
import ssl
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, update, delete
from supportops.auth import PASSWORDS, Identity, LoginInput, digest
from supportops.db import RecoveryToken, LoginSession, User, utcnow


def deliver(config, to, subject, body):
    if not config.smtp_host or not config.smtp_from:
        return False
    msg = EmailMessage()
    msg["From"] = config.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10) as smtp:
            if config.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if config.smtp_user:
                smtp.login(config.smtp_user, config.smtp_password)
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        return False


def send_token(config, sessions, user_id, purpose):
    if not config.smtp_host:
        return
    token = secrets.token_urlsafe(32)
    with sessions() as db:
        user = db.get(User, user_id)
        if not user:
            return
        db.execute(
            update(RecoveryToken)
            .where(
                RecoveryToken.user_id == user.id,
                RecoveryToken.purpose == purpose,
                RecoveryToken.used.is_(False),
            )
            .values(used=True)
        )
        db.add(
            RecoveryToken(
                token_hash=digest(token),
                user_id=user.id,
                purpose=purpose,
                expires_at=utcnow()
                + timedelta(hours=24 if purpose == "verify" else 0.5),
                used=False,
            )
        )
        db.commit()
        email = user.email
    url = (
        config.public_base_url.rstrip("/")
        + ("/#verify/" if purpose == "verify" else "/#reset/")
        + token
    )
    deliver(
        config,
        email,
        "Verify your SupportOps email"
        if purpose == "verify"
        else "Reset your SupportOps password",
        (
            "Confirm your email address to activate AI processing.\n\n"
            if purpose == "verify"
            else "Use this one-time link to choose a new password. It expires in 30 minutes.\n\n"
        )
        + url
        + "\n\nIf you did not request this, ignore this email.",
    )


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(max_length=160, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class TokenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200)


class ResetInput(TokenInput):
    password: str = Field(min_length=12, max_length=128)


def accounts_router(auth, sessions, config):
    router = APIRouter(prefix="/api/auth")

    @router.post("/forgot-password", status_code=202)
    def forgot(body: EmailInput, request: Request, tasks: BackgroundTasks):
        auth.throttle(request, "recovery", body.email.casefold(), limit=3)
        auth.throttle(request, "recovery-ip", "", limit=10)
        with sessions() as db:
            user = db.scalar(select(User).where(User.email == body.email.casefold()))
            if user:
                tasks.add_task(send_token, config, sessions, user.id, "reset")
        return {
            "message": "If an account exists and email delivery is configured, a recovery link will be sent."
        }

    @router.post("/reset-password")
    def reset(body: ResetInput, request: Request):
        auth.throttle(request, "reset", "", limit=15)
        with sessions() as db:
            token = db.get(RecoveryToken, digest(body.token))
            if not token or token.purpose != "reset":
                raise HTTPException(410, "This recovery link is invalid or expired.")
            claimed = db.execute(
                update(RecoveryToken)
                .execution_options(synchronize_session=False)
                .where(
                    RecoveryToken.token_hash == token.token_hash,
                    RecoveryToken.used.is_(False),
                    RecoveryToken.expires_at > utcnow(),
                )
                .values(used=True)
            ).rowcount
            if not claimed:
                raise HTTPException(410, "This recovery link is invalid or expired.")
            user = db.get(User, token.user_id)
            user.password_hash = PASSWORDS.hash(body.password)
            db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
            db.commit()
        return {"message": "Password updated. Sign in with your new password."}

    @router.post("/verification-email", status_code=202)
    def resend(
        request: Request, tasks: BackgroundTasks, actor: Identity = Depends(auth.user)
    ):
        auth.throttle(request, "verify-email", actor.user_id, limit=3)
        tasks.add_task(send_token, config, sessions, actor.user_id, "verify")
        return {
            "message": "If email delivery is configured, a verification link will be sent."
        }

    @router.post("/verify-email")
    def verify(body: TokenInput, request: Request):
        auth.throttle(request, "verify", "", limit=20)
        with sessions() as db:
            token = db.get(RecoveryToken, digest(body.token))
            if not token or token.purpose != "verify":
                raise HTTPException(
                    410, "This verification link is invalid or expired."
                )
            claimed = db.execute(
                update(RecoveryToken)
                .execution_options(synchronize_session=False)
                .where(
                    RecoveryToken.token_hash == token.token_hash,
                    RecoveryToken.used.is_(False),
                    RecoveryToken.expires_at > utcnow(),
                )
                .values(used=True)
            ).rowcount
            if not claimed:
                raise HTTPException(
                    410, "This verification link is invalid or expired."
                )
            db.get(User, token.user_id).email_verified = True
            db.commit()
        return {
            "message": "Email verified. You can use AI processing and connect request sources."
        }

    return router

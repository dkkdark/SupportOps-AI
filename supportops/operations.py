"""Scoped operational status and account privacy controls."""

from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from supportops.auth import Identity, PASSWORDS, identifier
from supportops.db import (
    AccountEmailJob,
    CreditReservation,
    DemoAccount,
    IndexJob,
    Membership,
    Organization,
    PrivacyRequest,
    User,
    WorkerHeartbeat,
    utcnow,
)


def worker_status(db):
    recent = db.scalar(
        select(WorkerHeartbeat)
        .where(
            WorkerHeartbeat.updated_at > utcnow() - timedelta(minutes=5),
            WorkerHeartbeat.details.is_not(None),
            WorkerHeartbeat.details["status"].as_string() == "running",
        )
        .order_by(WorkerHeartbeat.updated_at.desc())
    )
    return {
        "healthy": bool(recent and recent.details.get("status") == "running"),
        "last_seen": recent.updated_at.isoformat() if recent else None,
    }


class PrivacyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)


def operations_router(auth, sessions, config):
    router = APIRouter(prefix="/api")

    @router.get("/operations/status")
    def status(actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            indexes = dict(
                db.execute(
                    select(IndexJob.status, func.count())
                    .where(IndexJob.organization_id == actor.organization_id)
                    .group_by(IndexJob.status)
                ).all()
            )
            emails = dict(
                db.execute(
                    select(AccountEmailJob.status, func.count())
                    .where(AccountEmailJob.user_id == actor.user_id)
                    .group_by(AccountEmailJob.status)
                ).all()
            )
            pending = db.scalar(
                select(func.count())
                .select_from(CreditReservation)
                .where(
                    CreditReservation.user_id == actor.user_id,
                    CreditReservation.settled.is_(False),
                )
            )
            return {
                "worker": worker_status(db),
                "knowledge_jobs": indexes,
                "your_email_jobs": emails,
                "your_unsettled_ai_reservations": pending,
                "demo_release": config.demo_release,
            }

    @router.get("/account/export")
    def export(actor: Identity = Depends(auth.user)):
        with sessions() as db:
            user = db.get(User, actor.user_id)
            data = {
                "profile": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "email_verified": user.email_verified,
                    "created_at": user.created_at,
                }
            }
            data["memberships"] = [
                {"business": org.name, "id": org.id, "role": m.role, "active": m.active}
                for m, org in db.execute(
                    select(Membership, Organization)
                    .join(Organization, Organization.id == Membership.organization_id)
                    .where(Membership.user_id == user.id)
                )
            ]
            balance = db.get(DemoAccount, user.id)
            data["demo"] = (
                {
                    k: getattr(balance, k)
                    for k in (
                        "spent_microusd",
                        "reserved_microusd",
                        "limit_microusd",
                        "limit_reached_at",
                        "launch_email_requested_at",
                        "launch_email_sent_at",
                        "launch_email_opt_out",
                    )
                }
                if balance
                else None
            )
            data["privacy_requests"] = [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "status": r.status,
                    "created_at": r.created_at,
                }
                for r in db.scalars(
                    select(PrivacyRequest).where(PrivacyRequest.user_id == user.id)
                )
            ]
            return JSONResponse(
                jsonable_encoder(data),
                headers={
                    "Content-Disposition": 'attachment; filename="supportops-account.json"'
                },
            )

    @router.post("/account/deletion-request", status_code=202)
    def deletion(
        body: PrivacyInput, request: Request, actor: Identity = Depends(auth.user)
    ):
        auth.throttle(request, "privacy-delete", actor.user_id, limit=3)
        with sessions() as db:
            user = db.scalar(
                select(User).where(User.id == actor.user_id).with_for_update()
            )
            try:
                valid = PASSWORDS.verify(user.password_hash, body.password)
            except Exception:
                valid = False
            if not valid:
                raise HTTPException(403, "Confirm with your current password.")
            previous = db.scalar(
                select(PrivacyRequest).where(
                    PrivacyRequest.user_id == user.id,
                    PrivacyRequest.kind == "delete",
                    PrivacyRequest.status == "pending",
                )
            )
            if not previous:
                previous = PrivacyRequest(
                    id=identifier(), user_id=user.id, kind="delete"
                )
                db.add(previous)
                db.commit()
            return {
                "id": previous.id,
                "status": "pending",
                "message": "Your deletion request is recorded for the operator. Shared business records and any required retention must be reviewed before erasure.",
            }

    @router.get("/account/privacy-requests")
    def privacy(actor: Identity = Depends(auth.user)):
        with sessions() as db:
            return [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "status": r.status,
                    "created_at": r.created_at,
                }
                for r in db.scalars(
                    select(PrivacyRequest).where(
                        PrivacyRequest.user_id == actor.user_id
                    )
                )
            ]

    return router

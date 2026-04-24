"""Discord (webhook) report-submission reminders.

Triggered from the dashboard when a viewer/general sees an op in the 'pending'
state and wants to nudge the assigned chief. We look up the chief's Discord
ID from their onboarding submission, post a mention to the webhook channel,
and log an audit row so we can enforce a cooldown.

Webhook-only: no persistent Discord bot or gateway connection. If
DISCORD_WEBHOOK_URL isn't set the endpoint returns 503.
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
import os
import logging

import httpx

from deps import require_current_role
from routers.reports import today_ist

log = logging.getLogger(__name__)

router = APIRouter()

# freddy/general/viewer can ping; chiefs can't ping themselves or each other
# (they'd just fire off the form instead).
remind_roles = require_current_role("freddy", "general", "viewer")

COOLDOWN_MIN = 30


class RemindIn(BaseModel):
    op_id: str
    # If set, ping only this chief's email. Otherwise, ping every chief
    # assigned to the op who has a Discord ID on file.
    chief_email: Optional[str] = None


def _webhook_url_or_503() -> str:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        # Fail fast with a 503 before we log anything - config issues shouldn't
        # fill the audit table with 'failed' rows.
        raise HTTPException(503, "Discord webhook is not configured (DISCORD_WEBHOOK_URL missing)")
    return url


async def _post_discord(url: str, content: str) -> tuple[bool, str]:
    """Return (ok, error_detail). Keep the timeout short so the dashboard doesn't hang."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.post(url, json={"content": content, "allowed_mentions": {"parse": ["users"]}})
        if 200 <= res.status_code < 300:
            return True, ""
        return False, f"discord {res.status_code}: {res.text[:200]}"
    except Exception as e:
        return False, f"discord request failed: {e}"


@router.post("/discord")
async def remind_discord(
    body: RemindIn,
    request: Request,
    claims: dict = Depends(remind_roles),
):
    # Upfront config check: if the webhook isn't configured we 503 before doing any
    # work, so operators know to set DISCORD_WEBHOOK_URL instead of seeing "sent 0".
    webhook_url = _webhook_url_or_503()

    db = request.app.state.db
    op = await db.fetchrow(
        "SELECT op_id, factory_name, shift, is_active FROM operations WHERE op_id = $1",
        body.op_id,
    )
    if not op:
        raise HTTPException(404, "operation not found")
    if not op["is_active"]:
        raise HTTPException(410, "operation is not active")

    report_date = today_ist()
    # Pending-only: if the op already has today's report, don't ping.
    already = await db.fetchval(
        "SELECT 1 FROM daily_reports WHERE op_id = $1 AND report_date = $2",
        body.op_id, report_date,
    )
    if already:
        return {"ok": True, "skipped": "report already submitted", "sent": 0}

    # Who to ping: one specific chief (by email) or every assigned chief.
    if body.chief_email:
        chiefs = [{"email": body.chief_email.lower()}]
    else:
        rows = await db.fetch(
            "SELECT email FROM op_assignments WHERE op_id = $1 AND role = 'chief'",
            body.op_id,
        )
        chiefs = [{"email": r["email"].lower()} for r in rows]
    if not chiefs:
        raise HTTPException(404, "no chiefs assigned to this operation")

    cooldown_cutoff = datetime.utcnow() - timedelta(minutes=COOLDOWN_MIN)
    sent_by = (claims.get("email") or "").lower()

    sent: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    missing: list[dict] = []

    for c in chiefs:
        email = c["email"]
        # Cooldown guard. We check the most recent reminder for this
        # (op, chief) pair regardless of kind so chiefs aren't double-pinged.
        last = await db.fetchval(
            """
            SELECT sent_at FROM report_reminders
             WHERE op_id = $1 AND chief_email = $2 AND status = 'sent'
             ORDER BY sent_at DESC LIMIT 1
            """,
            body.op_id, email,
        )
        if last and last.replace(tzinfo=None) > cooldown_cutoff:
            skipped.append({"chief_email": email, "reason": f"cooldown ({COOLDOWN_MIN}m)", "last_sent_at": last.isoformat()})
            await db.execute(
                """
                INSERT INTO report_reminders (op_id, chief_email, report_date, kind, status, error, sent_by_email)
                VALUES ($1, $2, $3, 'discord', 'skipped', 'cooldown', $4)
                """,
                body.op_id, email, report_date, sent_by,
            )
            continue

        # Resolve the chief's Discord ID from their onboarding submission. Use
        # the most recent submission for this email in case they re-applied.
        sub = await db.fetchrow(
            """
            SELECT discord_id, full_name
              FROM submissions
             WHERE LOWER(email) = $1
             ORDER BY created_at DESC
             LIMIT 1
            """,
            email,
        )
        discord_id = (sub["discord_id"] if sub else "") or ""
        name = (sub["full_name"] if sub else "") or email
        mention = f"<@{discord_id}>" if discord_id.isdigit() else f"**{name}**"
        if not discord_id:
            missing.append({"chief_email": email, "reason": "no Discord ID on file"})

        content = (
            f"Reminder {mention} — please submit today's report for "
            f"**{op['factory_name']} / {op['shift']}** "
            f"(op `{op['op_id']}`, {report_date.isoformat()}).\n"
            f"Form: https://spec-ops.best/report/{op['op_id']}"
        )

        ok, err = await _post_discord(webhook_url, content)
        if ok:
            sent.append({"chief_email": email, "mention": mention, "has_discord_id": bool(discord_id)})
            await db.execute(
                """
                INSERT INTO report_reminders (op_id, chief_email, report_date, kind, status, sent_by_email)
                VALUES ($1, $2, $3, 'discord', 'sent', $4)
                """,
                body.op_id, email, report_date, sent_by,
            )
        else:
            errors.append({"chief_email": email, "error": err})
            await db.execute(
                """
                INSERT INTO report_reminders (op_id, chief_email, report_date, kind, status, error, sent_by_email)
                VALUES ($1, $2, $3, 'discord', 'failed', $4, $5)
                """,
                body.op_id, email, report_date, err[:500], sent_by,
            )

    return {
        "ok": len(errors) == 0,
        "sent": len(sent),
        "sent_details": sent,
        "skipped_cooldown": skipped,
        "missing_discord": missing,
        "errors": errors,
    }

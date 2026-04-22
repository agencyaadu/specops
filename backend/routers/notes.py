from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Literal, Optional

from deps import require_current_role

router = APIRouter()

any_role = require_current_role("marshal", "general", "chief", "captain")

class NoteIn(BaseModel):
    kind:  Literal["journal", "complaint", "greeting", "other"] = "journal"
    note:  str = Field(..., min_length=1, max_length=2000)
    op_id: Optional[str] = None

@router.post("")
async def submit_note(body: NoteIn, request: Request, claims: dict = Depends(any_role)):
    email = (claims.get("email") or "").lower()
    role  = claims.get("role")
    row = await request.app.state.db.fetchrow(
        """
        INSERT INTO captain_notes (email, role, op_id, kind, note)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, created_at
        """,
        email, role, body.op_id, body.kind, body.note.strip(),
    )
    return {"ok": True, "id": row["id"], "created_at": row["created_at"].isoformat()}

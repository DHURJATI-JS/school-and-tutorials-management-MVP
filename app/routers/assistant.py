"""Private AI assistant (live, streamed, remembers context, visible only to the user)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import SessionLocal, get_db
from ..models import AssistantMemory, AssistantMessage
from ..security import Principal, client_ip, csrf_protect, limiter, require_member
from ..services.ai import AIDeclined, AIUnavailable, ai, tidy_answer
from ..services.assistant import build_messages, file_concern, looks_like_report, offline_reply, refresh_memory
from ..services.background import spawn
from ..services.moderation import mask, moderate
from ..templating import markdown_html, render

router = APIRouter(dependencies=[Depends(csrf_protect)])


@router.get("/assistant", response_class=HTMLResponse)
async def page(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_member)):
    rows = (await db.scalars(select(AssistantMessage).where(AssistantMessage.user_id == me.id)
                             .order_by(AssistantMessage.id.desc()).limit(60))).all()
    memory = await db.get(AssistantMemory, me.id)
    return render(request, "assistant/index.html", {"history": list(reversed(rows)), "memory": memory,
                                                    "is_staff": me.kind == "staff"})


class AskIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/api/assistant/ask")
async def ask(payload: AskIn, request: Request, db: AsyncSession = Depends(get_db),
              me: Principal = Depends(require_member)):
    if not limiter.hit(f"assistant:{me.key}", 30, 600):
        return JSONResponse({"detail": "That's a lot of messages in a short time — please wait a few minutes."},
                            status_code=429)
    outcome = await moderate(db, text=payload.message, principal=me, ip=client_ip(request), context="ai_assistant")
    if outcome.strikes or outcome.banned:
        async def blocked():
            yield sse({"type": "notice", "text": outcome.notice})
            yield sse({"type": "done"})
        return StreamingResponse(blocked(), media_type="text/event-stream")
    text = outcome.text
    concern = looks_like_report(text)
    messages = await build_messages(db, me.user, text, concern)
    user_msg = AssistantMessage(user_id=me.id, role="user", content=text)
    db.add(user_msg)
    await db.commit()
    uid, message_id = me.id, user_msg.id

    async def stream():
        answer, source = "", "ai"
        try:
            async for piece in ai.stream(messages, max_tokens=900, timeout=120):
                answer += piece
                yield sse({"type": "token", "text": piece})
        except AIDeclined:  # safety filter: discard any partial text
            answer, source = offline_reply(concern), "fallback"
        except AIUnavailable:
            if not answer:
                answer, source = offline_reply(concern), "fallback"
            else:
                answer += "\n\n_(Connection to the AI dropped — the answer may be incomplete.)_"
        clean = mask(tidy_answer(answer))
        yield sse({"type": "final", "html": str(markdown_html(clean)), "source": source})
        async with SessionLocal() as session:
            session.add(AssistantMessage(user_id=uid, role="assistant", content=clean, source=source))
            await session.commit()
        if concern:
            spawn(file_concern(uid, text, message_id), name=f"concern:{uid}")
        spawn(refresh_memory(uid), name=f"memory:{uid}")
        yield sse({"type": "done", "concern": concern})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/assistant/clear")
async def clear(db: AsyncSession = Depends(get_db), me: Principal = Depends(require_member)):
    await db.execute(delete(AssistantMessage).where(AssistantMessage.user_id == me.id))
    await db.execute(delete(AssistantMemory).where(AssistantMemory.user_id == me.id))
    await db.commit()
    return {"ok": True}

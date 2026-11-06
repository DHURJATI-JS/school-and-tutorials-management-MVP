"""Comments with replies under class videos and recordings.

* Who: the people who may watch the video - students of its batch, the batch teacher, supervisors and admins.
* One level of replies (a reply to a reply joins the same thread, addressed with @Name).
* Moderated like the chats (abusive words masked; severe abuse refused with a strike, repeated abuse -> IP ban) and
  rate limited. Authors delete their own comments; the batch teacher, supervisors and admins delete any.
* A reply notifies the person replied to; a student's new comment notifies the batch teacher.
* Comments by banned people show a "Banned" label; those of deleted (dormant) accounts are hidden.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ..config import settings
from ..database import get_db
from ..models import Lesson, Video, VideoComment
from ..security import Principal, client_ip, csrf_protect, limiter, require_user
from ..services.moderation import moderate
from ..services.notify import notify_users
from ..templating import env, flash, render
from .common import back, int_or, redirect
from .files import batch_member, staff_for_batch

router = APIRouter(dependencies=[Depends(csrf_protect)])
MAX_CHARS = 1000


async def _video(db: AsyncSession, me: Principal, vid: int) -> tuple[Video, bool]:
    """The video if `me` may watch it (else 404), and whether `me` may moderate its comments."""
    video = await db.scalar(select(Video).options(joinedload(Video.batch)).where(Video.id == vid))
    if video is None or not await batch_member(db, me, video.batch_id):
        raise HTTPException(404)
    return video, await staff_for_batch(db, me, video.batch_id)


def _visible(c: VideoComment) -> bool:
    return not c.deleted and not (c.user is not None and c.user.status == "deleted")


async def load_thread(db: AsyncSession, video_id: int) -> tuple[list[dict], int, str]:
    """Threads newest first (replies oldest first), the visible comment count and a version for cheap refreshes."""
    rows = (await db.scalars(select(VideoComment).options(joinedload(VideoComment.user))
                             .where(VideoComment.video_id == video_id).order_by(VideoComment.id))).unique().all()
    replies: dict[int, list[VideoComment]] = {}
    for c in rows:
        if c.parent_id:
            replies.setdefault(c.parent_id, []).append(c)
    threads, count = [], 0
    for c in reversed([c for c in rows if c.parent_id is None]):
        kids = [r for r in replies.get(c.id, []) if _visible(r)]
        if not _visible(c) and not kids:  # a deleted / dormant comment nobody answered just disappears
            continue
        threads.append({"c": c, "replies": kids})
        count += int(_visible(c)) + len(kids)
    version = f"{len(rows)}:{max((c.id for c in rows), default=0)}:{sum(not _visible(c) for c in rows)}"
    return threads, count, version


async def comment_context(db: AsyncSession, me: Principal, video: Video | None) -> dict[str, Any]:
    """Template context for partials/comments.html - used by the lesson pages and the watch page."""
    if video is None:
        return {}
    threads, count, version = await load_thread(db, video.id)
    return {"comment_video": video, "comments": threads, "comment_count": count, "comment_version": version,
            "can_moderate": await staff_for_batch(db, me, video.batch_id)}


async def _fragment(db: AsyncSession, video: Video, me: Principal, can_mod: bool, notice: str | None = None):
    threads, count, version = await load_thread(db, video.id)
    html = env.get_template("partials/comment_list.html").render(comments=threads, me=me, can_moderate=can_mod)
    return JSONResponse({"html": html, "count": count, "version": version, "notice": notice},
                        headers={"Cache-Control": "no-store"})


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


async def _link_for(db: AsyncSession, kind: str, video_id: int, comment_id: int) -> str:
    """Students follow comments on their lesson page (with attendance tracking); everyone else on the watch page."""
    if kind == "student":
        lesson_id = await db.scalar(select(Lesson.id).where(Lesson.video_id == video_id)
                                    .order_by(Lesson.date.desc()).limit(1))
        if lesson_id:
            return f"/student/lessons/{lesson_id}#c{comment_id}"
    return f"/videos/{video_id}#c{comment_id}"


# ------------------------------------------------------------------ pages
@router.get("/videos/{vid}", response_class=HTMLResponse)
async def watch(vid: int, request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    video, _ = await _video(db, me, vid)
    if me.kind == "student":  # students watch on their lesson page, where watching counts for attendance
        lesson_id = await db.scalar(select(Lesson.id).where(Lesson.video_id == vid).order_by(Lesson.date.desc()).limit(1))
        if lesson_id:
            return redirect(f"/student/lessons/{lesson_id}")
    return render(request, "shared/video.html", {"video": video, **await comment_context(db, me, video)})


@router.get("/videos/{vid}/comments")
async def list_comments(vid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    video, can_mod = await _video(db, me, vid)
    return await _fragment(db, video, me, can_mod)


# ------------------------------------------------------------------ write
@router.post("/videos/{vid}/comments")
async def post_comment(vid: int, request: Request, db: AsyncSession = Depends(get_db),
                       me: Principal = Depends(require_user), body: str = Form(""), parent_id: str = Form(""),
                       video_seconds: str = Form("")):
    video, can_mod = await _video(db, me, vid)
    wants = _wants_json(request)

    def fail(message: str, status: int = 400):
        if wants:
            return JSONResponse({"detail": message}, status_code=status)
        flash(request, message, "danger")
        return back(request, f"/videos/{vid}")

    text = body.replace("\r\n", "\n").strip()
    if not text:
        return fail("Write something first.")
    if len(text) > MAX_CHARS:
        return fail(f"Comments can be at most {MAX_CHARS} characters.")
    if not limiter.hit(f"comment:{me.key}", settings.COMMENTS_PER_5_MIN, 300):
        return fail("You're commenting very quickly — please wait a few minutes.", 429)
    parent = None
    if int_or(parent_id):
        parent = await db.get(VideoComment, int_or(parent_id))
        if parent is None or parent.video_id != vid:
            return fail("That comment no longer exists.", 404)
        if parent.parent_id:  # one level of replies: answer inside the same thread
            parent = await db.get(VideoComment, parent.parent_id)
    outcome = await moderate(db, text=text, principal=me, ip=client_ip(request), context="video_comment")
    if outcome.banned or outcome.strikes:
        return fail(outcome.notice or "Please keep comments respectful.", 403)
    seconds = int_or(video_seconds)
    if seconds is not None:
        seconds = min(seconds, video.duration_seconds or 6 * 3600)
    comment = VideoComment(video_id=vid, parent_id=parent.id if parent else None,
                           user_id=me.id if me.kind != "admin" else None, author_kind=me.kind,
                           author_name=me.name[:120], body=outcome.text, video_seconds=seconds,
                           flagged=outcome.flagged)
    db.add(comment)
    await db.flush()
    preview = outcome.text[:120]
    if parent is not None and parent.user_id and parent.user_id != me.id and not parent.deleted:
        kind = "student" if parent.author_kind == "student" else "staff"
        await notify_users(db, [parent.user_id], f"{me.name} replied to your comment", preview,
                           await _link_for(db, kind, vid, comment.id), "fa-reply")
    elif parent is None and me.kind == "student" and video.batch and video.batch.teacher_id:
        await notify_users(db, [video.batch.teacher_id], f"New comment on “{video.title}”", f"{me.name}: {preview}",
                           f"/videos/{vid}#c{comment.id}", "fa-comment")
    await db.commit()
    if wants:
        return await _fragment(db, video, me, can_mod, notice=outcome.notice)
    target = back(request, f"/videos/{vid}").headers["location"]  # same-site only (see common.back / safe_next)
    return redirect(target.split("#")[0] + f"#c{comment.id}")


@router.post("/comments/{cid}/delete")
async def delete_comment(cid: int, request: Request, db: AsyncSession = Depends(get_db),
                         me: Principal = Depends(require_user)):
    comment = await db.get(VideoComment, cid)
    if comment is None:
        raise HTTPException(404)
    video, can_mod = await _video(db, me, comment.video_id)
    mine = (comment.user_id is not None and comment.user_id == me.id) or \
        (me.kind == "admin" and comment.author_kind == "admin")
    if not (mine or can_mod):
        raise HTTPException(403, "You can only delete your own comments.")
    replies = int(await db.scalar(select(func.count()).select_from(VideoComment)
                                  .where(VideoComment.parent_id == comment.id)) or 0)
    if replies:  # keep the thread readable
        comment.deleted, comment.body, comment.video_seconds = True, "", None
    else:
        parent_id = comment.parent_id
        await db.delete(comment)
        await db.flush()
        if parent_id:  # an emptied placeholder with no replies left goes too
            parent = await db.get(VideoComment, parent_id)
            left = int(await db.scalar(select(func.count()).select_from(VideoComment)
                                       .where(VideoComment.parent_id == parent_id)) or 0)
            if parent is not None and parent.deleted and not left:
                await db.delete(parent)
    await db.commit()
    if _wants_json(request):
        return await _fragment(db, video, me, can_mod)
    return back(request, f"/videos/{video.id}")

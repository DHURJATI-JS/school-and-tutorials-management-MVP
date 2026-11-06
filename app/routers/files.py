"""Protected file serving with per-file permission checks (uploads are never public).

Staff access is scoped like the staff portal: teachers reach files of the batches they teach,
supervisors (principal / vice principal / coordinator) reach all batches, other staff none."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import SUPERVISOR_ROLES, TEACHING_ROLES
from ..database import get_db
from ..models import (Batch, DoubtMessage, Enrollment, FeePayment, Material, Meeting, SalaryPayment, Task,
                      TaskSubmission, User, Video)
from ..security import Principal, require_user
from ..services.storage import abs_path, guess_mime

router = APIRouter(prefix="/files")
CACHE_MEDIA = "private, max-age=3600"  # videos / recordings / class material: seeking + re-watching
CACHE_SENSITIVE = "private, no-store"  # ID proofs, salary slips, payment proofs, answers: never on disk


def serve(rel: str | None, download: str | None = None, cache: str = CACHE_MEDIA) -> FileResponse:
    if not rel:
        raise HTTPException(404)
    path = abs_path(rel)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type=guess_mime(rel), filename=download,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": cache})


async def enrolled(db: AsyncSession, uid: int | None, batch_id: int | None) -> bool:
    if not uid or not batch_id:
        return False
    return bool(await db.scalar(select(Enrollment.id).where(Enrollment.user_id == uid, Enrollment.batch_id == batch_id)))


async def staff_for_batch(db: AsyncSession, p: Principal, batch_id: int | None) -> bool:
    """Admin, a supervisor, or the teacher assigned to this batch."""
    if p.kind == "admin":
        return True
    if p.kind != "staff" or not batch_id:
        return False
    if p.staff_role in SUPERVISOR_ROLES:
        return True
    if p.staff_role in TEACHING_ROLES:
        return bool(await db.scalar(select(Batch.id).where(Batch.id == batch_id, Batch.teacher_id == p.id)))
    return False


async def batch_member(db: AsyncSession, p: Principal, batch_id: int | None) -> bool:
    return await staff_for_batch(db, p, batch_id) or await enrolled(db, p.id, batch_id)


@router.get("/photo/{uid}")
async def photo(uid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    user = await db.get(User, uid)
    if user is None:
        raise HTTPException(404)
    allowed = me.is_admin or me.id == uid or (me.kind == "staff" and me.staff_role in SUPERVISOR_ROLES)
    if not allowed and me.kind == "staff" and me.staff_role in TEACHING_ROLES and user.role == "student":
        allowed = bool(await db.scalar(select(Enrollment.id).join(Batch, Batch.id == Enrollment.batch_id).where(
            Enrollment.user_id == uid, Batch.teacher_id == me.id).limit(1)))
    if not allowed:
        raise HTTPException(404)
    return serve(user.photo_path)


@router.get("/idproof/{uid}")
async def idproof(uid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    user = await db.get(User, uid)
    if user is None or not (me.is_admin or me.id == uid):
        raise HTTPException(404)
    return serve(user.id_proof_path, cache=CACHE_SENSITIVE)


@router.get("/idcard/{uid}")
async def idcard(uid: int, download: int = 0, db: AsyncSession = Depends(get_db),
                 me: Principal = Depends(require_user)):
    user = await db.get(User, uid)
    if user is None or not (me.is_admin or me.id == uid):
        raise HTTPException(404)
    return serve(user.idcard_path, f"{user.id_code}-id-card.png" if download else None, cache=CACHE_SENSITIVE)


@router.get("/video/{vid}")
async def video(vid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    item = await db.get(Video, vid)
    if item is None or not await batch_member(db, me, item.batch_id):
        raise HTTPException(404)
    return serve(item.file_path)  # FileResponse supports HTTP Range (seeking)


@router.get("/recording/{mid}")
async def recording(mid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    meeting = await db.get(Meeting, mid)
    if meeting is None:
        raise HTTPException(404)
    allowed = me.is_admin or (meeting.kind == "class" and await batch_member(db, me, meeting.batch_id))
    if not allowed:
        raise HTTPException(404)
    return serve(meeting.recording_path)


@router.get("/material/{mid}")
async def material(mid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    item = await db.get(Material, mid)
    if item is None or not await batch_member(db, me, item.batch_id):
        raise HTTPException(404)
    return serve(item.file_path, item.original_name)


@router.get("/salary/{pid}")
async def salary(pid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    item = await db.get(SalaryPayment, pid)
    if item is None or not (me.is_admin or me.id == item.user_id):
        raise HTTPException(404)
    return serve(item.receipt_path, cache=CACHE_SENSITIVE)


@router.get("/fee-proof/{fid}")
async def fee_proof(fid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    item = await db.get(FeePayment, fid)
    if item is None or not (me.is_admin or me.id == item.user_id):
        raise HTTPException(404)
    return serve(item.proof_path, cache=CACHE_SENSITIVE)


@router.get("/task/{tid}")
async def task_file(tid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    item = await db.get(Task, tid)
    if item is None or not await batch_member(db, me, item.batch_id):
        raise HTTPException(404)
    return serve(item.attachment_path, None)


@router.get("/doubt/{did}")
async def doubt_frame(did: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    item = await db.get(DoubtMessage, did)
    if item is None or not (me.id == item.user_id or await staff_for_batch(db, me, item.batch_id)):
        raise HTTPException(404)
    return serve(item.image_path, cache=CACHE_SENSITIVE)


@router.get("/submission/{sid}")
async def submission(sid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    item = await db.get(TaskSubmission, sid)
    if item is None:
        raise HTTPException(404)
    if me.id != item.user_id:
        task = await db.get(Task, item.task_id)
        if task is None or not await staff_for_batch(db, me, task.batch_id):
            raise HTTPException(404)
    return serve(item.file_path, cache=CACHE_SENSITIVE)

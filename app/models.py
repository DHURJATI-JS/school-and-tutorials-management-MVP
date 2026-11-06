"""SQLAlchemy 2.x ORM models (async PostgreSQL + pgvector).

N+1 safety: every relationship is declared with ``lazy="raise"`` so an accidental
lazy load fails loudly. Queries eager-load explicitly with selectinload()/joinedload()
or use aggregate SQL.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
                        Time, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings
from .constants import STAFF_ROLES, WEEKDAY_SHORT

LEXICAL_DIM = 512


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def TS(nullable: bool = True, **kw: Any):
    return mapped_column(DateTime(timezone=True), nullable=nullable, **kw)


class Base(DeclarativeBase):
    pass


class Timestamped:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- people
class User(Timestamped, Base):
    """Students and staff (admins live in .env)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(10), index=True)  # student | staff
    staff_role: Mapped[str | None] = mapped_column(String(30))
    id_code: Mapped[str | None] = mapped_column(String(20), unique=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str] = mapped_column(String(20))
    dob: Mapped[dt.date] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    photo_path: Mapped[str] = mapped_column(String(255))
    id_proof_type: Mapped[str] = mapped_column(String(40))
    id_proof_number: Mapped[str] = mapped_column(String(60))
    id_proof_path: Mapped[str] = mapped_column(String(255))
    idcard_path: Mapped[str | None] = mapped_column(String(255))
    # student specific
    class_level: Mapped[str | None] = mapped_column(String(40))
    school_name: Mapped[str | None] = mapped_column(String(160))
    parent_name: Mapped[str | None] = mapped_column(String(120))
    parent_relation: Mapped[str | None] = mapped_column(String(30))
    parent_email: Mapped[str | None] = mapped_column(String(255))
    parent_phone: Mapped[str | None] = mapped_column(String(20))
    special_needs: Mapped[bool] = mapped_column(Boolean, default=False)
    special_needs_notes: Mapped[str | None] = mapped_column(Text)
    # staff specific
    designation: Mapped[str | None] = mapped_column(String(80))
    qualification: Mapped[str | None] = mapped_column(String(160))
    monthly_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    joining_date: Mapped[dt.date | None] = mapped_column(Date)
    bank_account_name: Mapped[str | None] = mapped_column(String(120))  # for Cashfree payouts
    bank_account_number: Mapped[str | None] = mapped_column(String(34))
    bank_ifsc: Mapped[str | None] = mapped_column(String(11))
    upi_id: Mapped[str | None] = mapped_column(String(100))
    # lifecycle
    # active | removed (access withdrawn) | banned (kept, shown as banned) | deleted (dormant, then purged)
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    status_changed_at: Mapped[dt.datetime | None] = TS()
    last_login_at: Mapped[dt.datetime | None] = TS()
    last_ip: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(60))

    enrollments: Mapped[list[Enrollment]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan", passive_deletes=True)
    health_records: Mapped[list[HealthRecord]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan", passive_deletes=True,
        order_by="HealthRecord.id.desc()")

    @property
    def is_student(self) -> bool:
        return self.role == "student"

    @property
    def is_staff(self) -> bool:
        return self.role == "staff"

    @property
    def initials(self) -> str:
        parts = [p for p in self.full_name.split() if p]
        if not parts:
            return "?"
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    @property
    def role_label(self) -> str:
        if self.is_student:
            return f"Student · {self.class_level}" if self.class_level else "Student"
        return STAFF_ROLES.get(self.staff_role or "", "Staff")

    @property
    def age(self) -> int:
        today = dt.date.today()
        return today.year - self.dob.year - ((today.month, today.day) < (self.dob.month, self.dob.day))

    @property
    def is_banned(self) -> bool:
        return self.status == "banned"

    @property
    def is_deleted(self) -> bool:
        return self.status == "deleted"

    @property
    def purge_on(self) -> dt.datetime | None:
        """A deleted account stays dormant for DELETED_RETENTION_DAYS; the purge job then removes it for good."""
        if self.status != "deleted" or self.status_changed_at is None:
            return None
        return self.status_changed_at + dt.timedelta(days=settings.DELETED_RETENTION_DAYS)

    @property
    def public_name(self) -> str:
        """The name other members see: a dormant (deleted) account is anonymous."""
        return "Deleted user" if self.status == "deleted" else self.full_name


class HealthRecord(Timestamped, Base):
    __tablename__ = "health_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    condition: Mapped[str] = mapped_column(String(160))
    severity: Mapped[str] = mapped_column(String(10), default="low")
    notes: Mapped[str | None] = mapped_column(Text)
    medication: Mapped[str | None] = mapped_column(String(255))
    emergency_contact: Mapped[str | None] = mapped_column(String(120))
    added_by: Mapped[str | None] = mapped_column(String(60))

    user: Mapped[User] = relationship(back_populates="health_records", lazy="raise")


# --------------------------------------------------------------------------- academics
class Batch(Timestamped, Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    class_level: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(9), default="#6366f1")
    days: Mapped[list[int]] = mapped_column(JSONB, default=list)  # 0=Mon … 6=Sun
    start_time: Mapped[dt.time] = mapped_column(Time)
    end_time: Mapped[dt.time] = mapped_column(Time)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    monthly_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    teacher: Mapped[User | None] = relationship(lazy="raise", foreign_keys=[teacher_id])
    enrollments: Mapped[list[Enrollment]] = relationship(
        back_populates="batch", lazy="raise", cascade="all, delete-orphan", passive_deletes=True)

    @property
    def duration_minutes(self) -> int:
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        return max(0, end - start)

    @property
    def days_label(self) -> str:
        return ", ".join(WEEKDAY_SHORT[d] for d in sorted(self.days or []))

    @property
    def time_label(self) -> str:
        return f"{self.start_time.strftime('%I:%M %p').lstrip('0')} – {self.end_time.strftime('%I:%M %p').lstrip('0')}"


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("user_id", "batch_id", name="uq_enrollment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    joined_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="enrollments", lazy="raise")
    batch: Mapped[Batch] = relationship(back_populates="enrollments", lazy="raise")


class Holiday(Timestamped, Base):
    """kind=holiday blocks everything; kind=vacation allows 'holiday homework' only."""

    __tablename__ = "holidays"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    start_date: Mapped[dt.date] = mapped_column(Date, index=True)
    end_date: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="holiday")
    allow_homework: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(60))


class Video(Timestamped, Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(255))
    external_url: Mapped[str | None] = mapped_column(String(500))
    mime: Mapped[str | None] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(12), default="upload")  # upload | recording
    meeting_id: Mapped[int | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL", use_alter=True, name="fk_videos_meeting_id"))
    notes: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(String(60))

    batch: Mapped[Batch] = relationship(lazy="raise")


class VideoComment(Timestamped, Base):
    """Comments under a class video or recording, with one level of replies (a reply to a reply joins the thread)."""

    __tablename__ = "video_comments"
    __table_args__ = (Index("ix_video_comments_video", "video_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("video_comments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    author_kind: Mapped[str] = mapped_column(String(10))  # student | staff | admin (admins have no user row)
    author_name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    video_seconds: Mapped[int | None] = mapped_column(Integer)  # "at 3:21" - a click seeks the video there
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # kept as a placeholder while it has replies

    user: Mapped[User | None] = relationship(lazy="raise")

    @property
    def timestamp_label(self) -> str | None:
        if self.video_seconds is None:
            return None
        m, s = divmod(int(self.video_seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Lesson(Timestamped, Base):
    """A scheduled class. Needs a live meeting unless a pre-recorded video is attached."""

    __tablename__ = "lessons"
    __table_args__ = (Index("ix_lessons_batch_date", "batch_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    date: Mapped[dt.date] = mapped_column(Date)
    start_time: Mapped[dt.time] = mapped_column(Time)
    end_time: Mapped[dt.time] = mapped_column(Time)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(12), default="scheduled")  # scheduled|completed|cancelled
    warning_sent_at: Mapped[dt.datetime | None] = TS()
    reminder_sent_at: Mapped[dt.datetime | None] = TS()
    missed_alert_sent_at: Mapped[dt.datetime | None] = TS()
    created_by: Mapped[str | None] = mapped_column(String(60))

    batch: Mapped[Batch] = relationship(lazy="raise")
    video: Mapped[Video | None] = relationship(lazy="raise", foreign_keys=[video_id])
    meeting: Mapped[Meeting | None] = relationship(back_populates="lesson", uselist=False, lazy="raise")

    @property
    def needs_live_meeting(self) -> bool:
        return self.video_id is None and self.status != "cancelled"

    def starts_at(self) -> dt.datetime:
        return dt.datetime.combine(self.date, self.start_time, tzinfo=settings.tz)

    def ends_at(self) -> dt.datetime:
        return dt.datetime.combine(self.date, self.end_time, tzinfo=settings.tz)


class Meeting(Timestamped, Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default="class")  # class | ptm
    title: Mapped[str] = mapped_column(String(160))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id", ondelete="SET NULL"), index=True)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"), unique=True)
    host_name: Mapped[str] = mapped_column(String(120))
    host_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    scheduled_start: Mapped[dt.datetime | None] = TS()
    scheduled_minutes: Mapped[int] = mapped_column(Integer, default=60)
    started_at: Mapped[dt.datetime | None] = TS()
    ended_at: Mapped[dt.datetime | None] = TS()
    status: Mapped[str] = mapped_column(String(12), default="scheduled", index=True)  # scheduled|live|ended|cancelled
    recording_path: Mapped[str | None] = mapped_column(String(255))
    recording_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    transcript: Mapped[str | None] = mapped_column(Text)
    ai_notes: Mapped[str | None] = mapped_column(Text)
    notes_status: Mapped[str] = mapped_column(String(12), default="none")  # none|pending|ready|failed
    notes_source: Mapped[str | None] = mapped_column(String(12))  # ai | fallback

    batch: Mapped[Batch | None] = relationship(lazy="raise")
    lesson: Mapped[Lesson | None] = relationship(back_populates="meeting", lazy="raise", foreign_keys=[lesson_id])

    @property
    def duration_seconds(self) -> int | None:
        if self.started_at and self.ended_at:
            return max(1, int((self.ended_at - self.started_at).total_seconds()))
        return None


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    __table_args__ = (UniqueConstraint("meeting_id", "participant_key", name="uq_meeting_participant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    participant_key: Mapped[str] = mapped_column(String(120))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(10))
    first_joined_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_left_at: Mapped[dt.datetime | None] = TS()
    total_seconds: Mapped[int] = mapped_column(Integer, default=0)
    join_count: Mapped[int] = mapped_column(Integer, default=1)


class MeetingMessage(Timestamped, Base):
    __tablename__ = "meeting_messages"
    __table_args__ = (Index("ix_meeting_messages_meeting", "meeting_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(12))  # chat | transcript | system
    sender_key: Mapped[str] = mapped_column(String(120))
    sender_name: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)


class Attendance(Timestamped, Base):
    __tablename__ = "attendance"
    __table_args__ = (
        UniqueConstraint("user_id", "lesson_id", name="uq_attendance_user_lesson"),
        Index("ix_attendance_batch_date", "batch_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10))  # present | absent
    source: Mapped[str] = mapped_column(String(10))  # meeting | video | manual
    detail: Mapped[str | None] = mapped_column(String(200))


class VideoProgress(Base):
    __tablename__ = "video_progress"
    __table_args__ = (UniqueConstraint("user_id", "video_id", name="uq_video_progress"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    intervals: Mapped[list] = mapped_column(JSONB, default=list)
    watched_seconds: Mapped[int] = mapped_column(Integer, default=0)
    last_position: Mapped[int] = mapped_column(Integer, default=0)
    last_heartbeat_at: Mapped[dt.datetime | None] = TS()
    attendance_marked: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- tasks & tests
class Test(Timestamped, Base):
    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    subject: Mapped[str] = mapped_column(String(80))
    class_level: Mapped[str] = mapped_column(String(40))
    topic: Mapped[str] = mapped_column(String(200))
    difficulty: Mapped[str] = mapped_column(String(10), default="medium")
    instructions: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    total_marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(12), default="draft")  # generating|draft|published|failed
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    generation_note: Mapped[str | None] = mapped_column(Text)
    show_results: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(60))

    batch: Mapped[Batch] = relationship(lazy="raise")
    questions: Mapped[list[Question]] = relationship(
        back_populates="test", lazy="raise", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Question.position")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(12))  # mcq | true_false | short
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSONB, default=list)
    answer: Mapped[str] = mapped_column(Text, default="")
    explanation: Mapped[str | None] = mapped_column(Text)
    marks: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("1"))

    test: Mapped[Test] = relationship(back_populates="questions", lazy="raise")


class Task(Timestamped, Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_batch_due", "batch_id", "due_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(12), default="assignment")
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    assign_date: Mapped[dt.date] = mapped_column(Date)
    due_date: Mapped[dt.date] = mapped_column(Date)
    is_holiday_homework: Mapped[bool] = mapped_column(Boolean, default=False)
    test_id: Mapped[int | None] = mapped_column(ForeignKey("tests.id", ondelete="SET NULL"), index=True)
    max_marks: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    attachment_path: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(String(60))

    batch: Mapped[Batch] = relationship(lazy="raise")
    test: Mapped[Test | None] = relationship(lazy="raise")


class TaskSubmission(Base):
    __tablename__ = "task_submissions"
    __table_args__ = (UniqueConstraint("task_id", "user_id", name="uq_task_submission"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(12), default="submitted")  # submitted | graded
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)
    marks: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    feedback: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    graded_at: Mapped[dt.datetime | None] = TS()

    task: Mapped[Task] = relationship(lazy="raise")
    user: Mapped[User] = relationship(lazy="raise")


class TestAttempt(Base):
    __tablename__ = "test_attempts"
    __table_args__ = (UniqueConstraint("test_id", "user_id", name="uq_test_attempt"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deadline_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[dt.datetime | None] = TS()
    answers: Mapped[dict] = mapped_column(JSONB, default=dict)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    max_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    status: Mapped[str] = mapped_column(String(12), default="in_progress")  # in_progress|submitted|graded
    feedback: Mapped[str | None] = mapped_column(Text)
    grading_note: Mapped[str | None] = mapped_column(Text)
    per_question: Mapped[dict] = mapped_column(JSONB, default=dict)

    test: Mapped[Test] = relationship(lazy="raise")
    user: Mapped[User] = relationship(lazy="raise")

    @property
    def percent(self) -> float | None:
        if self.score is None or not self.max_score:
            return None
        return round(float(self.score) * 100 / float(self.max_score), 1)


# --------------------------------------------------------------------------- knowledge base (RAG)
class Material(Timestamped, Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id", ondelete="SET NULL"), index=True)
    class_level: Mapped[str | None] = mapped_column(String(40))
    subject: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    file_path: Mapped[str | None] = mapped_column(String(255))
    original_name: Mapped[str | None] = mapped_column(String(255))
    mime: Mapped[str | None] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="processing")  # processing|ready|failed
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(String(60))

    batch: Mapped[Batch | None] = relationship(lazy="raise")


class KnowledgeChunk(Timestamped, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_chunks_source", "source_type", "source_id"),
        Index("ix_chunks_lexical_hnsw", "lexical", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"lexical": "vector_cosine_ops"}),
        Index("ix_chunks_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(20))  # material | meeting | video | lesson
    source_id: Mapped[int] = mapped_column(Integer)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    class_level: Mapped[str | None] = mapped_column(String(40), index=True)
    subject: Mapped[str | None] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    lexical = mapped_column(Vector(LEXICAL_DIM), nullable=False)  # local hashing embedding (always works)
    embedding = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=True)  # gateway embedding (optional)
    embed_model: Mapped[str | None] = mapped_column(String(80))


class DoubtMessage(Timestamped, Base):
    __tablename__ = "doubt_messages"
    __table_args__ = (Index("ix_doubts_user_batch", "user_id", "batch_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id", ondelete="SET NULL"))
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    answered_by: Mapped[str] = mapped_column(String(12), default="ai")  # ai | fallback | teacher
    needs_teacher: Mapped[bool] = mapped_column(Boolean, default=False)
    image_path: Mapped[str | None] = mapped_column(String(255))  # video frame screenshot
    video_seconds: Mapped[int | None] = mapped_column(Integer)  # timestamp the student asked about

    user: Mapped[User] = relationship(lazy="raise")

    @property
    def timestamp_label(self) -> str | None:
        if self.video_seconds is None:
            return None
        m, s = divmod(int(self.video_seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --------------------------------------------------------------------------- chat & moderation
class BatchChatMessage(Timestamped, Base):
    __tablename__ = "batch_chat_messages"
    __table_args__ = (Index("ix_batch_chat_batch", "batch_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sender_name: Mapped[str] = mapped_column(String(120))
    sender_role: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(Text)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class ModerationStrike(Timestamped, Base):
    __tablename__ = "moderation_strikes"
    __table_args__ = (Index("ix_strikes_user_time", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    ip: Mapped[str] = mapped_column(String(64), index=True)
    context: Mapped[str] = mapped_column(String(20))
    excerpt: Mapped[str] = mapped_column(Text)


# --------------------------------------------------------------------------- social: friends, DMs, groups
class Friendship(Timestamped, Base):
    """One row per pair (user_a < user_b). status: pending | accepted | blocked."""

    __tablename__ = "friendships"
    __table_args__ = (UniqueConstraint("user_a", "user_b", name="uq_friend_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_a: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user_b: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    requested_by: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    blocked_by: Mapped[int | None] = mapped_column(Integer)
    responded_at: Mapped[dt.datetime | None] = TS()


class ChatRoom(Timestamped, Base):
    __tablename__ = "chat_rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # direct | group
    name: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_message_at: Mapped[dt.datetime | None] = TS()

    members: Mapped[list[ChatMember]] = relationship(back_populates="room", lazy="raise",
                                                     cascade="all, delete-orphan", passive_deletes=True)


class ChatMember(Base):
    __tablename__ = "chat_members"
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_chat_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("chat_rooms.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")  # owner | member
    last_read_id: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    room: Mapped[ChatRoom] = relationship(back_populates="members", lazy="raise")
    user: Mapped[User] = relationship(lazy="raise")


class ChatMessage(Timestamped, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_room", "room_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("chat_rooms.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sender_name: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------- private AI assistant + reports
class AssistantMessage(Timestamped, Base):
    """Private AI assistant conversation - visible only to its owner."""

    __tablename__ = "assistant_messages"
    __table_args__ = (Index("ix_assistant_user", "user_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(12))  # ai | fallback


class AssistantMemory(Base):
    """Long-term memory: a rolling summary of everything the user told the assistant."""

    __tablename__ = "assistant_memory"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    summarized_until: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Report(Timestamped, Base):
    """Complaints & safety reports (from the AI assistant or the Report button), handled by admins."""

    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_status", "status", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    subject_name: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20), default="complaint")
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # low | medium | high
    source: Mapped[str] = mapped_column(String(20), default="manual")  # ai_assistant | manual | chat
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open|accepted|resolved|dismissed|banned
    admin_note: Mapped[str | None] = mapped_column(Text)
    handled_by: Mapped[str | None] = mapped_column(String(60))
    handled_at: Mapped[dt.datetime | None] = TS()

    reporter: Mapped[User | None] = relationship(lazy="raise", foreign_keys=[reporter_id])
    subject: Mapped[User | None] = relationship(lazy="raise", foreign_keys=[subject_id])


# --------------------------------------------------------------------------- finance
class FeePayment(Timestamped, Base):
    __tablename__ = "fee_payments"
    __table_args__ = (
        UniqueConstraint("user_id", "batch_id", "period", "title", name="uq_fee_period"),
        Index("ix_fee_status", "status", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(80), default="Tuition fee")
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|verifying|paid|overdue|waived
    paid_on: Mapped[dt.date | None] = mapped_column(Date)
    method: Mapped[str | None] = mapped_column(String(20))
    reference: Mapped[str | None] = mapped_column(String(80))
    receipt_no: Mapped[str | None] = mapped_column(String(30), unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str | None] = mapped_column(String(60))
    # manual payments: a screenshot / photo is mandatory proof, verified by the office
    proof_path: Mapped[str | None] = mapped_column(String(255))
    proof_reference: Mapped[str | None] = mapped_column(String(80))  # UTR / transaction id
    proof_submitted_at: Mapped[dt.datetime | None] = TS()
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(60))
    # online payments (Cashfree PG) reflect automatically - no proof needed
    gateway: Mapped[str | None] = mapped_column(String(20))
    gateway_order_id: Mapped[str | None] = mapped_column(String(60), unique=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(60))

    user: Mapped[User] = relationship(lazy="raise")
    batch: Mapped[Batch | None] = relationship(lazy="raise")

    @property
    def is_due(self) -> bool:
        return self.status in ("pending", "overdue")


class SalaryPayment(Timestamped, Base):
    __tablename__ = "salary_payments"
    __table_args__ = (UniqueConstraint("user_id", "period", "title", name="uq_salary_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(80), default="Salary")
    period: Mapped[str] = mapped_column(String(7))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid_on: Mapped[dt.date | None] = mapped_column(Date)
    method: Mapped[str | None] = mapped_column(String(20))
    reference: Mapped[str | None] = mapped_column(String(80))
    receipt_path: Mapped[str | None] = mapped_column(String(255))  # proof for manual payouts
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str | None] = mapped_column(String(60))
    # Cashfree Payouts reflect automatically (no proof needed)
    status: Mapped[str] = mapped_column(String(12), default="paid")  # paid | processing | failed
    payout_transfer_id: Mapped[str | None] = mapped_column(String(60), unique=True)
    payout_reference: Mapped[str | None] = mapped_column(String(80))
    failure_reason: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(lazy="raise")


# --------------------------------------------------------------------------- security
class IPBan(Timestamped, Base):
    __tablename__ = "ip_bans"

    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), unique=True)
    reason: Mapped[str] = mapped_column(Text)
    banned_by: Mapped[str] = mapped_column(String(60))
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[dt.datetime | None] = TS()


class LoginEvent(Timestamped, Base):
    __tablename__ = "login_events"
    __table_args__ = (Index("ix_login_events_time", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(12))
    success: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(String(200))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class OTPCode(Timestamped, Base):
    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(20), default="login")
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[dt.datetime | None] = TS()
    last_sent_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- communication
class EmailLog(Timestamped, Base):
    __tablename__ = "email_log"
    __table_args__ = (Index("ix_email_log_time", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    to_email: Mapped[str] = mapped_column(String(500))
    subject: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10))  # sent | failed | skipped
    error: Mapped[str | None] = mapped_column(Text)


class Communication(Timestamped, Base):
    __tablename__ = "communications"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(10))
    audience: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    recipients: Mapped[int] = mapped_column(Integer, default=0)
    sent_by: Mapped[str] = mapped_column(String(60))


class PTM(Timestamped, Base):
    __tablename__ = "ptms"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    agenda: Mapped[str | None] = mapped_column(Text)
    scheduled_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    mode: Mapped[str] = mapped_column(String(10), default="online")  # online | in_person | hybrid
    location: Mapped[str | None] = mapped_column(String(200))
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="SET NULL"))
    batch_ids: Mapped[list] = mapped_column(JSONB, default=list)
    include_staff: Mapped[bool] = mapped_column(Boolean, default=True)
    include_students: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="scheduled")
    created_by: Mapped[str] = mapped_column(String(60))

    meeting: Mapped[Meeting | None] = relationship(lazy="raise")
    invites: Mapped[list[PTMInvite]] = relationship(
        back_populates="ptm", lazy="raise", cascade="all, delete-orphan", passive_deletes=True)


class PTMInvite(Timestamped, Base):
    __tablename__ = "ptm_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    ptm_id: Mapped[int] = mapped_column(ForeignKey("ptms.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    invitee_name: Mapped[str] = mapped_column(String(120))
    invitee_email: Mapped[str] = mapped_column(String(255))
    invitee_type: Mapped[str] = mapped_column(String(10))  # parent | student | staff
    regarding: Mapped[str | None] = mapped_column(String(120))  # student name for parents
    token: Mapped[str] = mapped_column(String(64), unique=True)
    rsvp: Mapped[str] = mapped_column(String(10), default="pending")  # pending|yes|no|maybe
    responded_at: Mapped[dt.datetime | None] = TS()

    ptm: Mapped[PTM] = relationship(back_populates="invites", lazy="raise")


class RemovalRecord(Timestamped, Base):
    __tablename__ = "removal_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(12))  # removed | reinstated | banned | unbanned | deleted | restored
    template_key: Mapped[str | None] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    email_subject: Mapped[str | None] = mapped_column(String(200))
    email_body: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(60))


class PerformanceAlert(Timestamped, Base):
    __tablename__ = "performance_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(10))  # info | concern | warning
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    parent_message: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(160), unique=True)
    notified: Mapped[list] = mapped_column(JSONB, default=list)
    source: Mapped[str] = mapped_column(String(10), default="rules")  # ai | rules
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(lazy="raise")
    batch: Mapped[Batch | None] = relationship(lazy="raise")


class ContactMessage(Timestamped, Base):
    __tablename__ = "contact_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)


class Notification(Timestamped, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user", "user_id", "is_read", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))
    icon: Mapped[str] = mapped_column(String(40), default="fa-bell")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------- website builder
class SiteSetting(Base):
    __tablename__ = "site_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)


class HomeSection(Base):
    __tablename__ = "home_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str | None] = mapped_column(String(200))
    subtitle: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)  # sanitised HTML
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    show_in_footer: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

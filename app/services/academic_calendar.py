"""Holiday-aware calendar rules.

* kind="holiday"  -> nothing can be assigned or be due on these dates (classes blocked too)
* kind="vacation" -> only *holiday homework* (e.g. summer vacation in May) may be given / be due,
                     and only if the vacation has allow_homework enabled
* weekly off days (WEEKLY_OFF_DAYS, default Sunday) behave like holidays
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import WEEKDAYS, settings
from ..models import Holiday


@dataclass
class DayInfo:
    date: dt.date
    kind: str  # open | holiday | vacation | weekly_off
    label: str = ""
    allow_homework: bool = False

    @property
    def is_blocked(self) -> bool:
        return self.kind in ("holiday", "weekly_off")


async def holidays_between(db: AsyncSession, start: dt.date, end: dt.date) -> list[Holiday]:
    stmt = select(Holiday).where(Holiday.start_date <= end, Holiday.end_date >= start).order_by(Holiday.start_date)
    return list((await db.scalars(stmt)).all())


def classify(day: dt.date, holidays: list[Holiday]) -> DayInfo:
    hard = next((h for h in holidays if h.kind == "holiday" and h.start_date <= day <= h.end_date), None)
    if hard:
        return DayInfo(day, "holiday", hard.title)
    vacation = next((h for h in holidays if h.kind == "vacation" and h.start_date <= day <= h.end_date), None)
    if vacation:
        return DayInfo(day, "vacation", vacation.title, vacation.allow_homework)
    if day.weekday() in settings.weekly_off:
        return DayInfo(day, "weekly_off", f"weekly off ({WEEKDAYS[day.weekday()].title()})")
    return DayInfo(day, "open")


async def day_info(db: AsyncSession, day: dt.date) -> DayInfo:
    return classify(day, await holidays_between(db, day, day))


async def next_open_day(db: AsyncSession, start: dt.date, *, holiday_homework: bool = False) -> dt.date:
    holidays = await holidays_between(db, start, start + dt.timedelta(days=120))
    day = start
    for _ in range(120):
        info = classify(day, holidays)
        if info.kind == "open" or (info.kind == "vacation" and holiday_homework and info.allow_homework):
            return day
        day += dt.timedelta(days=1)
    return start


async def validate_task_dates(db: AsyncSession, assign_date: dt.date, due_date: dt.date,
                              holiday_homework: bool) -> list[str]:
    errors: list[str] = []
    if due_date < assign_date:
        errors.append("The deadline cannot be before the assignment date.")
    holidays = await holidays_between(db, min(assign_date, due_date), max(assign_date, due_date))
    for label, day in (("Assignment date", assign_date), ("Deadline", due_date)):
        info = classify(day, holidays)
        pretty = day.strftime("%a %d %b %Y")
        if info.is_blocked:
            nxt = await next_open_day(db, day, holiday_homework=holiday_homework)
            errors.append(f"{label} {pretty} falls on {info.label} — assignments and deadlines can't be set on "
                          f"holidays. Next available day: {nxt.strftime('%a %d %b')}.")
        elif info.kind == "vacation":
            if not holiday_homework:
                errors.append(f"{label} {pretty} is during {info.label}. Only work marked as "
                              f"“Holiday homework” can be given during vacations.")
            elif not info.allow_homework:
                errors.append(f"{info.label} does not allow holiday homework (enable it on the vacation).")
    return errors


async def validate_class_date(db: AsyncSession, day: dt.date) -> list[str]:
    info = await day_info(db, day)
    if info.is_blocked:
        return [f"{day.strftime('%a %d %b %Y')} is {info.label} — classes can't be scheduled on holidays."]
    return []

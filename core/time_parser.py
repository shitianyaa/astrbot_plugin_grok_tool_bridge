from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class ParsedFutureSchedule:
    run_once: bool
    run_at: str = ""
    cron_expression: str = ""


_MINUTES_LATER_RE = re.compile(r"(\d+)\s*分钟后")
_HOURS_LATER_RE = re.compile(r"(\d+)\s*小时后")
_TIME_OF_DAY_RE = re.compile(
    r"(今天|明天|后天)?\s*(凌晨|早上|上午|中午|下午|晚上)?\s*(\d{1,2})"
    r"(?:\s*([:：])\s*(\d{1,2})|\s*(?:点|时)(?:\s*(\d{1,2})\s*分?)?)?"
)
_DAILY_RE = re.compile(
    r"(每天|每日)(?:\s*(凌晨|早上|上午|中午|下午|晚上))?\s*(\d{1,2})"
    r"(?:\s*([:：])\s*(\d{1,2})|\s*(?:点|时)(?:\s*(\d{1,2})\s*分?)?)?"
)


def infer_future_task_schedule(
    message: str, *, now: datetime
) -> ParsedFutureSchedule | None:
    text = " ".join(str(message or "").split())
    if not text:
        return None

    if match := _MINUTES_LATER_RE.search(text):
        minutes = int(match.group(1))
        if minutes <= 0:
            return None
        return ParsedFutureSchedule(
            run_once=True,
            run_at=(now + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
        )

    if match := _HOURS_LATER_RE.search(text):
        hours = int(match.group(1))
        if hours <= 0:
            return None
        return ParsedFutureSchedule(
            run_once=True,
            run_at=(now + timedelta(hours=hours)).isoformat(timespec="seconds"),
        )

    if match := _DAILY_RE.search(text):
        if not _has_time_context(match.group(0), period=(match.group(2) or "").strip()):
            return None
        hour = _normalize_hour(
            int(match.group(3)),
            period=(match.group(2) or "").strip(),
        )
        minute = _extract_minute(match.group(5), match.group(6))
        if not _valid_hm(hour, minute):
            return None
        return ParsedFutureSchedule(
            run_once=False,
            cron_expression=f"{minute} {hour} * * *",
        )

    if match := _TIME_OF_DAY_RE.search(text):
        day_word = (match.group(1) or "").strip()
        period = (match.group(2) or "").strip()
        if not _has_time_context(match.group(0), period=period):
            return None
        hour = _normalize_hour(int(match.group(3)), period=period)
        minute = _extract_minute(match.group(5), match.group(6))
        if not _valid_hm(hour, minute):
            return None

        base_date = now.date()
        if day_word == "明天":
            base_date = (now + timedelta(days=1)).date()
        elif day_word == "后天":
            base_date = (now + timedelta(days=2)).date()

        candidate = now.replace(
            year=base_date.year,
            month=base_date.month,
            day=base_date.day,
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if not day_word and candidate <= now:
            candidate = candidate + timedelta(days=1)
        return ParsedFutureSchedule(
            run_once=True,
            run_at=candidate.isoformat(timespec="seconds"),
        )

    return None


def extract_future_task_instruction(message: str) -> str:
    text = " ".join(str(message or "").split())
    if not text:
        return ""

    match = _find_schedule_match(text)
    if match is None:
        return text

    start, end = match.span()
    trimmed = (text[:start] + " " + text[end:]).strip()
    trimmed = re.sub(r"^[,，。；;:：、\s]+", "", trimmed)
    trimmed = re.sub(r"[,，。；;:：、\s]+$", "", trimmed)
    trimmed = re.sub(r"\s{2,}", " ", trimmed)
    return trimmed or text


def _normalize_hour(hour: int, *, period: str) -> int:
    if period in {"下午", "晚上"} and 1 <= hour <= 11:
        return hour + 12
    if period == "中午" and 1 <= hour <= 10:
        return hour + 12
    if period == "凌晨" and hour == 12:
        return 0
    return hour


def _extract_minute(colon_minute: str | None, dotted_minute: str | None) -> int:
    return int(colon_minute or dotted_minute or 0)


def _find_schedule_match(text: str) -> re.Match[str] | None:
    for pattern in (
        _MINUTES_LATER_RE,
        _HOURS_LATER_RE,
        _DAILY_RE,
        _TIME_OF_DAY_RE,
    ):
        match = pattern.search(text)
        if match is None:
            continue
        period = ""
        if pattern in {_DAILY_RE, _TIME_OF_DAY_RE}:
            period = (match.group(2) or "").strip()
            if not _has_time_context(match.group(0), period=period):
                continue
        return match
    return None


def _has_time_context(matched_text: str, *, period: str) -> bool:
    if period:
        return True
    return any(token in matched_text for token in (":", "：", "点", "时"))


def _valid_hm(hour: int, minute: int) -> bool:
    return 0 <= hour <= 23 and 0 <= minute <= 59

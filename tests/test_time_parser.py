from datetime import datetime

from core.time_parser import infer_future_task_schedule


def test_infer_future_task_schedule_minutes_later():
    now = datetime.fromisoformat("2026-07-02T15:43:29+08:00")

    schedule = infer_future_task_schedule("1分钟后向我问好", now=now)

    assert schedule is not None
    assert schedule.run_once is True
    assert schedule.run_at == "2026-07-02T15:44:29+08:00"


def test_infer_future_task_schedule_daily_alarm():
    now = datetime.fromisoformat("2026-07-02T15:43:29+08:00")

    schedule = infer_future_task_schedule("每天早上7点叫我起床", now=now)

    assert schedule is not None
    assert schedule.run_once is False
    assert schedule.cron_expression == "0 7 * * *"


def test_infer_future_task_schedule_tomorrow_morning():
    now = datetime.fromisoformat("2026-07-02T15:43:29+08:00")

    schedule = infer_future_task_schedule("明天早上7点叫我起床", now=now)

    assert schedule is not None
    assert schedule.run_once is True
    assert schedule.run_at == "2026-07-03T07:00:00+08:00"


def test_infer_future_task_schedule_ignores_plain_numbers():
    now = datetime.fromisoformat("2026-07-02T15:43:29+08:00")

    schedule = infer_future_task_schedule("提醒我给2个同事发邮件", now=now)

    assert schedule is None

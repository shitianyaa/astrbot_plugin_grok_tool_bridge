from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from astrbot.core.message.components import File

from core.config_manager import ConfigManager
from core.proactive_files import ScheduledFileStore
from core.recent_file_store import RecentFileStore


class FakeEvent:
    def __init__(self, components):
        self.unified_msg_origin = "aiocqhttp:group:test"
        self.message_obj = SimpleNamespace(message=components)


def test_scheduled_file_cleanup_keeps_unexpired_files(tmp_path: Path):
    store = ScheduledFileStore(tmp_path / "plugin-data")
    store.storage_dir.mkdir(parents=True)
    old_file = store.storage_dir / "old.md"
    fresh_file = store.storage_dir / "fresh.md"
    old_file.write_text("old", encoding="utf-8")
    fresh_file.write_text("fresh", encoding="utf-8")

    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=9)).timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))

    removed = store.cleanup(retention_days=7)

    assert removed == 1
    assert not old_file.exists()
    assert fresh_file.exists()


def test_recent_file_rejects_non_allowlisted_suffix(tmp_path: Path):
    source = tmp_path / "payload.exe"
    source.write_text("nope", encoding="utf-8")
    component = File(name="payload.exe", file=str(source))
    store = RecentFileStore(tmp_path / "plugin-data")

    cached = asyncio.run(
        store.capture_from_event(
            FakeEvent([component]), config=ConfigManager({}).config
        )
    )

    assert cached is None


def test_recent_file_rejects_oversized_file(tmp_path: Path):
    source = tmp_path / "large.md"
    source.write_bytes(b"x" * (65 * 1024))
    component = File(name="large.md", file=str(source))
    store = RecentFileStore(tmp_path / "plugin-data")
    config = ConfigManager({"recent_file_max_size_kb": 1}).config

    cached = asyncio.run(
        store.capture_from_event(FakeEvent([component]), config=config)
    )

    assert cached is None


def test_recent_file_tool_copy_stays_under_plugin_data_dir(tmp_path: Path):
    source = tmp_path / "note.md"
    source.write_text("# hello\n", encoding="utf-8")
    component = File(name="note.md", file=str(source))
    data_dir = tmp_path / "plugin-data"
    store = RecentFileStore(data_dir)

    cached = asyncio.run(
        store.capture_from_event(
            FakeEvent([component]), config=ConfigManager({}).config
        )
    )

    assert cached is not None
    assert Path(cached.storage_path).is_relative_to(data_dir)
    assert Path(cached.tool_path).is_relative_to(data_dir)

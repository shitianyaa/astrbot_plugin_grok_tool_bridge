from __future__ import annotations

import shutil
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from astrbot.api import logger

from .recent_file_store import CachedSessionFile


@dataclass(frozen=True)
class ScheduledFile:
    session_id: str
    file_name: str
    source_path: str
    storage_path: str
    tool_path: str
    size_bytes: int
    captured_at: datetime


@dataclass(frozen=True)
class ScheduledFileSummary:
    count: int
    directory: str


class ScheduledFileStore:
    def __init__(self, data_dir: Path | str):
        self.storage_dir = Path(data_dir) / "scheduled_files"

    def persist(
        self,
        cached: CachedSessionFile,
        *,
        retention_days: int,
    ) -> ScheduledFile | None:
        # Prune expired scheduled files before writing a new one. `cleanup`
        # returns early when the directory does not exist yet, so the first
        # persist after restart avoids an unnecessary directory scan.
        self.cleanup(retention_days=retention_days)
        source = Path(cached.tool_path)
        if not source.exists():
            source = Path(cached.storage_path)
        if not source.exists():
            return None

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        extension = Path(cached.file_name).suffix.lower() or source.suffix.lower()
        basename = (
            f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{uuid4().hex[:8]}{extension}"
        )
        destination = self.storage_dir / basename
        try:
            shutil.copy2(source, destination)
            now = datetime.now(timezone.utc)
            os.utime(destination, (now.timestamp(), now.timestamp()))
        except Exception as exc:
            logger.warning(
                "GrokToolBridge failed to persist scheduled file; source=%s error=%s",
                source,
                exc,
            )
            return None

        return ScheduledFile(
            session_id=cached.session_id,
            file_name=cached.file_name,
            source_path=cached.source_path,
            storage_path=str(destination.resolve()),
            tool_path=str(destination.resolve()),
            size_bytes=cached.size_bytes,
            captured_at=now,
        )

    def cleanup(self, *, retention_days: int) -> int:
        if not self.storage_dir.exists():
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        removed = 0
        for path in self.storage_dir.iterdir():
            if not path.is_file():
                continue
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                if modified_at < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                logger.debug(
                    "GrokToolBridge failed to inspect scheduled file: %s", path
                )
        return removed

    def summary(self) -> ScheduledFileSummary:
        if not self.storage_dir.exists():
            return ScheduledFileSummary(count=0, directory=str(self.storage_dir))
        count = sum(1 for path in self.storage_dir.iterdir() if path.is_file())
        return ScheduledFileSummary(count=count, directory=str(self.storage_dir))

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrbot.api import logger
from astrbot.core.message.components import File, Reply
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .config_manager import PluginConfig


@dataclass(frozen=True)
class CachedSessionFile:
    session_id: str
    file_name: str
    source_path: str
    storage_path: str
    tool_path: str
    size_bytes: int
    captured_at: datetime

    def expired(self, *, now: datetime, ttl_seconds: int) -> bool:
        return self.captured_at + timedelta(seconds=ttl_seconds) <= now


class RecentFileStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.storage_dir = self.data_dir / "recent_files"
        self.tool_dir = Path(get_astrbot_temp_path()) / "grok_tool_bridge_recent_files"
        self._by_session: dict[str, CachedSessionFile] = {}

    async def capture_from_event(
        self,
        event: Any,
        *,
        config: PluginConfig,
    ) -> CachedSessionFile | None:
        if not config.recent_file_bridge_enabled:
            return None

        self.cleanup(ttl_seconds=config.recent_file_ttl_seconds)
        latest: CachedSessionFile | None = None
        for component in self._iter_file_components(event):
            cached = await self._capture_component(component, event, config=config)
            if cached is not None:
                latest = cached
        if latest is not None:
            self._replace_session_file(latest.session_id, latest)
        return latest

    def get_recent_file(
        self,
        session_id: str,
        *,
        ttl_seconds: int,
    ) -> CachedSessionFile | None:
        self.cleanup(ttl_seconds=ttl_seconds)
        cached = self._by_session.get(session_id)
        if cached is None:
            return None
        if not Path(cached.tool_path).exists():
            self._drop_session(session_id)
            return None
        return cached

    def cleanup(self, *, ttl_seconds: int) -> None:
        now = datetime.now(timezone.utc)
        expired_sessions = [
            session_id
            for session_id, cached in self._by_session.items()
            if cached.expired(now=now, ttl_seconds=ttl_seconds)
        ]
        for session_id in expired_sessions:
            self._drop_session(session_id)

    def close(self) -> None:
        for session_id in list(self._by_session):
            self._drop_session(session_id)

    async def _capture_component(
        self,
        component: File,
        event: Any,
        *,
        config: PluginConfig,
    ) -> CachedSessionFile | None:
        source_path = await component.get_file()
        if not source_path or not os.path.exists(source_path):
            return None

        file_name = (component.name or Path(source_path).name or "").strip()
        if not file_name:
            return None

        extension = Path(file_name).suffix.lower()
        if extension not in set(config.recent_file_allowed_extensions):
            logger.debug(
                "GrokToolBridge skipped uploaded file outside allowlist; session=%s name=%s",
                getattr(event, "unified_msg_origin", ""),
                file_name,
            )
            return None

        size_bytes = os.path.getsize(source_path)
        if size_bytes > config.recent_file_max_size_kb * 1024:
            logger.warning(
                "GrokToolBridge skipped uploaded file exceeding size limit; session=%s name=%s size=%s limit_kb=%s",
                getattr(event, "unified_msg_origin", ""),
                file_name,
                size_bytes,
                config.recent_file_max_size_kb,
            )
            return None

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.tool_dir.mkdir(parents=True, exist_ok=True)

        basename = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{uuid4().hex[:8]}{extension}"
        storage_path = self.storage_dir / basename
        tool_path = self.tool_dir / basename
        shutil.copy2(source_path, storage_path)
        shutil.copy2(source_path, tool_path)

        return CachedSessionFile(
            session_id=str(getattr(event, "unified_msg_origin", "") or ""),
            file_name=file_name,
            source_path=os.path.abspath(source_path),
            storage_path=str(storage_path.resolve()),
            tool_path=str(tool_path.resolve()),
            size_bytes=size_bytes,
            captured_at=datetime.now(timezone.utc),
        )

    def _replace_session_file(self, session_id: str, cached: CachedSessionFile) -> None:
        previous = self._by_session.get(session_id)
        if previous is not None:
            self._delete_cached_file(previous)
        self._by_session[session_id] = cached

    def _drop_session(self, session_id: str) -> None:
        cached = self._by_session.pop(session_id, None)
        if cached is not None:
            self._delete_cached_file(cached)

    @staticmethod
    def _delete_cached_file(cached: CachedSessionFile) -> None:
        for path in (cached.storage_path, cached.tool_path):
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                logger.debug("GrokToolBridge failed to remove cached file: %s", path)

    @staticmethod
    def _iter_file_components(event: Any) -> list[File]:
        message_chain = getattr(getattr(event, "message_obj", None), "message", None)
        if not isinstance(message_chain, list):
            return []

        files: list[File] = []
        for component in message_chain:
            if isinstance(component, File):
                files.append(component)
                continue
            if isinstance(component, Reply) and getattr(component, "chain", None):
                for reply_component in component.chain:
                    if isinstance(reply_component, File):
                        files.append(reply_component)
        return files

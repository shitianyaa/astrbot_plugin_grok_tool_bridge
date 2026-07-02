from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_AUTO_TOOLS = [
    "future_task",
    "astr_kb_search",
    "astrbot_file_read_tool",
    "astrbot_grep_tool",
]

DEFAULT_MANUAL_TOOLS = [
    *DEFAULT_AUTO_TOOLS,
    "send_message_to_user",
    "astrbot_upload_file",
    "astrbot_download_file",
]

DEFAULT_PROACTIVE_TOOLS = [
    "send_message_to_user",
    *DEFAULT_AUTO_TOOLS,
]

DEFAULT_TARGET_PROVIDER_KEYWORDS = ["grok", "xai"]
DEFAULT_RECENT_FILE_ALLOWED_EXTENSIONS = [
    ".txt",
    ".md",
    ".markdown",
    ".log",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".toml",
    ".csv",
    ".tsv",
    ".pdf",
    ".docx",
    ".epub",
]


@dataclass(frozen=True)
class PluginConfig:
    enabled: bool = True
    auto_mode: bool = True
    proactive_mode: bool = True
    manual_command_enabled: bool = True
    target_provider_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_TARGET_PROVIDER_KEYWORDS)
    )
    router_provider_id: str = ""
    final_provider_id: str = ""
    proactive_agent_provider_id: str = ""
    confidence_threshold: float = 0.65
    max_steps: int = 3
    tool_call_timeout: int = 60
    enabled_auto_tools: list[str] = field(
        default_factory=lambda: list(DEFAULT_AUTO_TOOLS)
    )
    enabled_manual_tools: list[str] = field(
        default_factory=lambda: list(DEFAULT_MANUAL_TOOLS)
    )
    enabled_proactive_tools: list[str] = field(
        default_factory=lambda: list(DEFAULT_PROACTIVE_TOOLS)
    )
    recent_file_bridge_enabled: bool = True
    recent_file_ttl_seconds: int = 1800
    recent_file_max_size_kb: int = 2048
    recent_file_allowed_extensions: list[str] = field(
        default_factory=lambda: list(DEFAULT_RECENT_FILE_ALLOWED_EXTENSIONS)
    )
    auto_process_uploaded_text_file: bool = False
    send_progress_message: bool = False
    debug_mode: bool = False


class ConfigManager:
    def __init__(self, raw_config: Any):
        self.raw_config = raw_config
        self._config = self._load()

    @property
    def config(self) -> PluginConfig:
        return self._config

    def reload(self) -> PluginConfig:
        self._config = self._load()
        return self._config

    def _get(self, key: str, default: Any) -> Any:
        try:
            if hasattr(self.raw_config, "get"):
                return self.raw_config.get(key, default)
        except Exception:
            return default
        try:
            return self.raw_config[key]
        except Exception:
            return default

    def _load(self) -> PluginConfig:
        return PluginConfig(
            enabled=self._bool("enabled", True),
            auto_mode=self._bool("auto_mode", True),
            proactive_mode=self._bool("proactive_mode", True),
            manual_command_enabled=self._bool("manual_command_enabled", True),
            target_provider_keywords=self._string_list(
                "target_provider_keywords",
                DEFAULT_TARGET_PROVIDER_KEYWORDS,
            ),
            router_provider_id=self._str("router_provider_id", ""),
            final_provider_id=self._str("final_provider_id", ""),
            proactive_agent_provider_id=self._str("proactive_agent_provider_id", ""),
            confidence_threshold=self._float("confidence_threshold", 0.65, 0.0, 1.0),
            max_steps=self._int("max_steps", 3, 1, 10),
            tool_call_timeout=self._int("tool_call_timeout", 60, 5, 3600),
            enabled_auto_tools=self._string_list(
                "enabled_auto_tools",
                DEFAULT_AUTO_TOOLS,
            ),
            enabled_manual_tools=self._string_list(
                "enabled_manual_tools",
                DEFAULT_MANUAL_TOOLS,
            ),
            enabled_proactive_tools=self._string_list(
                "enabled_proactive_tools",
                DEFAULT_PROACTIVE_TOOLS,
            ),
            recent_file_bridge_enabled=self._bool("recent_file_bridge_enabled", True),
            recent_file_ttl_seconds=self._int(
                "recent_file_ttl_seconds",
                1800,
                60,
                86400,
            ),
            recent_file_max_size_kb=self._int(
                "recent_file_max_size_kb",
                2048,
                64,
                102400,
            ),
            recent_file_allowed_extensions=self._extension_list(
                "recent_file_allowed_extensions",
                DEFAULT_RECENT_FILE_ALLOWED_EXTENSIONS,
            ),
            auto_process_uploaded_text_file=self._bool(
                "auto_process_uploaded_text_file",
                False,
            ),
            send_progress_message=self._bool("send_progress_message", False),
            debug_mode=self._bool("debug_mode", False),
        )

    def _bool(self, key: str, default: bool) -> bool:
        value = self._get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _str(self, key: str, default: str) -> str:
        value = self._get(key, default)
        return str(value or "").strip()

    def _int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        value = self._get(key, default)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _float(self, key: str, default: float, minimum: float, maximum: float) -> float:
        value = self._get(key, default)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _string_list(self, key: str, default: list[str]) -> list[str]:
        value = self._get(key, default)
        if isinstance(value, str):
            items = [part.strip() for part in value.replace("|", ",").split(",")]
        elif isinstance(value, list):
            items = [str(item).strip() for item in value]
        else:
            items = list(default)
        return [item for item in items if item]

    def _extension_list(self, key: str, default: list[str]) -> list[str]:
        items = self._string_list(key, default)
        normalized: list[str] = []
        seen: set[str] = set()
        for item in items:
            ext = item.lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            if ext not in seen:
                seen.add(ext)
                normalized.append(ext)
        return normalized

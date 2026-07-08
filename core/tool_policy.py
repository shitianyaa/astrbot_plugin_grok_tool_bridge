from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config_manager import DEFAULT_AUTO_TOOLS, DEFAULT_MANUAL_TOOLS


TOOL_PURPOSES = {
    "future_task": "Create, edit, delete, or list reminders and scheduled tasks.",
    "astr_kb_search": "Search AstrBot knowledge base, indexed docs, group rules, and stored reference material.",
    "astrbot_file_read_tool": "Read a specific file explicitly mentioned by the user, such as README, logs, or config files.",
    "astrbot_grep_tool": "Search project files or logs for a keyword, error message, function name, or config key.",
    "send_message_to_user": "Send proactive messages or media. Usually not needed for normal current-session replies.",
    "astrbot_upload_file": "Transfer an existing host file into the sandbox for processing.",
    "astrbot_download_file": "Transfer a sandbox file out to the host and optionally send it to the user.",
}
BRIDGE_TOOL_ALLOWLIST = frozenset(TOOL_PURPOSES)


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    parameters: dict[str, Any]


class ToolPolicy:
    def __init__(self, tool_manager: Any):
        self.tool_manager = tool_manager

    def descriptors(self, allowed_names: list[str]) -> list[ToolDescriptor]:
        descriptors: list[ToolDescriptor] = []
        for name in self._unique_names(allowed_names):
            tool = self.get_tool(name)
            if tool is None:
                continue
            parameters = getattr(tool, "parameters", {}) or {}
            descriptors.append(
                ToolDescriptor(
                    name=name,
                    description=TOOL_PURPOSES.get(
                        name,
                        str(getattr(tool, "description", "") or ""),
                    ),
                    parameters=parameters,
                )
            )
        return descriptors

    def get_tool(self, name: str) -> Any | None:
        if not name:
            return None
        if name not in BRIDGE_TOOL_ALLOWLIST:
            return None
        get_func = getattr(self.tool_manager, "get_func", None)
        if not callable(get_func):
            return None
        try:
            return get_func(name)
        except Exception:
            return None

    def is_allowed(self, name: str, allowed_names: list[str]) -> bool:
        return (
            name in set(self._unique_names(allowed_names))
            and self.get_tool(name) is not None
        )

    def tool_set(self, allowed_names: list[str]):
        from astrbot.api import ToolSet

        tool_set = ToolSet()
        for name in self._unique_names(allowed_names):
            tool = self.get_tool(name)
            if tool is not None:
                tool_set.add_tool(tool)
        return tool_set

    def tool_prompt(self, allowed_names: list[str]) -> str:
        rows = []
        for descriptor in self.descriptors(allowed_names):
            schema = json.dumps(
                descriptor.parameters,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            rows.append(
                f"- {descriptor.name}: {descriptor.description}\n"
                f"  JSON Schema: {schema}"
            )
        return "\n".join(rows)

    @staticmethod
    def _unique_names(names: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for name in names:
            clean = str(name).strip()
            if clean and clean not in seen:
                seen.add(clean)
                result.append(clean)
        return result


def default_auto_tools() -> list[str]:
    return list(DEFAULT_AUTO_TOOLS)


def default_manual_tools() -> list[str]:
    return list(DEFAULT_MANUAL_TOOLS)

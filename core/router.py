from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from astrbot.api import logger

from .json_utils import parse_json_object
from .prompts import ROUTER_SYSTEM_PROMPT, ROUTER_USER_TEMPLATE


@dataclass(frozen=True)
class ToolDecision:
    action: str = "no_tool"
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""

    @property
    def wants_tool(self) -> bool:
        return self.action == "tool_call" and bool(self.tool)


def parse_tool_decision(text: str) -> ToolDecision:
    payload = parse_json_object(text)
    if not payload:
        return ToolDecision(reason="router returned invalid JSON")

    action = str(payload.get("action") or "no_tool").strip()
    tool = str(payload.get("tool") or "").strip()
    args = payload.get("args")
    if not isinstance(args, dict):
        args = {}

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = str(payload.get("reason") or "").strip()
    if action != "tool_call":
        return ToolDecision(
            action="no_tool",
            confidence=confidence,
            reason=reason,
        )

    return ToolDecision(
        action="tool_call",
        tool=tool,
        args=args,
        confidence=confidence,
        reason=reason,
    )


class ToolRouter:
    def __init__(self, context: Any):
        self.context = context

    async def decide(
        self,
        *,
        provider_id: str,
        message: str,
        tool_docs: str,
        tool_results: str,
        now: datetime,
    ) -> ToolDecision:
        prompt = ROUTER_USER_TEMPLATE.format(
            tool_docs=tool_docs or "(no tools available)",
            message=message,
            tool_results=tool_results or "(none)",
            now=now.isoformat(),
        )
        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=ROUTER_SYSTEM_PROMPT,
            )
        except Exception as exc:
            logger.warning(
                "GrokToolBridge router provider call failed; provider_id=%s error=%s",
                provider_id,
                exc,
            )
            return ToolDecision(reason=f"router call failed: {exc}")

        text = str(getattr(response, "completion_text", "") or "")
        return parse_tool_decision(text)

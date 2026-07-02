from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema
from astrbot.api import logger
from astrbot.core.astr_agent_context import AgentContextWrapper, AstrAgentContext
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

from .tool_policy import ToolPolicy


@dataclass(frozen=True)
class ToolExecutionResult:
    tool: str
    args: dict[str, Any]
    content: str
    ok: bool = True
    direct_message_sent: bool = False


class BuiltinToolExecutor:
    def __init__(self, context: Any, policy: ToolPolicy):
        self.context = context
        self.policy = policy

    async def execute(
        self,
        *,
        event: Any,
        tool_name: str,
        args: dict[str, Any],
        allowed_tools: list[str],
        timeout: int,
    ) -> ToolExecutionResult:
        if not self.policy.is_allowed(tool_name, allowed_tools):
            return ToolExecutionResult(
                tool=tool_name,
                args=args,
                content=f"error: tool `{tool_name}` is not allowed or not available.",
                ok=False,
            )

        tool = self.policy.get_tool(tool_name)
        if tool is None:
            return ToolExecutionResult(
                tool=tool_name,
                args=args,
                content=f"error: tool `{tool_name}` is not available.",
                ok=False,
            )

        validation_error = self._validate_args(tool, args)
        if validation_error:
            return ToolExecutionResult(
                tool=tool_name,
                args=args,
                content=f"error: invalid arguments for `{tool_name}`: {validation_error}",
                ok=False,
            )

        run_context = AgentContextWrapper(
            context=AstrAgentContext(
                context=self.context,
                event=event,
            ),
            tool_call_timeout=timeout,
        )

        pieces: list[str] = []
        direct_message_sent = False
        try:
            executor = FunctionToolExecutor.execute(
                tool=tool,
                run_context=run_context,
                tool_call_timeout=timeout,
                **args,
            )
            async for response in executor:
                if response is None:
                    direct_message_sent = True
                    continue
                pieces.extend(self._stringify_tool_response(response))
        except Exception as exc:
            logger.warning("GrokToolBridge tool execution failed: %s", exc)
            return ToolExecutionResult(
                tool=tool_name,
                args=args,
                content=f"error: tool `{tool_name}` execution failed: {exc}",
                ok=False,
            )

        content = "\n".join(piece for piece in pieces if piece).strip()
        if direct_message_sent and not content:
            content = "Tool sent a message directly to the user."
        if not content:
            content = "Tool returned no text content."

        return ToolExecutionResult(
            tool=tool_name,
            args=args,
            content=content,
            ok=True,
            direct_message_sent=direct_message_sent,
        )

    @staticmethod
    def _validate_args(tool: Any, args: dict[str, Any]) -> str:
        schema = getattr(tool, "parameters", None)
        if not isinstance(schema, dict):
            return ""
        try:
            jsonschema.validate(instance=args, schema=schema)
        except jsonschema.ValidationError as exc:
            return exc.message
        return ""

    @staticmethod
    def _stringify_tool_response(response: Any) -> list[str]:
        content_items = getattr(response, "content", None)
        if not content_items:
            return [str(response)]

        pieces: list[str] = []
        for item in content_items:
            text = getattr(item, "text", None)
            if text:
                pieces.append(str(text))
                continue

            mime_type = getattr(item, "mimeType", "") or getattr(item, "mime_type", "")
            data = getattr(item, "data", None) or getattr(item, "blob", None)
            if data and str(mime_type).startswith("image/"):
                pieces.append(f"[image content returned: {mime_type}]")
            elif data:
                pieces.append(f"[binary content returned: {mime_type or 'unknown'}]")
            else:
                pieces.append(str(item))
        return pieces

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.astr_main_agent import _get_session_conv
from astrbot.core.message.message_event_result import MessageChain

from .config_manager import ConfigManager, PluginConfig
from .provider_matcher import is_target_provider
from .prompts import (
    FINAL_SYSTEM_PROMPT,
    FINAL_USER_TEMPLATE,
    FUTURE_TASK_NOTE_SYSTEM_PROMPT,
    FUTURE_TASK_NOTE_USER_TEMPLATE,
    PROACTIVE_TOOL_PREP_SYSTEM_PROMPT,
    PROACTIVE_TOOL_PREP_USER_TEMPLATE,
    PROACTIVE_CONTENT_SYSTEM_PROMPT,
    PROACTIVE_CONTENT_USER_TEMPLATE,
    PROACTIVE_DELIVERY_SYSTEM_PROMPT,
    PROACTIVE_DELIVERY_USER_TEMPLATE,
    PROACTIVE_AGENT_SYSTEM_PROMPT,
)
from .proactive_files import ScheduledFileStore
from .proactive_planner import ProactivePlanner
from .recent_file_store import CachedSessionFile, RecentFileStore
from .router import ToolDecision, ToolRouter
from .time_parser import extract_future_task_instruction, infer_future_task_schedule
from .tool_executor import BuiltinToolExecutor, ToolExecutionResult
from .tool_policy import ToolPolicy


BRIDGE_ACTIVE_EXTRA = "grok_tool_bridge_active"
_PLACEHOLDER_PROMPT_RE = re.compile(r"^\s*(<attachment>|\[ComponentType\.[^\]]+\])\s*$")
_RECENT_FILE_REFERENCE_RE = re.compile(
    r"(刚才|刚刚|上个|上一|上传的|刚发的|刚传的|附件|这份|这个|那份|那个).{0,6}文件"
    r"|attached file|attachment|uploaded file|last file|this file|that file",
    re.IGNORECASE,
)
_GENERIC_TASK_NAMES = {
    "active_agent_task",
    "future_task",
    "future task",
    "task",
    "cron",
    "cron job",
    "提醒",
    "定时任务",
    "任务",
}
_DIAGNOSTIC_COMMANDS = {
    "status": "status",
    "状态": "status",
    "recent-file": "recent-file",
    "recent_file": "recent-file",
    "最近文件": "recent-file",
    "proactive-status": "proactive-status",
    "proactive_status": "proactive-status",
    "主动任务状态": "proactive-status",
    "clear-cache": "clear-cache",
    "clear_cache": "clear-cache",
    "清缓存": "clear-cache",
}


@dataclass
class BridgeRunResult:
    handled: bool
    reply: str = ""
    decisions: list[ToolDecision] = field(default_factory=list)
    tool_results: list[ToolExecutionResult] = field(default_factory=list)
    reason: str = ""


class GrokToolBridgeService:
    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        data_dir: Path | str,
    ):
        self.context = context
        self.config_manager = config_manager
        self.file_store = RecentFileStore(data_dir)
        self.scheduled_file_store = ScheduledFileStore(data_dir)
        self.proactive_planner = ProactivePlanner()

    def close(self) -> None:
        self.file_store.close()

    def manual_command_available(self) -> bool:
        config = self.config_manager.reload()
        return config.enabled and config.manual_command_enabled

    async def capture_recent_files(self, event: Any) -> CachedSessionFile | None:
        config = self.config_manager.reload()
        return await self._capture_recent_files(event, config)

    async def handle_file_message(self, event: Any) -> str:
        config = self.config_manager.reload()
        if not config.enabled or not config.auto_mode:
            return ""
        if event.get_extra(BRIDGE_ACTIVE_EXTRA):
            return ""

        current_file = await self._capture_recent_files(event, config)
        if current_file is None or not config.auto_process_uploaded_text_file:
            return ""

        if self._clean_request_message(self._request_message(event, None)):
            return ""

        provider_id = await self._current_provider_id(event)
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        if not is_target_provider(
            provider_id,
            provider,
            config.target_provider_keywords,
        ):
            return ""

        message = self._append_file_context(
            f"请读取并总结我刚上传的文件 {current_file.file_name}。",
            current_file,
            source="current_message_attachment",
        )
        self._debug_log(
            config,
            "GrokToolBridge auto file bridge start; session=%s provider_id=%s file=%s",
            self._event_session(event),
            provider_id,
            current_file.file_name,
        )
        result = await self.run_bridge(
            event=event,
            message=message,
            allowed_tools=config.enabled_auto_tools,
            manual=True,
            req=None,
            config=config,
        )
        if not result.handled:
            if config.debug_mode:
                logger.debug(
                    "GrokToolBridge skipped auto-process uploaded file; session=%s reason=%s",
                    self._event_session(event),
                    result.reason,
                )
            return ""
        return result.reply

    async def handle_agent_begin(self, event: Any, run_context: Any) -> None:
        del run_context
        config = self.config_manager.reload()
        if not config.enabled or not config.proactive_mode:
            return
        if event.get_extra(BRIDGE_ACTIVE_EXTRA):
            return

        proactive_payload = self._proactive_payload(event)
        if proactive_payload is None:
            return

        provider_id = self._proactive_provider_id(config)
        if not provider_id:
            logger.warning(
                "GrokToolBridge proactive event detected but "
                "proactive_agent_provider_id is not configured; "
                "kind=%s session=%s event=%s",
                proactive_payload["kind"],
                self._event_session(event),
                self._proactive_event_name(proactive_payload),
            )
            return
        if self.context.get_provider_by_id(provider_id) is None:
            logger.warning(
                "GrokToolBridge proactive agent provider not found; "
                "provider_id=%s kind=%s session=%s event=%s",
                provider_id,
                proactive_payload["kind"],
                self._event_session(event),
                self._proactive_event_name(proactive_payload),
            )
            return

        logger.info(
            "GrokToolBridge proactive event accepted; provider_id=%s kind=%s "
            "session=%s event=%s",
            provider_id,
            proactive_payload["kind"],
            self._event_session(event),
            self._proactive_event_name(proactive_payload),
        )

        event.stop_event()
        event.set_extra(BRIDGE_ACTIVE_EXTRA, True)
        try:
            await self._run_proactive_agent(
                event=event,
                provider_id=provider_id,
                payload=proactive_payload,
                config=config,
            )
        finally:
            event.set_extra(BRIDGE_ACTIVE_EXTRA, False)

    async def handle_llm_request(self, event: Any, req: Any) -> None:
        config = self.config_manager.reload()
        if not config.enabled or not config.auto_mode:
            return
        if event.get_extra(BRIDGE_ACTIVE_EXTRA):
            return

        if config.require_at_or_wake and not getattr(
            event, "is_at_or_wake_command", False
        ):
            if config.debug_mode:
                logger.debug(
                    "GrokToolBridge skipped LLM request: not at-or-wake; "
                    "session=%s",
                    self._event_session(event),
                )
            return

        await self._capture_recent_files(event, config)

        provider_id = await self._current_provider_id(event)
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        if not is_target_provider(
            provider_id,
            provider,
            config.target_provider_keywords,
        ):
            if config.debug_mode:
                logger.debug(
                    "GrokToolBridge skipped LLM request: provider not targeted; "
                    "provider_id=%s session=%s keywords=%s",
                    provider_id,
                    self._event_session(event),
                    config.target_provider_keywords,
                )
            return
        if self._should_passthrough_native_tools(provider, config):
            self._debug_log(
                config,
                "GrokToolBridge skipped LLM request: native tool passthrough; "
                "provider_id=%s mode=%s session=%s",
                provider_id,
                config.native_tool_passthrough_mode,
                self._event_session(event),
            )
            return

        message = await self._prepare_bridge_message(
            event=event,
            message=self._request_message(event, req),
            req=req,
            config=config,
        )
        if not message:
            if config.debug_mode:
                logger.debug(
                    "GrokToolBridge skipped LLM request: empty prompt; session=%s",
                    self._event_session(event),
                )
            return

        if config.debug_mode:
            logger.debug(
                "GrokToolBridge routing LLM request; provider_id=%s session=%s "
                "message=%s",
                provider_id,
                self._event_session(event),
                self._short_text(message),
            )

        result = await self.run_bridge(
            event=event,
            message=message,
            allowed_tools=config.enabled_auto_tools,
            manual=False,
            req=req,
            config=config,
        )
        if not result.handled:
            if config.debug_mode:
                logger.debug(
                    "GrokToolBridge did not handle LLM request; reason=%s "
                    "session=%s decisions=%s",
                    result.reason,
                    self._event_session(event),
                    [decision.reason for decision in result.decisions],
                )
            return

        logger.info(
            "GrokToolBridge handled LLM request; session=%s tools=%s",
            self._event_session(event),
            [tool_result.tool for tool_result in result.tool_results],
        )
        event.stop_event()
        if config.send_progress_message and result.tool_results:
            await event.send(MessageChain().message("已调用工具，正在整理结果..."))
        await event.send(MessageChain().message(result.reply))

    async def handle_manual_command(self, event: Any, text: str) -> str:
        config = self.config_manager.reload()
        if not config.enabled:
            return "Grok 工具桥接插件已关闭。"
        if not config.manual_command_enabled:
            return "手动工具桥接命令已关闭。"

        diagnostic_command = self._diagnostic_command_name(text)
        if diagnostic_command:
            if not config.diagnostic_command_enabled:
                return "诊断命令已关闭。"
            return await self._handle_diagnostic_command(
                event,
                diagnostic_command,
                config=config,
            )

        message = await self._prepare_bridge_message(
            event=event,
            message=text.strip(),
            req=None,
            config=config,
        )
        if not message:
            return "请在命令后写明需要处理的内容，例如：/grok工具 明天早上 9 点提醒我交日报"

        self._debug_log(
            config,
            "GrokToolBridge manual bridge start; session=%s message=%s",
            self._event_session(event),
            self._short_text(message),
        )
        result = await self.run_bridge(
            event=event,
            message=message,
            allowed_tools=config.enabled_manual_tools,
            manual=True,
            req=None,
            config=config,
        )
        if not result.handled:
            return result.reason or "没有判断出需要调用的工具。"
        return result.reply

    async def _handle_diagnostic_command(
        self,
        event: Any,
        command: str,
        *,
        config: PluginConfig,
    ) -> str:
        if command == "status":
            return await self._diagnostic_status(event, config=config)
        if command == "recent-file":
            return self._diagnostic_recent_file(event, config=config)
        if command == "proactive-status":
            return self._diagnostic_proactive_status(config)
        if command == "clear-cache":
            return self._diagnostic_clear_cache(event, config=config)
        return "未知诊断命令。可用命令：status、recent-file、proactive-status、clear-cache。"

    async def _diagnostic_status(self, event: Any, *, config: PluginConfig) -> str:
        current_provider_id = await self._current_provider_id(event)
        router_provider_id = (
            config.router_provider_id or current_provider_id or "(none)"
        )
        final_provider_id = config.final_provider_id or current_provider_id or "(none)"
        proactive_provider_id = config.proactive_agent_provider_id or "(none)"
        warnings = self._diagnostic_warnings(config)
        lines = [
            "GrokToolBridge 状态",
            f"enabled: {config.enabled}",
            f"auto_mode: {config.auto_mode}",
            f"require_at_or_wake: {config.require_at_or_wake}",
            f"current_provider: {current_provider_id or '(none)'}",
            f"router_provider: {router_provider_id}",
            f"final_provider: {final_provider_id}",
            f"proactive_provider: {proactive_provider_id}",
            f"proactive_mode_policy: {config.proactive_mode_policy}",
            f"native_tool_passthrough_mode: {config.native_tool_passthrough_mode}",
            f"auto_tools: {', '.join(config.enabled_auto_tools) or '(none)'}",
        ]
        if warnings:
            lines.append(f"warnings: {'; '.join(warnings)}")
        return "\n".join(lines)

    def _diagnostic_recent_file(self, event: Any, *, config: PluginConfig) -> str:
        summary = self.file_store.describe_recent_file(
            self._event_session(event),
            ttl_seconds=config.recent_file_ttl_seconds,
        )
        if summary is None:
            return "最近文件：无。"
        return "\n".join(
            [
                "最近文件",
                f"name: {summary.file_name}",
                f"size_bytes: {summary.size_bytes}",
                f"available: {summary.tool_path_exists}",
                f"captured_at: {summary.captured_at.isoformat(timespec='seconds')}",
                f"expires_at: {summary.expires_at.isoformat(timespec='seconds')}",
            ]
        )

    def _diagnostic_proactive_status(self, config: PluginConfig) -> str:
        scheduled_summary = self.scheduled_file_store.summary()
        provider_status = (
            "configured" if config.proactive_agent_provider_id else "missing"
        )
        return "\n".join(
            [
                "主动任务状态",
                f"proactive_mode: {config.proactive_mode}",
                f"proactive_provider: {provider_status}",
                f"proactive_mode_policy: {config.proactive_mode_policy}",
                f"enabled_proactive_tools: {', '.join(config.enabled_proactive_tools) or '(none)'}",
                f"scheduled_files: {scheduled_summary.count}",
                f"scheduled_file_retention_days: {config.scheduled_file_retention_days}",
            ]
        )

    def _diagnostic_clear_cache(self, event: Any, *, config: PluginConfig) -> str:
        recent_cleared = self.file_store.clear_session(self._event_session(event))
        expired_scheduled = self.scheduled_file_store.cleanup(
            retention_days=config.scheduled_file_retention_days
        )
        return "\n".join(
            [
                "缓存清理完成",
                f"recent_file_cleared: {recent_cleared}",
                f"expired_scheduled_files_removed: {expired_scheduled}",
            ]
        )

    @staticmethod
    def _diagnostic_warnings(config: PluginConfig) -> list[str]:
        warnings: list[str] = []
        if config.auto_mode and not config.enabled_auto_tools:
            warnings.append("enabled_auto_tools is empty")
        if config.proactive_mode and not config.proactive_agent_provider_id:
            warnings.append("proactive_agent_provider_id is empty")
        if (
            config.proactive_mode
            and "send_message_to_user" not in config.enabled_proactive_tools
        ):
            warnings.append("send_message_to_user is not enabled for proactive tasks")
        return warnings

    async def _run_proactive_agent(
        self,
        *,
        event: Any,
        provider_id: str,
        payload: dict[str, Any],
        config: PluginConfig,
    ) -> None:
        if not config.enabled_proactive_tools:
            logger.warning(
                "GrokToolBridge proactive agent has no available tools; "
                "configured_tools=%s kind=%s session=%s event=%s",
                config.enabled_proactive_tools,
                payload["kind"],
                self._event_session(event),
                self._proactive_event_name(payload),
            )
            return

        contexts, system_prompt = await self._build_proactive_context(
            event,
            config=config,
        )
        source_text = self._build_proactive_source_text(
            event=event,
            payload=payload,
            config=config,
        )
        proactive_plan = self.proactive_planner.plan(
            source_text=source_text,
            policy=config.proactive_mode_policy,
        )
        mode = proactive_plan.mode
        content_provider_id = await self._current_provider_id(event) or provider_id
        prepared_material = ""
        if proactive_plan.needs_tool_prep:
            prep_tools = self._select_proactive_prep_tools(
                config.enabled_proactive_tools
            )
            prepared_material = await self._prepare_proactive_material(
                event=event,
                payload=payload,
                source_text=source_text,
                provider_id=provider_id,
                allowed_tools=prep_tools,
                contexts=contexts,
                system_prompt=system_prompt,
                config=config,
            )
            if not prepared_material.strip():
                self._debug_log(
                    config,
                    "GrokToolBridge proactive material prep empty; session=%s mode=%s",
                    self._event_session(event),
                    mode,
                )
                await self._deliver_proactive_content(
                    event=event,
                    payload=payload,
                    provider_id=provider_id,
                    allowed_tools=self._select_proactive_delivery_tools(
                        config.enabled_proactive_tools
                    ),
                    prepared_content=self._build_proactive_material_failure_message(
                        source_text
                    ),
                    contexts=contexts,
                    system_prompt=system_prompt,
                    config=config,
                )
                return

        if proactive_plan.needs_content_generation:
            prepared_content = await self._generate_proactive_content(
                event=event,
                payload=payload,
                provider_id=content_provider_id,
                contexts=contexts,
                system_prompt=system_prompt,
                prepared_material=prepared_material,
                allow_native_search=proactive_plan.allow_native_search,
                config=config,
            )
        else:
            prepared_content = self._build_delivery_only_content(payload, source_text)
        self._debug_log(
            config,
            "GrokToolBridge proactive mode selected; session=%s mode=%s "
            "reason=%s source=%s prepared_material=%s",
            self._event_session(event),
            mode,
            proactive_plan.reason,
            self._short_text(source_text, limit=200),
            self._short_text(prepared_material or "(none)", limit=200),
        )
        await self._deliver_proactive_content(
            event=event,
            payload=payload,
            provider_id=provider_id,
            allowed_tools=self._select_proactive_delivery_tools(
                config.enabled_proactive_tools
            ),
            prepared_content=prepared_content or source_text,
            contexts=contexts,
            system_prompt=system_prompt,
            config=config,
        )

    async def _prepare_proactive_material(
        self,
        *,
        event: Any,
        payload: dict[str, Any],
        source_text: str,
        provider_id: str,
        allowed_tools: list[str],
        contexts: list[dict[str, Any]],
        system_prompt: str,
        config: PluginConfig,
    ) -> str:
        policy = ToolPolicy(self.context.get_llm_tool_manager())
        tool_set = policy.tool_set(allowed_tools)
        if not tool_set:
            self._debug_log(
                config,
                "GrokToolBridge proactive material prep skipped; no tools session=%s",
                self._event_session(event),
            )
            return ""

        prompt = PROACTIVE_TOOL_PREP_USER_TEMPLATE.format(
            event_kind=payload["kind"],
            payload=json.dumps(payload["data"], ensure_ascii=False),
            message=source_text or str(payload.get("message") or ""),
        )
        try:
            response = await self.context.tool_loop_agent(
                event=event,
                chat_provider_id=provider_id,
                prompt=prompt,
                tools=tool_set,
                system_prompt=f"{system_prompt}\n\n{PROACTIVE_TOOL_PREP_SYSTEM_PROMPT}",
                contexts=contexts or None,
                max_steps=config.max_steps,
                tool_call_timeout=config.tool_call_timeout,
            )
        except Exception as exc:
            logger.warning(
                "GrokToolBridge proactive material prep failed; provider_id=%s "
                "kind=%s session=%s event=%s error=%s",
                provider_id,
                payload["kind"],
                self._event_session(event),
                self._proactive_event_name(payload),
                exc,
            )
            return ""

        prepared = str(getattr(response, "completion_text", "") or "").strip()
        self._debug_log(
            config,
            "GrokToolBridge proactive material prep finished; session=%s "
            "provider_id=%s text=%s",
            self._event_session(event),
            provider_id,
            self._short_text(prepared or "(empty)", limit=240),
        )
        return prepared

    async def _deliver_proactive_content(
        self,
        *,
        event: Any,
        payload: dict[str, Any],
        provider_id: str,
        allowed_tools: list[str],
        prepared_content: str,
        contexts: list[dict[str, Any]],
        system_prompt: str,
        config: PluginConfig,
    ) -> None:
        policy = ToolPolicy(self.context.get_llm_tool_manager())
        tool_set = policy.tool_set(allowed_tools)
        if not tool_set:
            await event.send(MessageChain().message(prepared_content))
            logger.info(
                "GrokToolBridge proactive delivery direct-sent final text; "
                "delivery_provider_id=%s kind=%s session=%s event=%s text_len=%s",
                provider_id,
                payload["kind"],
                self._event_session(event),
                self._proactive_event_name(payload),
                len(prepared_content),
            )
            return

        prompt = PROACTIVE_DELIVERY_USER_TEMPLATE.format(
            event_kind=payload["kind"],
            payload=json.dumps(payload["data"], ensure_ascii=False),
            message=str(payload.get("message") or ""),
            prepared_content=prepared_content,
        )
        try:
            logger.info(
                "GrokToolBridge proactive delivery started; delivery_provider_id=%s "
                "tools=%s max_steps=%s timeout=%s kind=%s session=%s event=%s",
                provider_id,
                [tool.name for tool in tool_set.tools],
                config.max_steps,
                config.tool_call_timeout,
                payload["kind"],
                self._event_session(event),
                self._proactive_event_name(payload),
            )
            response = await self.context.tool_loop_agent(
                event=event,
                chat_provider_id=provider_id,
                prompt=prompt,
                tools=tool_set,
                system_prompt=f"{system_prompt}\n\n{PROACTIVE_DELIVERY_SYSTEM_PROMPT}",
                contexts=contexts or None,
                max_steps=config.max_steps,
                tool_call_timeout=config.tool_call_timeout,
            )
        except Exception as exc:
            logger.warning(
                "GrokToolBridge proactive delivery failed; provider_id=%s kind=%s "
                "session=%s event=%s error=%s",
                provider_id,
                payload["kind"],
                self._event_session(event),
                self._proactive_event_name(payload),
                exc,
                exc_info=True,
            )
            await self._send_prepared_content_fallback(
                event=event,
                payload=payload,
                provider_id=provider_id,
                prepared_content=prepared_content,
                reason="delivery_failed",
            )
            return

        # NOTE: `_has_send_oper` is an AstrBot private flag set by `event.send()`
        # and `event.send_typing()` to indicate the event already produced an
        # outgoing message. It is used here to skip the fallback text when a
        # tool (e.g. `send_message_to_user`) has already delivered the reply.
        # Cross-version safety: re-check this attribute if AstrBot renames it.
        if bool(getattr(event, "_has_send_oper", False)):
            logger.info(
                "GrokToolBridge proactive delivery finished; message already sent "
                "by tool; delivery_provider_id=%s kind=%s session=%s event=%s",
                provider_id,
                payload["kind"],
                self._event_session(event),
                self._proactive_event_name(payload),
            )
            return

        text = str(getattr(response, "completion_text", "") or "").strip()
        if text:
            await self._send_prepared_content_fallback(
                event=event,
                payload=payload,
                provider_id=provider_id,
                prepared_content=text,
                reason="delivery_model_returned_text",
            )
        else:
            await self._send_prepared_content_fallback(
                event=event,
                payload=payload,
                provider_id=provider_id,
                prepared_content=prepared_content,
                reason="delivery_model_returned_empty",
            )

    async def _send_prepared_content_fallback(
        self,
        *,
        event: Any,
        payload: dict[str, Any],
        provider_id: str,
        prepared_content: str,
        reason: str,
    ) -> None:
        try:
            await event.send(MessageChain().message(prepared_content))
        except Exception as exc:
            logger.warning(
                "GrokToolBridge proactive direct send failed; reason=%s "
                "delivery_provider_id=%s kind=%s session=%s event=%s error=%s",
                reason,
                provider_id,
                payload["kind"],
                self._event_session(event),
                self._proactive_event_name(payload),
                exc,
                exc_info=True,
            )
            return

        log_method = (
            logger.warning
            if reason
            in {
                "delivery_failed",
                "delivery_model_returned_empty",
            }
            else logger.info
        )
        log_method(
            "GrokToolBridge proactive fallback sent text; reason=%s "
            "delivery_provider_id=%s kind=%s session=%s event=%s text_len=%s",
            reason,
            provider_id,
            payload["kind"],
            self._event_session(event),
            self._proactive_event_name(payload),
            len(prepared_content),
        )

    async def _generate_proactive_content(
        self,
        *,
        event: Any,
        payload: dict[str, Any],
        provider_id: str,
        contexts: list[dict[str, Any]],
        system_prompt: str,
        prepared_material: str,
        allow_native_search: bool,
        config: PluginConfig,
    ) -> str:
        prompt = PROACTIVE_CONTENT_USER_TEMPLATE.format(
            event_kind=payload["kind"],
            payload=json.dumps(payload["data"], ensure_ascii=False),
            message=str(payload.get("message") or ""),
            prepared_material=prepared_material or "(none)",
        )
        self._debug_log(
            config,
            "GrokToolBridge proactive content generation started; session=%s "
            "provider_id=%s kind=%s event=%s",
            self._event_session(event),
            provider_id,
            payload["kind"],
            self._proactive_event_name(payload),
        )
        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                contexts=contexts or None,
                system_prompt=(
                    f"{system_prompt}\n\n{PROACTIVE_CONTENT_SYSTEM_PROMPT}\n\n"
                    f"Native search allowed: {'yes' if allow_native_search else 'no'}."
                ),
            )
        except Exception as exc:
            logger.warning(
                "GrokToolBridge proactive content generation failed; provider_id=%s "
                "kind=%s session=%s event=%s error=%s",
                provider_id,
                payload["kind"],
                self._event_session(event),
                self._proactive_event_name(payload),
                exc,
            )
            return str(payload.get("message") or "").strip()

        text = str(getattr(response, "completion_text", "") or "").strip()
        if text:
            self._debug_log(
                config,
                "GrokToolBridge proactive content generation finished; session=%s "
                "provider_id=%s text=%s",
                self._event_session(event),
                provider_id,
                self._short_text(text, limit=240),
            )
            return text

        self._debug_log(
            config,
            "GrokToolBridge proactive content generation returned empty; session=%s "
            "provider_id=%s fallback_to_message=%s",
            self._event_session(event),
            provider_id,
            self._short_text(str(payload.get("message") or ""), limit=240),
        )
        return str(payload.get("message") or "").strip()

    async def run_bridge(
        self,
        *,
        event: Any,
        message: str,
        allowed_tools: list[str],
        manual: bool,
        req: Any | None,
        config: PluginConfig,
    ) -> BridgeRunResult:
        tool_manager = self.context.get_llm_tool_manager()
        policy = ToolPolicy(tool_manager)
        tool_docs = policy.tool_prompt(allowed_tools)
        if not tool_docs:
            return BridgeRunResult(
                handled=False,
                reason="没有可用的白名单工具。",
            )

        provider_id = await self._current_provider_id(event)
        router_provider_id = config.router_provider_id or provider_id
        if not router_provider_id:
            return BridgeRunResult(
                handled=False,
                reason="没有可用的路由模型 Provider。",
            )

        self._debug_log(
            config,
            "GrokToolBridge bridge started; session=%s manual=%s current_provider_id=%s "
            "router_provider_id=%s final_provider_id=%s allowed_tools=%s max_steps=%s "
            "confidence_threshold=%.2f timeout=%s message=%s",
            self._event_session(event),
            manual,
            provider_id,
            router_provider_id,
            config.final_provider_id or provider_id,
            allowed_tools,
            config.max_steps,
            config.confidence_threshold,
            config.tool_call_timeout,
            self._short_text(message),
        )
        event.set_extra(BRIDGE_ACTIVE_EXTRA, True)
        try:
            return await self._run_bridge_inner(
                event=event,
                message=message,
                allowed_tools=allowed_tools,
                manual=manual,
                req=req,
                config=config,
                policy=policy,
                tool_docs=tool_docs,
                router_provider_id=router_provider_id,
                current_provider_id=provider_id,
            )
        finally:
            event.set_extra(BRIDGE_ACTIVE_EXTRA, False)

    async def _run_bridge_inner(
        self,
        *,
        event: Any,
        message: str,
        allowed_tools: list[str],
        manual: bool,
        req: Any | None,
        config: PluginConfig,
        policy: ToolPolicy,
        tool_docs: str,
        router_provider_id: str,
        current_provider_id: str,
    ) -> BridgeRunResult:
        router = ToolRouter(self.context)
        executor = BuiltinToolExecutor(self.context, policy)
        decisions: list[ToolDecision] = []
        tool_results: list[ToolExecutionResult] = []
        session = self._event_session(event)

        for step_index in range(1, config.max_steps + 1):
            self._debug_log(
                config,
                "GrokToolBridge step=%s router request; session=%s router_provider_id=%s "
                "tool_results=%s",
                step_index,
                session,
                router_provider_id,
                len(tool_results),
            )
            decision = await router.decide(
                provider_id=router_provider_id,
                message=message,
                tool_docs=tool_docs,
                tool_results=self._format_tool_results(tool_results),
                now=datetime.now().astimezone(),
            )
            decision = self._normalize_future_task_decision(decision, message)
            decision = await self._rewrite_future_task_decision_note(
                decision=decision,
                original_message=message,
                provider_id=router_provider_id,
                config=config,
            )
            decision = self._attach_scheduled_file_context_to_future_task_decision(
                decision=decision,
                event=event,
                original_message=message,
                config=config,
            )
            decisions.append(decision)
            self._debug_log(
                config,
                "GrokToolBridge step=%s router decision; session=%s action=%s tool=%s "
                "confidence=%.2f reason=%s args=%s",
                step_index,
                session,
                decision.action,
                decision.tool or "(none)",
                decision.confidence,
                decision.reason or "(none)",
                self._json_preview(decision.args),
            )

            if not decision.wants_tool:
                if tool_results:
                    self._debug_log(
                        config,
                        "GrokToolBridge step=%s finalize after router stop; session=%s",
                        step_index,
                        session,
                    )
                    break
                self._debug_log(
                    config,
                    "GrokToolBridge step=%s no tool selected; session=%s reason=%s",
                    step_index,
                    session,
                    decision.reason or "router decided no_tool",
                )
                return BridgeRunResult(
                    handled=False,
                    decisions=decisions,
                    reason=decision.reason or "router decided no_tool",
                )

            if decision.confidence < config.confidence_threshold and not manual:
                self._debug_log(
                    config,
                    "GrokToolBridge step=%s skipped by confidence; session=%s "
                    "confidence=%.2f threshold=%.2f",
                    step_index,
                    session,
                    decision.confidence,
                    config.confidence_threshold,
                )
                return BridgeRunResult(
                    handled=False,
                    decisions=decisions,
                    reason=(
                        f"router confidence {decision.confidence:.2f} below threshold"
                    ),
                )

            if not policy.is_allowed(decision.tool, allowed_tools):
                self._debug_log(
                    config,
                    "GrokToolBridge step=%s rejected tool; session=%s tool=%s allowed=%s",
                    step_index,
                    session,
                    decision.tool,
                    allowed_tools,
                )
                return BridgeRunResult(
                    handled=manual,
                    reply=f"工具 `{decision.tool}` 不在当前白名单中，已拒绝调用。",
                    decisions=decisions,
                    reason="tool not allowed",
                )

            self._debug_log(
                config,
                "GrokToolBridge step=%s tool execute; session=%s tool=%s timeout=%s args=%s",
                step_index,
                session,
                decision.tool,
                config.tool_call_timeout,
                self._json_preview(decision.args),
            )
            tool_result = await executor.execute(
                event=event,
                tool_name=decision.tool,
                args=decision.args,
                allowed_tools=allowed_tools,
                timeout=config.tool_call_timeout,
            )
            tool_results.append(tool_result)
            self._debug_log(
                config,
                "GrokToolBridge step=%s tool result; session=%s tool=%s ok=%s "
                "direct_message_sent=%s content=%s",
                step_index,
                session,
                tool_result.tool,
                tool_result.ok,
                tool_result.direct_message_sent,
                self._short_text(tool_result.content, limit=240),
            )
            if not tool_result.ok:
                self._debug_log(
                    config,
                    "GrokToolBridge step=%s finalize after tool failure; session=%s tool=%s",
                    step_index,
                    session,
                    tool_result.tool,
                )
                break
            if self._should_finalize_after_tool(decision, tool_result):
                self._debug_log(
                    config,
                    "GrokToolBridge step=%s finalize after tool; session=%s tool=%s action=%s",
                    step_index,
                    session,
                    decision.tool,
                    decision.args.get("action"),
                )
                break

        if not tool_results:
            return BridgeRunResult(
                handled=False,
                decisions=decisions,
                reason="no tool was executed",
            )

        reply = await self._final_reply(
            message=message,
            tool_results=tool_results,
            current_provider_id=current_provider_id,
            final_provider_id=config.final_provider_id,
            req=req,
            config=config,
        )
        self._debug_log(
            config,
            "GrokToolBridge bridge finished; session=%s tools=%s reply=%s",
            session,
            [tool_result.tool for tool_result in tool_results],
            self._short_text(reply, limit=240),
        )
        return BridgeRunResult(
            handled=True,
            reply=reply,
            decisions=decisions,
            tool_results=tool_results,
        )

    async def _final_reply(
        self,
        *,
        message: str,
        tool_results: list[ToolExecutionResult],
        current_provider_id: str,
        final_provider_id: str,
        req: Any | None,
        config: PluginConfig,
    ) -> str:
        provider_id = final_provider_id or current_provider_id
        if not provider_id:
            return self._fallback_reply(tool_results)

        self._debug_log(
            config,
            "GrokToolBridge final reply request; provider_id=%s tool_results=%s",
            provider_id,
            len(tool_results),
        )
        prompt = FINAL_USER_TEMPLATE.format(
            message=message,
            tool_results=self._format_tool_results(tool_results),
        )
        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                contexts=getattr(req, "contexts", None) or None,
                system_prompt=self._final_system_prompt(req),
            )
        except Exception as exc:
            logger.warning("GrokToolBridge final provider call failed: %s", exc)
            return self._fallback_reply(tool_results)

        text = str(getattr(response, "completion_text", "") or "").strip()
        return text or self._fallback_reply(tool_results)

    async def _build_proactive_context(
        self,
        event: Any,
        *,
        config: PluginConfig,
    ) -> tuple[list[dict[str, Any]], str]:
        system_parts = [PROACTIVE_AGENT_SYSTEM_PROMPT]
        contexts: list[dict[str, Any]] = []

        try:
            conversation = await _get_session_conv(
                event=event, plugin_context=self.context
            )
        except Exception as exc:
            logger.debug(
                "GrokToolBridge failed to load proactive conversation: %s", exc
            )
            conversation = None

        if conversation is not None:
            history = self._conversation_history(conversation)
            if history:
                contexts.extend(history)

            persona_prompt, begin_dialogs = await self._resolve_persona_context(
                event,
                conversation,
            )
            if begin_dialogs:
                contexts = begin_dialogs + contexts
            if persona_prompt:
                system_parts.append(f"# Persona Instructions\n\n{persona_prompt}")

            self._debug_log(
                config,
                "GrokToolBridge proactive context prepared; session=%s persona_id=%s "
                "persona_prompt=%s begin_dialogs=%s history_items=%s",
                self._event_session(event),
                getattr(conversation, "persona_id", "") or "(none)",
                self._short_text(persona_prompt or "(none)"),
                len(begin_dialogs),
                len(history),
            )
        else:
            self._debug_log(
                config,
                "GrokToolBridge proactive context prepared; session=%s no conversation",
                self._event_session(event),
            )

        return contexts, "\n\n".join(part for part in system_parts if part.strip())

    async def _resolve_persona_context(
        self,
        event: Any,
        conversation: Any,
    ) -> tuple[str, list[dict[str, Any]]]:
        provider_settings = self.context.get_config(umo=event.unified_msg_origin).get(
            "provider_settings",
            {},
        )
        try:
            (
                _persona_id,
                persona,
                _force_applied,
                _special_default,
            ) = await self.context.persona_manager.resolve_selected_persona(
                umo=event.unified_msg_origin,
                conversation_persona_id=getattr(conversation, "persona_id", None),
                platform_name=event.get_platform_name(),
                provider_settings=provider_settings,
            )
        except Exception as exc:
            logger.debug("GrokToolBridge failed to resolve persona: %s", exc)
            return "", []

        if not persona:
            return "", []

        prompt = str(persona.get("prompt") or "").strip()
        begin_dialogs = copy.deepcopy(persona.get("_begin_dialogs_processed") or [])
        if not isinstance(begin_dialogs, list):
            begin_dialogs = []
        return prompt, begin_dialogs

    async def _capture_recent_files(
        self,
        event: Any,
        config: PluginConfig,
    ) -> CachedSessionFile | None:
        try:
            return await self.file_store.capture_from_event(event, config=config)
        except Exception as exc:
            logger.warning("GrokToolBridge failed to cache uploaded file: %s", exc)
            return None

    async def _prepare_bridge_message(
        self,
        *,
        event: Any,
        message: str,
        req: Any | None,
        config: PluginConfig,
    ) -> str:
        del req
        current_file = await self._capture_recent_files(event, config)
        cleaned = self._clean_request_message(message)

        if current_file is not None:
            if not cleaned and not config.auto_process_uploaded_text_file:
                return ""
            if not cleaned:
                cleaned = f"请读取并总结我刚上传的文件 {current_file.file_name}。"
            return self._append_file_context(
                cleaned,
                current_file,
                source="current_message_attachment",
            )

        if not cleaned:
            return ""

        if self._mentions_recent_file(cleaned):
            recent_file = self.file_store.get_recent_file(
                self._event_session(event),
                ttl_seconds=config.recent_file_ttl_seconds,
            )
            if recent_file is not None:
                return self._append_file_context(
                    cleaned,
                    recent_file,
                    source="recent_session_file",
                )
        return cleaned

    @staticmethod
    def _append_file_context(
        message: str,
        cached: CachedSessionFile,
        *,
        source: str,
    ) -> str:
        return (
            f"{message}\n\n"
            "[Uploaded file context]\n"
            f"source={source}\n"
            f"file_name={cached.file_name}\n"
            f"preferred_path={cached.tool_path}\n"
            "If you need to inspect or summarize this file, use "
            "`astrbot_file_read_tool` with `path=preferred_path`.\n"
            "If the user asks to search inside this file for a keyword, use "
            "`astrbot_grep_tool` with `path=preferred_path`."
        )

    @classmethod
    def _clean_request_message(cls, message: str) -> str:
        cleaned = str(message or "").strip()
        if not cleaned:
            return ""
        if _PLACEHOLDER_PROMPT_RE.match(cleaned):
            return ""
        return cleaned

    @staticmethod
    def _mentions_recent_file(message: str) -> bool:
        return bool(_RECENT_FILE_REFERENCE_RE.search(message or ""))

    def _attach_scheduled_file_context_to_future_task_decision(
        self,
        *,
        decision: ToolDecision,
        event: Any,
        original_message: str,
        config: PluginConfig,
    ) -> ToolDecision:
        if decision.tool != "future_task" or not decision.args:
            return decision
        action = str(decision.args.get("action") or "").strip().lower()
        if action != "create":
            return decision

        args = dict(decision.args)
        note = str(args.get("note") or "").strip()
        combined_text = f"{original_message}\n{note}"
        if "preferred_path=" in combined_text and "scheduled_files" in combined_text:
            return decision
        if not self._mentions_recent_file(combined_text):
            return decision

        recent_file = self.file_store.get_recent_file(
            self._event_session(event),
            ttl_seconds=config.recent_file_ttl_seconds,
        )
        if recent_file is None:
            return decision

        scheduled_file = self.scheduled_file_store.persist(
            recent_file,
            retention_days=config.scheduled_file_retention_days,
        )
        if scheduled_file is None:
            return decision

        args["note"] = self._append_file_context(
            self._strip_uploaded_file_context(
                note or extract_future_task_instruction(original_message)
            ),
            scheduled_file,
            source="scheduled_session_file",
        )
        self._debug_log(
            config,
            "GrokToolBridge future_task scheduled file persisted; session=%s "
            "file=%s retention_days=%s",
            self._event_session(event),
            scheduled_file.file_name,
            config.scheduled_file_retention_days,
        )
        return ToolDecision(
            action=decision.action,
            tool=decision.tool,
            args=args,
            confidence=decision.confidence,
            reason=decision.reason,
        )

    @staticmethod
    def _strip_uploaded_file_context(message: str) -> str:
        return str(message or "").split("[Uploaded file context]", 1)[0].strip()

    @staticmethod
    def _should_finalize_after_tool(
        decision: ToolDecision,
        tool_result: ToolExecutionResult,
    ) -> bool:
        if not tool_result.ok:
            return True
        if decision.tool != "future_task":
            return False
        action = str(decision.args.get("action") or "").strip().lower()
        return action in {"create", "edit", "delete"}

    @classmethod
    def _normalize_future_task_decision(
        cls,
        decision: ToolDecision,
        message: str,
    ) -> ToolDecision:
        if decision.tool != "future_task" or not decision.args:
            return decision

        action = str(decision.args.get("action") or "").strip().lower()
        if action not in {"create", "edit"}:
            return decision

        args = dict(decision.args)
        original_message = cls._collapse_whitespace(message)
        execution_message = extract_future_task_instruction(original_message)
        note = str(args.get("note") or "").strip()
        if action == "create":
            args["note"] = execution_message or original_message or note
            schedule = infer_future_task_schedule(
                original_message,
                now=datetime.now().astimezone(),
            )
            if schedule is not None:
                args["run_once"] = schedule.run_once
                if schedule.run_at:
                    args["run_at"] = schedule.run_at
                    args.pop("cron_expression", None)
                if schedule.cron_expression:
                    args["cron_expression"] = schedule.cron_expression
                    args.pop("run_at", None)
        elif not note:
            args["note"] = original_message

        name = str(args.get("name") or "").strip()
        if action == "create" and (not name or name.lower() in _GENERIC_TASK_NAMES):
            args["name"] = cls._suggest_future_task_name(
                execution_message or args.get("note") or message
            )

        return ToolDecision(
            action=decision.action,
            tool=decision.tool,
            args=args,
            confidence=decision.confidence,
            reason=decision.reason,
        )

    async def _rewrite_future_task_decision_note(
        self,
        *,
        decision: ToolDecision,
        original_message: str,
        provider_id: str,
        config: PluginConfig,
    ) -> ToolDecision:
        if decision.tool != "future_task" or not decision.args:
            return decision

        action = str(decision.args.get("action") or "").strip().lower()
        if action != "create":
            return decision

        args = dict(decision.args)
        fallback_note = str(args.get("note") or "").strip()
        if not fallback_note:
            return decision

        prompt = FUTURE_TASK_NOTE_USER_TEMPLATE.format(
            message=original_message,
            draft=fallback_note,
        )
        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=FUTURE_TASK_NOTE_SYSTEM_PROMPT,
            )
        except Exception as exc:
            self._debug_log(
                config,
                "GrokToolBridge future_task note rewrite failed; provider_id=%s error=%s",
                provider_id,
                exc,
            )
            return decision

        rewritten_note = str(getattr(response, "completion_text", "") or "").strip()
        rewritten_note = self._strip_wrapped_text(rewritten_note)
        if not rewritten_note:
            return decision

        args["note"] = rewritten_note
        self._debug_log(
            config,
            "GrokToolBridge future_task note rewritten; provider_id=%s before=%s after=%s",
            provider_id,
            self._short_text(fallback_note, limit=200),
            self._short_text(rewritten_note, limit=200),
        )
        return ToolDecision(
            action=decision.action,
            tool=decision.tool,
            args=args,
            confidence=decision.confidence,
            reason=decision.reason,
        )

    @classmethod
    def _suggest_future_task_name(cls, source: Any, limit: int = 18) -> str:
        text = cls._collapse_whitespace(source)
        if not text:
            return "未来任务"
        for separator in ("。", "！", "？", ".", "!", "?", "\n", "，", ",", "；", ";"):
            if separator in text:
                text = text.split(separator, 1)[0].strip()
                break
        text = re.sub(
            r"^(请|麻烦|帮我|记得|到点|以后|之后|每天|每周|每晚|明天|今晚|早上|下午|晚上)+",
            "",
            text,
        ).strip()
        if not text:
            text = cls._collapse_whitespace(source)
        return text[:limit] if len(text) > limit else text

    @staticmethod
    def _collapse_whitespace(value: Any) -> str:
        return " ".join(str(value or "").split()).strip()

    def _final_system_prompt(self, req: Any | None) -> str:
        original = str(getattr(req, "system_prompt", "") or "").strip()
        if not original:
            return FINAL_SYSTEM_PROMPT
        return f"{original}\n\n# Bridge Reply Policy\n\n{FINAL_SYSTEM_PROMPT}"

    async def _current_provider_id(self, event: Any) -> str:
        try:
            return await self.context.get_current_chat_provider_id(
                event.unified_msg_origin
            )
        except Exception:
            return ""

    def _should_passthrough_native_tools(
        self,
        provider: Any,
        config: PluginConfig,
    ) -> bool:
        mode = config.native_tool_passthrough_mode
        if mode == "off":
            return False

        supports_native_tools = self._provider_supports_native_tools(provider)
        if mode == "log_only":
            if supports_native_tools:
                logger.info(
                    "GrokToolBridge native tool capability detected; "
                    "native_tool_passthrough_mode=log_only"
                )
            return False
        return supports_native_tools

    @staticmethod
    def _provider_supports_native_tools(provider: Any) -> bool:
        if provider is None:
            return False
        capability_names = {
            "supports_tool_calling",
            "support_tool_calling",
            "supports_tools",
            "support_tools",
            "tool_calling",
        }
        if isinstance(provider, dict):
            return any(bool(provider.get(name)) for name in capability_names)
        return any(bool(getattr(provider, name, False)) for name in capability_names)

    @staticmethod
    def _proactive_provider_id(config: PluginConfig) -> str:
        return config.proactive_agent_provider_id

    @classmethod
    def _proactive_source_text(cls, payload: dict[str, Any]) -> str:
        parts = [str(payload.get("message") or "").strip()]
        data = payload.get("data")
        if isinstance(data, dict):
            note = str(data.get("note") or "").strip()
            if note:
                parts.append(note)
            name = str(data.get("name") or "").strip()
            if name:
                parts.append(name)
        return cls._collapse_whitespace(" ".join(part for part in parts if part))

    def _build_proactive_source_text(
        self,
        *,
        event: Any,
        payload: dict[str, Any],
        config: PluginConfig,
    ) -> str:
        source_text = self._proactive_source_text(payload)
        if not source_text or "preferred_path=" in source_text:
            return source_text
        if not self._mentions_recent_file(source_text):
            return source_text

        recent_file = self.file_store.get_recent_file(
            self._event_session(event),
            ttl_seconds=config.recent_file_ttl_seconds,
        )
        if recent_file is None:
            return source_text
        return self._append_file_context(
            source_text,
            recent_file,
            source="recent_session_file",
        )

    @classmethod
    def _select_proactive_execution_mode(cls, payload: dict[str, Any]) -> str:
        return (
            ProactivePlanner()
            .plan(
                source_text=cls._proactive_source_text(payload),
                policy="auto",
            )
            .mode
        )

    @staticmethod
    def _select_proactive_prep_tools(allowed_tools: list[str]) -> list[str]:
        non_prep_tools = {
            "send_message_to_user",
            "future_task",
            "astrbot_upload_file",
            "astrbot_download_file",
        }
        return [name for name in allowed_tools if name not in non_prep_tools]

    @staticmethod
    def _select_proactive_delivery_tools(allowed_tools: list[str]) -> list[str]:
        delivery_tools = {
            "send_message_to_user",
            "astrbot_upload_file",
            "astrbot_download_file",
        }
        return [name for name in allowed_tools if name in delivery_tools]

    @classmethod
    def _build_proactive_material_failure_message(cls, source_text: str) -> str:
        if cls._mentions_recent_file(source_text):
            return (
                "这次定时任务需要读取你之前发过的文件，但到执行时我没有拿到可用的文件上下文。"
                "请重新发送文件后再创建这条定时任务。"
            )
        return (
            "这次定时任务依赖本地文件、知识库或项目检索结果，但执行时没有成功获取到所需材料。"
            "请检查相关资源后重新创建任务。"
        )

    @classmethod
    def _build_delivery_only_content(
        cls,
        payload: dict[str, Any],
        source_text: str,
    ) -> str:
        data = payload.get("data")
        note = ""
        if isinstance(data, dict):
            note = str(data.get("note") or "").strip()
        text = note or str(payload.get("message") or "").strip() or source_text
        cleaned = cls._collapse_whitespace(text)
        if not cleaned:
            return "提醒时间到了。"
        if re.match(r"^(提醒|记得|请|该|到点)", cleaned):
            return cleaned
        return f"提醒：{cleaned}"

    @classmethod
    def _proactive_payload(cls, event: Any) -> dict[str, Any] | None:
        cron_job = event.get_extra("cron_job")
        if isinstance(cron_job, dict):
            rendered = cls._render_dynamic_tokens(copy.deepcopy(cron_job))
            return {
                "kind": "cron_job",
                "data": rendered,
                "message": cls._render_dynamic_tokens(event.get_message_str()),
            }

        background_result = event.get_extra("background_task_result")
        if isinstance(background_result, dict):
            rendered = cls._render_dynamic_tokens(copy.deepcopy(background_result))
            return {
                "kind": "background_task_result",
                "data": rendered,
                "message": cls._render_dynamic_tokens(event.get_message_str()),
            }

        return None

    @classmethod
    def _render_dynamic_tokens(cls, value: Any) -> Any:
        now = datetime.now().astimezone()
        today_text = now.strftime("%Y-%m-%d")
        now_text = now.isoformat(timespec="seconds")

        if isinstance(value, str):
            return value.replace("{{today}}", today_text).replace("{{now}}", now_text)
        if isinstance(value, list):
            return [cls._render_dynamic_tokens(item) for item in value]
        if isinstance(value, dict):
            return {
                key: cls._render_dynamic_tokens(item) for key, item in value.items()
            }
        return value

    @staticmethod
    def _conversation_history(conversation: Any) -> list[dict[str, Any]]:
        raw_history = getattr(conversation, "history", "")
        if not raw_history:
            return []
        try:
            history = json.loads(raw_history)
        except Exception:
            return []
        return history if isinstance(history, list) else []

    @staticmethod
    def _event_session(event: Any) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "")

    @staticmethod
    def _proactive_event_name(payload: dict[str, Any]) -> str:
        data = payload.get("data")
        if not isinstance(data, dict):
            return ""
        for key in ("name", "id", "task_id", "tool_name"):
            value = data.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _short_text(text: str, limit: int = 120) -> str:
        cleaned = " ".join(str(text).split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit] + "..."

    @staticmethod
    def _strip_wrapped_text(text: str) -> str:
        cleaned = str(text or "").strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 3:
                cleaned = "\n".join(lines[1:-1]).strip()
        return cleaned.strip("` \n")

    @staticmethod
    def _json_preview(payload: Any, limit: int = 240) -> str:
        try:
            rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception:
            rendered = str(payload)
        return GrokToolBridgeService._short_text(rendered, limit=limit)

    @staticmethod
    def _debug_log(config: PluginConfig, message: str, *args: Any) -> None:
        if not config.debug_mode:
            return
        logger.info(message, *args)

    @staticmethod
    def _diagnostic_command_name(text: str) -> str:
        head = str(text or "").strip().split(maxsplit=1)[0].lower()
        return _DIAGNOSTIC_COMMANDS.get(head, "")

    @staticmethod
    def _request_message(event: Any, req: Any) -> str:
        prompt = str(getattr(req, "prompt", "") or "").strip()
        if prompt:
            return prompt
        try:
            return str(event.get_message_str() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _format_tool_results(tool_results: list[ToolExecutionResult]) -> str:
        if not tool_results:
            return ""
        parts = []
        for index, result in enumerate(tool_results, start=1):
            args = json.dumps(result.args, ensure_ascii=False)
            parts.append(
                f"{index}. tool={result.tool}\n"
                f"   args={args}\n"
                f"   ok={result.ok}\n"
                f"   result:\n{result.content}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _fallback_reply(tool_results: list[ToolExecutionResult]) -> str:
        if not tool_results:
            return "没有可用的工具结果。"
        last = tool_results[-1]
        if len(last.content) <= 1500:
            return last.content
        return last.content[:1500] + "\n...(结果过长，已截断)"

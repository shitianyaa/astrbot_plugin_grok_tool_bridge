from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrbot.core.message.components import File

from core.bridge_service import GrokToolBridgeService
from core.config_manager import ConfigManager
from core.router import ToolDecision


class DummyContext:
    def __init__(self):
        self.persona_manager = SimpleNamespace(
            resolve_selected_persona=self._resolve_selected_persona
        )
        self.llm_response_text = ""
        self.llm_calls: list[dict] = []
        self.tool_loop_calls: list[dict] = []

    def get_config(self, umo=None):
        return {"provider_settings": {}}

    def get_provider_by_id(self, provider_id):
        return {"id": provider_id} if provider_id else None

    def get_llm_tool_manager(self):
        return None

    async def get_current_chat_provider_id(self, unified_msg_origin):
        del unified_msg_origin
        return "grok-test"

    async def llm_generate(self, **kwargs):
        self.llm_calls.append(kwargs)
        return SimpleNamespace(completion_text=self.llm_response_text)

    async def tool_loop_agent(self, **kwargs):
        self.tool_loop_calls.append(kwargs)
        return SimpleNamespace(completion_text="")

    async def _resolve_selected_persona(
        self,
        *,
        umo,
        conversation_persona_id,
        platform_name,
        provider_settings,
    ):
        del umo, conversation_persona_id, platform_name, provider_settings
        return (
            "friendly-helper",
            {
                "prompt": "请保持轻松亲切、自然体贴的语气。",
                "_begin_dialogs_processed": [
                    {"role": "assistant", "content": "早上好，今天也一起加油。"}
                ],
            },
            None,
            False,
        )


class FakeEvent:
    def __init__(
        self,
        extras,
        *,
        message="提醒我交日报",
        session="aiocqhttp:group:test",
        components=None,
    ):
        self.extras = extras
        self.message = message
        self.unified_msg_origin = session
        self.message_obj = SimpleNamespace(message=components or [])
        self.stopped = False
        self.sent_messages: list[Any] = []
        self.is_at_or_wake_command = True

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_message_str(self):
        return self.message

    def get_platform_name(self):
        return "aiocqhttp"

    def stop_event(self):
        self.stopped = True

    async def send(self, message_chain):
        self.sent_messages.append(message_chain)


def test_proactive_payload_detects_cron_job():
    event = FakeEvent({"cron_job": {"id": "job-1", "note": "提醒我交日报"}})

    payload = GrokToolBridgeService._proactive_payload(event)

    assert payload == {
        "kind": "cron_job",
        "data": {"id": "job-1", "note": "提醒我交日报"},
        "message": "提醒我交日报",
    }


def test_proactive_payload_renders_today_placeholder():
    event = FakeEvent(
        {"cron_job": {"id": "job-1", "note": "今天是{{today}}，记得叫我起床"}}
    )

    payload = GrokToolBridgeService._proactive_payload(event)

    assert payload["kind"] == "cron_job"
    assert "{{today}}" not in payload["data"]["note"]
    assert "今天是" in payload["data"]["note"]


def test_proactive_provider_requires_explicit_provider(tmp_path: Path):
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager(
            {
                "router_provider_id": "router",
                "final_provider_id": "final",
            }
        ),
        data_dir=tmp_path,
    )

    assert service._proactive_provider_id(service.config_manager.config) == ""


def test_proactive_provider_uses_explicit_provider(tmp_path: Path):
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({"proactive_agent_provider_id": "tool-agent"}),
        data_dir=tmp_path,
    )

    assert service._proactive_provider_id(service.config_manager.config) == "tool-agent"


def test_handle_agent_begin_stops_event_after_proactive_takeover(
    tmp_path: Path, monkeypatch
):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({"proactive_agent_provider_id": "tool-agent"}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent(
        {"cron_job": {"id": "job-1", "note": "提醒我喝水"}},
        message="提醒我喝水",
    )
    calls: list[dict] = []

    async def fake_run_proactive_agent(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(service, "_run_proactive_agent", fake_run_proactive_agent)

    asyncio.run(service.handle_agent_begin(event, run_context=SimpleNamespace()))

    assert calls
    assert event.stopped is True


def test_select_proactive_execution_mode_prefers_grok_for_realtime_info():
    payload = {
        "kind": "cron_job",
        "data": {"note": "给我推送成都市温江区明天的天气和注意事项"},
        "message": "三分钟后给我推送成都市温江区明天的天气和注意事项",
    }

    assert (
        GrokToolBridgeService._select_proactive_execution_mode(payload) == "grok_first"
    )


def test_select_proactive_execution_mode_prefers_tools_for_file_tasks():
    payload = {
        "kind": "cron_job",
        "data": {"note": "读取我刚上传的文件并总结"},
        "message": "十分钟后读取我刚上传的文件并总结",
    }

    assert (
        GrokToolBridgeService._select_proactive_execution_mode(payload) == "tool_first"
    )


def test_select_proactive_prep_tools_excludes_future_task_mutations():
    tools = GrokToolBridgeService._select_proactive_prep_tools(
        [
            "send_message_to_user",
            "future_task",
            "astr_kb_search",
            "astrbot_file_read_tool",
            "astrbot_upload_file",
        ]
    )

    assert tools == ["astr_kb_search", "astrbot_file_read_tool"]


def test_build_proactive_source_text_reuses_recent_file_context(tmp_path: Path):
    source = tmp_path / "note.md"
    source.write_text("# hello\nworld\n", encoding="utf-8")
    component = File(name="note.md", file=str(source))
    file_event = FakeEvent({}, message="", components=[component])
    proactive_event = FakeEvent({}, message="十分钟后总结我刚上传的文件")
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )

    asyncio.run(service.capture_recent_files(file_event))
    source_text = service._build_proactive_source_text(
        event=proactive_event,
        payload={
            "kind": "cron_job",
            "data": {"note": "总结我刚上传的文件"},
            "message": "十分钟后总结我刚上传的文件",
        },
        config=service.config_manager.config,
    )

    assert "preferred_path=" in source_text
    assert "note.md" in source_text


def test_prepare_proactive_material_uses_resolved_source_text(
    tmp_path: Path, monkeypatch
):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({"proactive_agent_provider_id": "tool-agent"}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="十分钟后总结我刚上传的文件")
    payload = {
        "kind": "cron_job",
        "data": {"id": "job-1", "note": "总结我刚上传的文件"},
        "message": "十分钟后总结我刚上传的文件",
    }

    class FakePolicy:
        def __init__(self, tool_manager):
            del tool_manager

        def tool_set(self, names):
            del names
            return SimpleNamespace(
                tools=[SimpleNamespace(name="astrbot_file_read_tool")]
            )

    monkeypatch.setattr("core.bridge_service.ToolPolicy", FakePolicy)

    asyncio.run(
        service._prepare_proactive_material(
            event=event,
            payload=payload,
            source_text=(
                "总结我刚上传的文件\n\n"
                "[Uploaded file context]\n"
                "source=recent_session_file\n"
                "file_name=note.md\n"
                "preferred_path=C:/tmp/note.md\n"
            ),
            provider_id="tool-agent",
            allowed_tools=["astrbot_file_read_tool"],
            contexts=[],
            system_prompt="persona",
            config=service.config_manager.config,
        )
    )

    assert "preferred_path=C:/tmp/note.md" in context.tool_loop_calls[0]["prompt"]


def test_proactive_flow_uses_current_provider_for_content_and_tool_model_for_delivery(
    tmp_path: Path, monkeypatch
):
    context = DummyContext()
    context.llm_response_text = "明天温江区有小雨，出门记得带伞。"
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({"proactive_agent_provider_id": "tool-agent"}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="三分钟后给我推送明天天气")
    payload = {
        "kind": "cron_job",
        "data": {"id": "job-1", "note": "给我推送明天天气"},
        "message": "给我推送明天天气",
    }

    class FakePolicy:
        def __init__(self, tool_manager):
            del tool_manager

        def tool_set(self, names):
            del names
            return SimpleNamespace(tools=[SimpleNamespace(name="send_message_to_user")])

    async def fake_current_provider_id(event):
        del event
        return "grok-current"

    async def fake_build_proactive_context(event, *, config):
        del event, config
        return [], "persona"

    monkeypatch.setattr("core.bridge_service.ToolPolicy", FakePolicy)
    monkeypatch.setattr(service, "_current_provider_id", fake_current_provider_id)
    monkeypatch.setattr(
        service, "_build_proactive_context", fake_build_proactive_context
    )

    asyncio.run(
        service._run_proactive_agent(
            event=event,
            provider_id="tool-agent",
            payload=payload,
            config=service.config_manager.config,
        )
    )

    assert context.llm_calls[0]["chat_provider_id"] == "grok-current"
    assert context.tool_loop_calls[0]["chat_provider_id"] == "tool-agent"
    assert "明天温江区有小雨，出门记得带伞。" in context.tool_loop_calls[0]["prompt"]


def test_proactive_tool_first_failure_does_not_generate_fake_content(
    tmp_path: Path, monkeypatch
):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({"proactive_agent_provider_id": "tool-agent"}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="十分钟后总结我刚上传的文件")
    payload = {
        "kind": "cron_job",
        "data": {"id": "job-1", "note": "总结我刚上传的文件"},
        "message": "十分钟后总结我刚上传的文件",
    }

    async def fake_current_provider_id(event):
        del event
        return "grok-current"

    async def fake_build_proactive_context(event, *, config):
        del event, config
        return [], "persona"

    async def fake_prepare_material(**kwargs):
        del kwargs
        return ""

    async def fail_generate_content(**kwargs):
        raise AssertionError("should not generate content when tool prep fails")

    monkeypatch.setattr(service, "_current_provider_id", fake_current_provider_id)
    monkeypatch.setattr(
        service, "_build_proactive_context", fake_build_proactive_context
    )
    monkeypatch.setattr(service, "_prepare_proactive_material", fake_prepare_material)
    monkeypatch.setattr(service, "_generate_proactive_content", fail_generate_content)

    asyncio.run(
        service._run_proactive_agent(
            event=event,
            provider_id="tool-agent",
            payload=payload,
            config=service.config_manager.config,
        )
    )

    assert context.llm_calls == []
    assert event.sent_messages


def test_proactive_delivery_falls_back_when_tool_model_raises(
    tmp_path: Path, monkeypatch
):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({"proactive_agent_provider_id": "tool-agent"}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="提醒我")
    payload = {
        "kind": "cron_job",
        "data": {"id": "job-1", "note": "提醒我"},
        "message": "提醒我",
    }

    class FakePolicy:
        def __init__(self, tool_manager):
            del tool_manager

        def tool_set(self, names):
            del names
            return SimpleNamespace(tools=[SimpleNamespace(name="send_message_to_user")])

    async def fail_tool_loop_agent(**kwargs):
        del kwargs
        raise RuntimeError("delivery tool agent failed")

    monkeypatch.setattr("core.bridge_service.ToolPolicy", FakePolicy)
    monkeypatch.setattr(context, "tool_loop_agent", fail_tool_loop_agent)

    asyncio.run(
        service._deliver_proactive_content(
            event=event,
            payload=payload,
            provider_id="tool-agent",
            allowed_tools=["send_message_to_user"],
            prepared_content="请记得喝水",
            contexts=[],
            system_prompt="persona",
            config=service.config_manager.config,
        )
    )

    assert event.sent_messages


def test_proactive_delivery_only_skips_content_generation(tmp_path: Path, monkeypatch):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager(
            {
                "proactive_agent_provider_id": "tool-agent",
                "proactive_mode_policy": "delivery_only",
            }
        ),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="提醒我喝水")
    payload = {
        "kind": "cron_job",
        "data": {"id": "job-1", "note": "喝水"},
        "message": "提醒我喝水",
    }

    class FakePolicy:
        def __init__(self, tool_manager):
            del tool_manager

        def tool_set(self, names):
            del names
            return None

    monkeypatch.setattr("core.bridge_service.ToolPolicy", FakePolicy)

    asyncio.run(
        service._run_proactive_agent(
            event=event,
            provider_id="tool-agent",
            payload=payload,
            config=service.config_manager.config,
        )
    )

    assert context.llm_calls == []
    assert event.sent_messages


def test_future_task_decision_preserves_full_note_and_derives_name():
    message = (
        "叫醒服务 早上7点，用轻松可爱的方式叫老大起床，可以提醒吃早餐、"
        "说一些鼓励的话，每天换换花样。"
    )
    decision = ToolDecision(
        action="tool_call",
        tool="future_task",
        args={"action": "create", "name": "提醒"},
        confidence=0.9,
        reason="schedule",
    )

    normalized = GrokToolBridgeService._normalize_future_task_decision(
        decision,
        message,
    )

    assert "轻松可爱的方式" in normalized.args["note"]
    assert "每天换换花样" in normalized.args["note"]
    assert normalized.args["name"] != "提醒"
    assert normalized.args["name"]


def test_future_task_create_overrides_short_router_note_with_full_message():
    message = (
        "叫醒服务 早上7点，用轻松可爱的方式叫老大起床，可以提醒吃早餐、"
        "说一些鼓励的话，每天换换花样。不要重复。"
    )
    decision = ToolDecision(
        action="tool_call",
        tool="future_task",
        args={"action": "create", "name": "叫醒服务", "note": "早上7点叫醒老大"},
        confidence=0.9,
        reason="schedule",
    )

    normalized = GrokToolBridgeService._normalize_future_task_decision(
        decision,
        message,
    )

    assert normalized.args["note"] != "早上7点叫醒老大"
    assert "轻松可爱的方式" in normalized.args["note"]
    assert "不要重复" in normalized.args["note"]


def test_future_task_create_uses_execution_instruction_not_raw_schedule_message():
    decision = ToolDecision(
        action="tool_call",
        tool="future_task",
        args={"action": "create", "name": "提醒"},
        confidence=0.9,
        reason="schedule",
    )

    normalized = GrokToolBridgeService._normalize_future_task_decision(
        decision,
        "1分钟后向我问好",
    )

    assert normalized.args["note"] == "向我问好"
    assert normalized.args["run_at"] != ""


def test_rewrite_future_task_decision_note_uses_model_output(tmp_path: Path):
    context = DummyContext()
    context.llm_response_text = "请主动向我问好，语气自然一点。"
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )
    decision = ToolDecision(
        action="tool_call",
        tool="future_task",
        args={
            "action": "create",
            "name": "问好提醒",
            "note": "向我问好",
            "run_once": True,
            "run_at": "2026-07-02T15:44:29+08:00",
        },
        confidence=0.9,
        reason="schedule",
    )

    rewritten = asyncio.run(
        service._rewrite_future_task_decision_note(
            decision=decision,
            original_message="1分钟后向我问好，语气自然一点。",
            provider_id="router",
            config=service.config_manager.config,
        )
    )

    assert rewritten.args["note"] == "请主动向我问好，语气自然一点。"


def test_future_task_create_persists_recent_file_context(tmp_path: Path, monkeypatch):
    source = tmp_path / "note.md"
    source.write_text("# title\nhello\n", encoding="utf-8")
    component = File(name="note.md", file=str(source))
    file_event = FakeEvent({}, message="", components=[component])
    event = FakeEvent({}, message="1分钟后总结我刚上传的文件")
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )
    asyncio.run(service.capture_recent_files(file_event))
    bridge_message = asyncio.run(
        service._prepare_bridge_message(
            event=event,
            message=event.message,
            req=None,
            config=service.config_manager.config,
        )
    )
    execute_calls: list[dict] = []

    class FakeRouter:
        def __init__(self, context):
            del context

        async def decide(self, **kwargs):
            del kwargs
            return ToolDecision(
                action="tool_call",
                tool="future_task",
                args={
                    "action": "create",
                    "name": "文件总结",
                    "note": "总结我刚上传的文件",
                },
                confidence=0.9,
                reason="schedule",
            )

    class FakeExecutor:
        def __init__(self, context, policy):
            del context, policy

        async def execute(self, **kwargs):
            execute_calls.append(kwargs)
            return SimpleNamespace(
                tool="future_task",
                args=kwargs["args"],
                content="scheduled",
                ok=True,
                direct_message_sent=False,
            )

    class FakePolicy:
        def is_allowed(self, name, allowed_names):
            del allowed_names
            return name == "future_task"

    async def fake_final_reply(**kwargs):
        del kwargs
        return "done"

    monkeypatch.setattr("core.bridge_service.ToolRouter", FakeRouter)
    monkeypatch.setattr("core.bridge_service.BuiltinToolExecutor", FakeExecutor)
    monkeypatch.setattr(service, "_final_reply", fake_final_reply)

    result = asyncio.run(
        service._run_bridge_inner(
            event=event,
            message=bridge_message,
            allowed_tools=["future_task"],
            manual=False,
            req=None,
            config=service.config_manager.config,
            policy=FakePolicy(),
            tool_docs="- future_task",
            router_provider_id="router",
            current_provider_id="grok",
        )
    )

    assert result.handled is True
    note = execute_calls[0]["args"]["note"]
    assert "preferred_path=" in note
    assert "scheduled_files" in note
    assert "note.md" in note
    assert "grok_tool_bridge_recent_files" not in note
    path = note.split("preferred_path=", 1)[1].splitlines()[0]
    assert Path(path).exists()


def test_prepare_bridge_message_uses_recent_uploaded_file(tmp_path: Path):
    source = tmp_path / "note.md"
    source.write_text("# title\nhello\n", encoding="utf-8")
    component = File(name="note.md", file=str(source))
    event = FakeEvent(
        {},
        message="",
        components=[component],
    )
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )

    config = service.config_manager.config
    bridge_message = asyncio.run(
        service._prepare_bridge_message(
            event=event,
            message="帮我总结一下",
            req=None,
            config=config,
        )
    )

    assert "帮我总结一下" in bridge_message
    assert "preferred_path=" in bridge_message
    assert "astrbot_file_read_tool" in bridge_message


def test_prepare_bridge_message_resolves_recent_file_reference(tmp_path: Path):
    source = tmp_path / "rules.txt"
    source.write_text("群规第一条\n", encoding="utf-8")
    component = File(name="rules.txt", file=str(source))
    file_event = FakeEvent({}, message="", components=[component])
    chat_event = FakeEvent({}, message="总结我刚才发的文件")
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )

    config = service.config_manager.config
    asyncio.run(service.capture_recent_files(file_event))
    bridge_message = asyncio.run(
        service._prepare_bridge_message(
            event=chat_event,
            message="总结我刚才发的文件",
            req=None,
            config=config,
        )
    )

    assert "总结我刚才发的文件" in bridge_message
    assert "recent_session_file" in bridge_message
    assert "rules.txt" in bridge_message


def test_build_proactive_context_includes_persona_and_history(
    tmp_path: Path, monkeypatch
):
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="叫醒我", session="aiocqhttp:private:test")
    conversation = SimpleNamespace(
        history='[{"role":"user","content":"昨晚记得提醒我早睡"}]',
        persona_id="friendly-helper",
    )

    async def fake_get_session_conv(event, plugin_context):
        del event, plugin_context
        return conversation

    monkeypatch.setattr("core.bridge_service._get_session_conv", fake_get_session_conv)

    contexts, system_prompt = asyncio.run(
        service._build_proactive_context(
            event,
            config=service.config_manager.config,
        )
    )

    assert contexts[0]["content"] == "早上好，今天也一起加油。"
    assert contexts[1]["content"] == "昨晚记得提醒我早睡"
    assert "轻松亲切" in system_prompt


def test_handle_file_message_auto_processes_file_only_message(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "note.md"
    source.write_text("# title\nhello\n", encoding="utf-8")
    component = File(name="note.md", file=str(source))
    event = FakeEvent(
        {}, message="", components=[component], session="aiocqhttp:group:test"
    )
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({"auto_process_uploaded_text_file": True}),
        data_dir=tmp_path / "plugin-data",
    )

    async def fake_current_provider_id(event):
        del event
        return "grok-test"

    async def fake_run_bridge(*, event, message, allowed_tools, manual, req, config):
        del event, allowed_tools, req, config
        assert manual is True
        assert "请读取并总结我刚上传的文件 note.md" in message
        return SimpleNamespace(handled=True, reply="summary")

    monkeypatch.setattr(service, "_current_provider_id", fake_current_provider_id)
    monkeypatch.setattr(service, "run_bridge", fake_run_bridge)

    reply = asyncio.run(service.handle_file_message(event))

    assert reply == "summary"


def test_manual_status_diagnostic_does_not_call_llm(tmp_path: Path):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="/grok工具 status")

    reply = asyncio.run(service.handle_manual_command(event, "status"))

    assert "GrokToolBridge 状态" in reply
    assert context.llm_calls == []
    assert context.tool_loop_calls == []


def test_manual_recent_file_diagnostic_reports_empty(tmp_path: Path):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="/grok工具 recent-file")

    reply = asyncio.run(service.handle_manual_command(event, "recent-file"))

    assert reply == "最近文件：无。"
    assert context.llm_calls == []


def test_manual_command_availability_follows_config(tmp_path: Path):
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({"enabled": False}),
        data_dir=tmp_path / "plugin-data",
    )

    assert service.manual_command_available() is False

    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({"manual_command_enabled": False}),
        data_dir=tmp_path / "plugin-data",
    )

    assert service.manual_command_available() is False


def test_native_tool_passthrough_auto_skips_bridge(tmp_path: Path, monkeypatch):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({"native_tool_passthrough_mode": "auto"}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="明天早上9点提醒我")

    def fake_get_provider_by_id(provider_id):
        return {"id": provider_id, "supports_tool_calling": True}

    async def fail_run_bridge(**kwargs):
        del kwargs
        raise AssertionError("native passthrough should skip bridge")

    monkeypatch.setattr(context, "get_provider_by_id", fake_get_provider_by_id)
    monkeypatch.setattr(service, "run_bridge", fail_run_bridge)

    asyncio.run(
        service.handle_llm_request(event, SimpleNamespace(prompt=event.message))
    )

    assert event.stopped is False
    assert event.sent_messages == []


def test_handle_llm_request_skipped_when_not_at_or_wake(tmp_path: Path, monkeypatch):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="明天早上9点提醒我")
    event.is_at_or_wake_command = False

    async def fail_run_bridge(**kwargs):
        del kwargs
        raise AssertionError("not-at-or-wake should skip bridge")

    monkeypatch.setattr(service, "run_bridge", fail_run_bridge)

    asyncio.run(
        service.handle_llm_request(event, SimpleNamespace(prompt=event.message))
    )

    assert event.stopped is False
    assert event.sent_messages == []


def test_handle_llm_request_at_or_wake_passes_gate(tmp_path: Path, monkeypatch):
    context = DummyContext()
    service = GrokToolBridgeService(
        context=context,
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="明天早上9点提醒我")
    event.is_at_or_wake_command = True

    calls: list[dict] = []

    async def fake_run_bridge(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(handled=False, reply="", decisions=[], reason="")

    monkeypatch.setattr(service, "run_bridge", fake_run_bridge)

    asyncio.run(
        service.handle_llm_request(event, SimpleNamespace(prompt=event.message))
    )

    assert calls, "run_bridge should be reached when at-or-wake is True"


def test_run_bridge_stops_after_future_task_create(tmp_path: Path, monkeypatch):
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="明天早上7点叫我起床")
    execute_calls: list[dict] = []
    decisions = iter(
        [
            ToolDecision(
                action="tool_call",
                tool="future_task",
                args={
                    "action": "create",
                    "name": "叫醒服务",
                    "note": "明天早上7点叫我起床",
                },
                confidence=0.9,
                reason="schedule",
            ),
            ToolDecision(
                action="tool_call",
                tool="future_task",
                args={
                    "action": "create",
                    "name": "叫醒服务",
                    "note": "明天早上7点叫我起床",
                },
                confidence=0.9,
                reason="schedule again",
            ),
        ]
    )

    class FakeRouter:
        def __init__(self, context):
            del context

        async def decide(self, **kwargs):
            del kwargs
            return next(decisions)

    class FakeExecutor:
        def __init__(self, context, policy):
            del context, policy

        async def execute(self, **kwargs):
            execute_calls.append(kwargs)
            return SimpleNamespace(
                tool="future_task",
                args=kwargs["args"],
                content="scheduled",
                ok=True,
                direct_message_sent=False,
            )

    class FakePolicy:
        def is_allowed(self, name, allowed_names):
            del allowed_names
            return name == "future_task"

    async def fake_final_reply(**kwargs):
        del kwargs
        return "done"

    monkeypatch.setattr("core.bridge_service.ToolRouter", FakeRouter)
    monkeypatch.setattr("core.bridge_service.BuiltinToolExecutor", FakeExecutor)
    monkeypatch.setattr(service, "_final_reply", fake_final_reply)

    result = asyncio.run(
        service._run_bridge_inner(
            event=event,
            message="明天早上7点叫我起床",
            allowed_tools=["future_task"],
            manual=False,
            req=None,
            config=ConfigManager({}).config,
            policy=FakePolicy(),
            tool_docs="- future_task",
            router_provider_id="router",
            current_provider_id="grok",
        )
    )

    assert result.handled is True
    assert result.reply == "done"
    assert len(execute_calls) == 1

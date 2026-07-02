from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from astrbot.core.message.components import File

from core.bridge_service import GrokToolBridgeService
from core.config_manager import ConfigManager
from core.router import ToolDecision


class DummyContext:
    def __init__(self):
        self.persona_manager = SimpleNamespace(
            resolve_selected_persona=self._resolve_selected_persona
        )

    def get_config(self, umo=None):
        return {"provider_settings": {}}

    def get_provider_by_id(self, provider_id):
        return {"id": provider_id} if provider_id else None

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
            "catgirl",
            {
                "prompt": "请像猫娘一样自然亲切地说话。",
                "_begin_dialogs_processed": [
                    {"role": "assistant", "content": "喵，早上好。"}
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

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def get_message_str(self):
        return self.message

    def get_platform_name(self):
        return "aiocqhttp"

    def stop_event(self):
        self.stopped = True


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

    assert normalized.args["note"] == message
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

    assert normalized.args["note"] == message


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


def test_build_proactive_context_includes_persona_and_history(tmp_path: Path, monkeypatch):
    service = GrokToolBridgeService(
        context=DummyContext(),
        config_manager=ConfigManager({}),
        data_dir=tmp_path / "plugin-data",
    )
    event = FakeEvent({}, message="叫醒我", session="aiocqhttp:private:test")
    conversation = SimpleNamespace(
        history='[{"role":"user","content":"昨晚记得提醒我早睡"}]',
        persona_id="catgirl",
    )

    async def fake_get_session_conv(event, plugin_context):
        del event, plugin_context
        return conversation

    monkeypatch.setattr("core.bridge_service._get_session_conv", fake_get_session_conv)

    contexts, system_prompt = asyncio.run(service._build_proactive_context(event))

    assert contexts[0]["content"] == "喵，早上好。"
    assert contexts[1]["content"] == "昨晚记得提醒我早睡"
    assert "猫娘" in system_prompt


def test_handle_file_message_auto_processes_file_only_message(tmp_path: Path, monkeypatch):
    source = tmp_path / "note.md"
    source.write_text("# title\nhello\n", encoding="utf-8")
    component = File(name="note.md", file=str(source))
    event = FakeEvent({}, message="", components=[component], session="aiocqhttp:group:test")
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

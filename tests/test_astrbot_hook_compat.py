from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot_plugin_grok_tool_bridge.main import GrokToolBridgePlugin


class _FakeImageCommand:
    def __init__(self) -> None:
        self.called = False

    async def run(self, event):
        self.called = True
        yield event.plain_result("ok")


class _FakeBridgeService:
    def __init__(self) -> None:
        self.calls = []

    async def handle_llm_request(self, event, req) -> None:
        self.calls.append((event, req))


class _FakeEvent:
    def plain_result(self, text: str) -> str:
        return text


@pytest.mark.asyncio
async def test_grok_image_accepts_extra_runtime_argument():
    plugin = SimpleNamespace(image_command=_FakeImageCommand())
    event = _FakeEvent()

    results = [
        item
        async for item in GrokToolBridgePlugin.grok_image(
            plugin,
            event,
            "prompt-from-runtime",
        )
    ]

    assert results == ["ok"]
    assert plugin.image_command.called is True


@pytest.mark.asyncio
async def test_on_llm_request_ignores_extra_runtime_argument():
    service = _FakeBridgeService()
    plugin = SimpleNamespace(bridge_service=service)
    event = object()
    req = object()

    await GrokToolBridgePlugin.on_llm_request(
        plugin,
        event,
        req,
        "provider-from-runtime",
    )

    assert service.calls == [(event, req)]

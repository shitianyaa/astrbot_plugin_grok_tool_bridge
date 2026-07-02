from __future__ import annotations

from astrbot.core.config.astrbot_config import AstrBotConfig as CoreAstrBotConfig
from astrbot.core.message.components import File, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent as CoreMessageEvent
from astrbot.core.star.filter.custom_filter import CustomFilter

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from .core.bridge_service import GrokToolBridgeService
from .core.config_manager import ConfigManager


class HasFileAttachmentFilter(CustomFilter):
    def filter(self, event: CoreMessageEvent, cfg: CoreAstrBotConfig) -> bool:
        message_chain = getattr(getattr(event, "message_obj", None), "message", None)
        if not message_chain:
            return False
        for component in message_chain:
            if isinstance(component, File):
                return True
            if isinstance(component, Reply) and getattr(component, "chain", None):
                if any(isinstance(reply_component, File) for reply_component in component.chain):
                    return True
        return False


class GrokToolBridgePlugin(Star):
    """Bridge AstrBot builtin tools for Grok-like models without native tool calls."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.data_dir = StarTools.get_data_dir(self.name)
        self.config_manager = ConfigManager(config)
        self.bridge_service = GrokToolBridgeService(
            context,
            self.config_manager,
            self.data_dir,
        )

    async def initialize(self) -> None:
        logger.info("GrokToolBridgePlugin initialized")

    async def terminate(self) -> None:
        self.bridge_service.close()
        logger.info("GrokToolBridgePlugin terminated")

    @filter.custom_filter(HasFileAttachmentFilter, False, priority=200)
    async def cache_recent_files(self, event: AstrMessageEvent) -> None:
        """Cache the latest uploaded text-like file for this session."""
        reply = await self.bridge_service.handle_file_message(event)
        if reply:
            event.stop_event()
            await event.send(MessageChain().message(reply))

    @filter.on_agent_begin(priority=100)
    async def on_agent_begin(
        self,
        event: AstrMessageEvent,
        run_context,
    ) -> None:
        """Run proactive cron/background tasks through a tool-capable assistant."""
        await self.bridge_service.handle_agent_begin(event, run_context)

    @filter.on_llm_request()
    async def on_llm_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """Intercept Grok LLM requests and run a JSON-planned tool bridge."""
        await self.bridge_service.handle_llm_request(event, req)

    @filter.command("grok工具", alias={"groktool", "工具桥接"})
    async def grok_tool(self, event: AstrMessageEvent, text: str = ""):
        """Manually run the Grok tool bridge for one request."""
        event.stop_event()
        reply = await self.bridge_service.handle_manual_command(event, text)
        yield event.plain_result(reply)

"""Grok 生图命令封装

移植自 astrbot_plugin_grok_suite (作者: 沐沐沐倾) 的 on_image_request 流程，
裁剪为文生图。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger

from .client import GrokImageClient
from .errors import ErrorTranslator
from .media import MediaHandler
from .permissions import PermissionChecker


class GrokImageCommand:
    """封装 /grok生图 命令的完整流程"""

    MAX_IMAGE_COUNT = 10
    MAX_PROMPT_LENGTH = 4000

    def __init__(self, config: Any, data_dir: Path | str):
        self.conf = config
        self._media_handler = MediaHandler(Path(data_dir))
        self._api_client = GrokImageClient(config)
        self._error_translator = ErrorTranslator()

    async def close(self) -> None:
        await self._api_client.close()

    # ==================== 消息段处理 ====================

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        """将配置值转换为布尔值"""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
        return default

    @staticmethod
    def _is_segment_type(seg: Any, type_name: str) -> bool:
        """兼容不同平台的消息段类型判断"""
        cls = getattr(Comp, type_name, None)
        if cls is not None:
            try:
                if isinstance(seg, cls):
                    return True
            except Exception:
                pass
        return seg.__class__.__name__.lower() == type_name.lower()

    def _iter_event_segments(self, event: Any) -> list[Any]:
        """展开消息链与引用链，返回统一的消息段列表"""
        message_list = (
            getattr(getattr(event, "message_obj", None), "message", None) or []
        )
        segments: list[Any] = []
        for seg in message_list:
            if self._is_segment_type(seg, "Reply") and getattr(seg, "chain", None):
                for inner in seg.chain:
                    segments.append(inner)
            else:
                segments.append(seg)
        return segments

    def _has_image_input(self, event: Any) -> bool:
        """判断命令消息或引用消息中是否包含图片。"""
        return any(
            self._is_segment_type(seg, "Image")
            for seg in self._iter_event_segments(event)
        )

    # ==================== 参数解析 ====================

    def _parse_image_params(self, text: str) -> tuple[str, dict[str, Any]]:
        """解析生图参数，只消费开头的合法数量。"""
        params: dict[str, Any] = {"n": 1}
        parts = text.split()
        if not parts:
            return "", params

        if parts[0].isdigit() and 1 <= int(parts[0]) <= self.MAX_IMAGE_COUNT:
            params["n"] = int(parts[0])
            parts = parts[1:]

        return " ".join(parts).strip(), params

    # ==================== 主流程 ====================

    async def run(self, event: Any):
        """Grok 生图: /grok生图 [数量] 提示词"""
        # 权限检查前置
        can_proceed, _ = PermissionChecker.check_event_permissions(event, self.conf)
        if not can_proceed:
            yield event.plain_result("❌ 当前会话无权限使用此功能")
            return

        api_key = str(self.conf.get("grok_api_key", "")).strip()
        if not api_key:
            yield event.plain_result("❌ 未配置 API 密钥")
            return

        raw_input = event.message_str.strip()
        cmd = "grok生图"
        user_input = (
            raw_input[len(cmd) :].strip() if raw_input.startswith(cmd) else raw_input
        )

        if not user_input:
            yield event.plain_result("❌ 请输入提示词\n示例: /grok生图 一只可爱的猫咪")
            return

        if self._has_image_input(event):
            yield event.plain_result("❌ 暂不支持图生图，请移除图片后重试")
            return

        prompt_text, params = self._parse_image_params(user_input)
        if not prompt_text:
            yield event.plain_result("❌ 请输入提示词")
            return

        if len(prompt_text) > self.MAX_PROMPT_LENGTH:
            # 截断而非直接拒绝
            prompt_text = prompt_text[: self.MAX_PROMPT_LENGTH - 3] + "..."
            yield event.plain_result(
                f"⚠️ 提示词过长，已自动截断至 {self.MAX_PROMPT_LENGTH} 字符"
            )

        n = params["n"]
        yield event.plain_result(f"🎨 正在进行 [文生图] · {n}张 ...")

        results, error = await self._api_client.generate_image(prompt_text, n=n)

        if error:
            yield event.plain_result(
                f"❌ [文生图] 生成失败: {self._error_translator.translate(error)}"
            )
            return

        if not results:
            yield event.plain_result("❌ 未获取到图片")
            return

        # 处理所有图片
        images_data = []
        failed_count = 0
        for i, (url_or_path, img_bytes) in enumerate(results):
            if img_bytes:
                images_data.append((url_or_path or f"image_{i}", img_bytes))
            elif url_or_path:
                downloaded = await self._media_handler.download_media(url_or_path)
                if downloaded:
                    images_data.append((url_or_path, downloaded))
                else:
                    failed_count += 1

        if not images_data:
            yield event.plain_result("❌ 图片下载失败，请到后台查看")
            return

        save_media = self._to_bool(self.conf.get("save_media", False))

        # 单张图片直接发送，多张使用合并转发
        if len(images_data) == 1:
            async for result in self._media_handler.save_and_send_media(
                event, images_data[0][0], images_data[0][1], "image", save_media
            ):
                yield result
        else:
            async for result in self._media_handler.send_images_forward(
                event, images_data, failed_count, save_media
            ):
                yield result

        logger.debug(f"[grok生图] 已发送 {len(images_data)} 张图片，模式=文生图")

"""Grok 生图命令封装

移植自 astrbot_plugin_grok_suite (作者: 沐沐沐倾) 的 on_image_request 流程，
裁剪为文生图 / 图生图。
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

    DEFAULT_TEXT_IMAGE_SIZE = "720x1280"
    SIZE_TO_ASPECT_RATIO = {
        "1280x720": "16:9",
        "720x1280": "9:16",
        "1792x1024": "3:2",
        "1024x1792": "2:3",
        "1024x1024": "1:1",
    }
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

    @staticmethod
    def _extract_segment_sources(seg: Any) -> list[str]:
        """从消息段中提取资源 URL/路径"""
        sources: list[str] = []
        for key in ("file", "url", "path", "src"):
            value = getattr(seg, key, None)
            if isinstance(value, str) and value.strip():
                sources.append(value.strip())
        return list(dict.fromkeys(sources))

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

    async def _load_segment_payload(self, seg: Any) -> tuple[bytes | None, str | None]:
        """从消息段中读取媒体数据"""
        direct_data = getattr(seg, "data", None)
        if isinstance(direct_data, (bytes, bytearray)) and direct_data:
            return bytes(direct_data), None

        for src in self._extract_segment_sources(seg):
            payload = await self._media_handler.load_bytes(src)
            if payload:
                return payload, src
        return None, None

    async def _get_images_from_event(
        self, event: Any, max_count: int = 1
    ) -> list[bytes]:
        """从事件中获取图片"""
        images: list[bytes] = []
        if max_count <= 0:
            return images

        for seg in self._iter_event_segments(event):
            if not self._is_segment_type(seg, "Image"):
                continue
            payload, _ = await self._load_segment_payload(seg)
            if payload:
                images.append(payload)
                if len(images) >= max_count:
                    break
        return images

    # ==================== 参数解析 ====================

    def _parse_image_params(
        self, text: str, strict_size: bool = True
    ) -> tuple[str, dict[str, Any]]:
        """解析生图参数: [数量] [尺寸] 提示词（顺序任意）"""
        params: dict[str, Any] = {
            "n": 1,
            "size": self.DEFAULT_TEXT_IMAGE_SIZE,
            "invalid_size": None,
        }
        parts = text.split()
        if not parts:
            return "", params

        prompt_start = 0
        found_n = False
        found_size = False

        for i in range(min(2, len(parts))):
            p = parts[i]

            if not found_n and p.isdigit() and 1 <= int(p) <= self.MAX_IMAGE_COUNT:
                params["n"] = int(p)
                prompt_start = i + 1
                found_n = True
            elif not found_size:
                normalized = self._api_client._normalize_supported_size(p)
                if normalized:
                    params["size"] = normalized
                    prompt_start = i + 1
                    found_size = True
                    continue

                parsed_size = self._api_client._parse_size_string(p)
                if parsed_size and strict_size:
                    params["invalid_size"] = self._api_client._format_size(
                        parsed_size[0], parsed_size[1]
                    )
                    prompt_start = i + 1
                    found_size = True
                    continue
                break
            else:
                break

        prompt = " ".join(parts[prompt_start:]).strip()
        return prompt, params

    # ==================== 主流程 ====================

    async def run(self, event: Any):
        """Grok 生图: /grok生图 [数量] [尺寸] <提示词> [+图片可选]"""
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

        image_inputs = await self._get_images_from_event(event, max_count=2)
        image_bytes = image_inputs[0] if image_inputs else None
        mask_bytes = image_inputs[1] if len(image_inputs) > 1 else None
        mode = "图生图" if image_bytes else "文生图"

        prompt_text, params = self._parse_image_params(
            user_input, strict_size=not image_bytes
        )
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
        requested_size = params["size"]
        invalid_size = params.get("invalid_size")

        if not image_bytes and invalid_size:
            supported_ratios = "、".join(self.SIZE_TO_ASPECT_RATIO.values())
            yield event.plain_result(
                f"❌ 不支持的尺寸: {invalid_size}\n支持比例: {supported_ratios}"
            )
            return

        target_size = None
        if image_bytes:
            source_resolution = self._media_handler.get_image_resolution(image_bytes)
            if source_resolution:
                target_size = self._api_client._get_closest_supported_size(
                    *source_resolution
                )
        else:
            target_size = requested_size

        if not target_size:
            target_size = self.DEFAULT_TEXT_IMAGE_SIZE

        aspect_ratio_display = self._api_client._get_aspect_ratio_display(target_size)
        yield event.plain_result(
            f"🎨 正在进行 [{mode}] · {n}张 · {aspect_ratio_display} ..."
        )

        results, error = await self._api_client.generate_image(
            prompt_text,
            image_bytes,
            mask_bytes=mask_bytes,
            n=n,
            target_size=target_size,
        )

        if error:
            yield event.plain_result(
                f"❌ [{mode}] 生成失败: {self._error_translator.translate(error)}"
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

        logger.debug(f"[grok生图] 已发送 {len(images_data)} 张图片，模式={mode}")

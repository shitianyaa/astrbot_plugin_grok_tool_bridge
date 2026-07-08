"""Grok 生图 API 客户端

移植自 astrbot_plugin_grok_suite (作者: 沐沐沐倾)，裁剪为文生图 / 图生图。
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from typing import Any

import aiohttp
from PIL import Image

from astrbot.api import logger

from .errors import ErrorTranslator
from .parser import ResponseParser


class GrokImageClient:
    """Grok 生图 HTTP 客户端（文生图 / 图生图）"""

    # 常量
    DEFAULT_TEXT_IMAGE_SIZE = "720x1280"  # 9:16 竖屏
    SUPPORTED_IMAGE_SIZES = (
        "1024x1024",
        "1024x1792",
        "1280x720",
        "1792x1024",
        "720x1280",
    )
    SIZE_TO_ASPECT_RATIO = {
        "1280x720": "16:9",
        "720x1280": "9:16",
        "1792x1024": "3:2",
        "1024x1792": "2:3",
        "1024x1024": "1:1",
    }
    ASPECT_RATIO_TO_SIZE = {
        "16:9": "1280x720",
        "9:16": "720x1280",
        "3:2": "1792x1024",
        "2:3": "1024x1792",
        "1:1": "1024x1024",
    }

    MAX_IMAGE_COUNT = 10
    MAX_PROMPT_LENGTH = 4000
    MAX_REQUEST_RETRIES = 3
    RETRYABLE_HTTP_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
    MODEL_CACHE_TTL_SECONDS = 300
    MODEL_PROBE_TIMEOUT = 15
    IMAGE_RESPONSE_FORMAT_CANDIDATES = ("url", "b64_json", None)
    IMAGE_TIMEOUT = 120

    def __init__(self, config: Any):
        """初始化生图客户端

        Args:
            config: 插件配置对象
        """
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        self._models_cache: dict[str, Any] = {"expires_at": 0.0, "models": set()}
        self._models_cache_lock = asyncio.Lock()
        self._error_translator = ErrorTranslator()
        self._parser = ResponseParser()

    # ==================== Session 管理 ====================

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 session 有效（线程安全）"""
        async with self._session_lock:
            if not self._session or self._session.closed:
                self._session = aiohttp.ClientSession()
            return self._session

    async def close(self) -> None:
        """关闭 session"""
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None

    # ==================== 配置读取 ====================

    def _get_base_url(self) -> str:
        """获取 API 基础 URL，自动处理常见的 URL 格式问题"""
        url = str(self._config.get("grok_api_url", "https://api.x.ai")).rstrip("/")
        suffixes = [
            "/v1/chat/completions",
            "/v1/images/generations",
            "/v1/images/edits",
            "/v1/video/generations",
            "/chat/completions",
            "/images/generations",
            "/images/edits",
            "/video/generations",
            "/v1",
        ]
        for suffix in suffixes:
            if url.endswith(suffix):
                url = url[: -len(suffix)]
        return url.rstrip("/")

    def _get_headers(self) -> dict:
        api_key = str(self._config.get("grok_api_key", "")).strip()
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    # ==================== 模型探测 ====================

    async def _fetch_available_models(self) -> set | None:
        """探测当前可用模型列表，带短时缓存"""
        now = time.time()
        async with self._models_cache_lock:
            cached_models = set(self._models_cache.get("models", set()))
            expires_at = float(self._models_cache.get("expires_at", 0.0))
            if cached_models and now < expires_at:
                return cached_models

        base_url = self._get_base_url()
        api_url = f"{base_url}/v1/models"
        try:
            session = await self._ensure_session()
            api_key = str(self._config.get("grok_api_key", "")).strip()
            async with session.get(
                api_url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=self.MODEL_PROBE_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return None
                raw_text = await resp.text()
            data = json.loads(raw_text)
            model_ids: set = set()
            for item in data.get("data", []) if isinstance(data, dict) else []:
                if isinstance(item, dict):
                    model_id = str(item.get("id", "")).strip()
                    if model_id:
                        model_ids.add(model_id)
            if not model_ids:
                return None
            async with self._models_cache_lock:
                self._models_cache["models"] = model_ids
                self._models_cache["expires_at"] = (
                    time.time() + self.MODEL_CACHE_TTL_SECONDS
                )
            return set(model_ids)
        except Exception:
            return None

    async def _resolve_model(
        self,
        configured_model: str,
        fallback_models: list[str],
        scene: str,
    ) -> str:
        """根据 /v1/models 自动选择可用模型，不可用时按候选回退"""
        preferred_model = str(configured_model or "").strip()
        if not preferred_model and fallback_models:
            preferred_model = fallback_models[0]

        candidates: list[str] = []
        for model_name in [preferred_model, *fallback_models]:
            model_name = str(model_name or "").strip()
            if model_name and model_name not in candidates:
                candidates.append(model_name)

        if not candidates:
            return preferred_model

        available_models = await self._fetch_available_models()
        if not available_models:
            return candidates[0]

        for candidate in candidates:
            if candidate in available_models:
                if candidate != candidates[0]:
                    logger.warning(
                        f"[{scene}] 配置模型不可用，自动回退为可用模型: {candidate}"
                    )
                return candidate

        logger.warning(f"[{scene}] 未命中可用候选模型，继续使用: {candidates[0]}")
        return candidates[0]

    # ==================== 尺寸处理 ====================

    @staticmethod
    def _parse_size_string(size: str) -> tuple[int, int] | None:
        """解析 WxH 字符串"""
        if not size or "x" not in size.lower():
            return None
        try:
            width_str, height_str = size.lower().split("x", 1)
            width = int(width_str.strip())
            height = int(height_str.strip())
            if width <= 0 or height <= 0:
                return None
            return width, height
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _format_size(width: int, height: int) -> str:
        """格式化尺寸字符串"""
        return f"{width}x{height}"

    def _normalize_supported_size(self, size: str) -> str | None:
        """归一化并校验是否为受支持尺寸"""
        if ":" in size:
            parts = size.split(":")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                if size in self.ASPECT_RATIO_TO_SIZE:
                    return self.ASPECT_RATIO_TO_SIZE[size]
                return None

        parsed = self._parse_size_string(size)
        if not parsed:
            return None
        normalized = self._format_size(parsed[0], parsed[1])
        if normalized in self.SUPPORTED_IMAGE_SIZES:
            return normalized
        return None

    def _get_closest_supported_size(self, width: int, height: int) -> str | None:
        """按分辨率距离匹配最接近的合法尺寸"""
        if width <= 0 or height <= 0:
            return None

        candidates: list[tuple[str, int, int]] = []
        for size_str in self.SUPPORTED_IMAGE_SIZES:
            parsed = self._parse_size_string(size_str)
            if parsed:
                candidates.append((size_str, parsed[0], parsed[1]))

        if not candidates:
            return None

        target_ratio = width / height
        target_area = width * height

        def distance(item: tuple[str, int, int]) -> tuple[float, float, float]:
            _, cand_w, cand_h = item
            dim_distance = abs(cand_w - width) / max(width, 1) + abs(
                cand_h - height
            ) / max(height, 1)
            ratio_distance = abs((cand_w / cand_h) - target_ratio)
            area_distance = abs((cand_w * cand_h) - target_area) / max(target_area, 1)
            return ratio_distance, area_distance, dim_distance

        best = min(candidates, key=distance)
        return best[0]

    @classmethod
    def _get_aspect_ratio_display(cls, size: str) -> str:
        """获取尺寸的比例显示"""
        if size in cls.SIZE_TO_ASPECT_RATIO:
            return cls.SIZE_TO_ASPECT_RATIO[size]
        return size

    # ==================== 重试逻辑 ====================

    @classmethod
    def _is_retryable_status(cls, status_code: int) -> bool:
        """判断状态码是否适合自动重试"""
        return status_code in cls.RETRYABLE_HTTP_STATUS_CODES

    @staticmethod
    def _retry_delay_seconds(attempt_index: int) -> float:
        """退避重试等待时长"""
        return min(1.5 * (2**attempt_index), 4.0)

    # ==================== 图片生成 ====================

    @staticmethod
    def _detect_mime_type(data: bytes) -> str:
        """检测图片 MIME 类型"""
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if data.startswith(b"RIFF") and len(data) > 12 and data[8:12] == b"WEBP":
            return "image/webp"
        if data.startswith(b"BM"):
            return "image/bmp"
        return "image/png"

    def _build_edit_image_form(
        self,
        model: str,
        prompt: str,
        n: int,
        image_bytes: bytes,
        size: str | None = None,
        response_format: str | None = "url",
        mask_bytes: bytes | None = None,
    ) -> aiohttp.FormData:
        """构建图生图请求体"""
        form = aiohttp.FormData()
        form.add_field("model", model)
        form.add_field("prompt", prompt)
        form.add_field("n", str(max(1, min(n, self.MAX_IMAGE_COUNT))))
        if response_format:
            form.add_field("response_format", response_format)
        if size:
            form.add_field("size", size)

        mime_type = self._detect_mime_type(image_bytes)
        ext = mime_type.split("/")[-1]
        if ext == "jpeg":
            ext = "jpg"
        form.add_field(
            "image",
            image_bytes,
            filename=f"image.{ext}",
            content_type=mime_type,
        )
        if mask_bytes:
            mask_mime_type = self._detect_mime_type(mask_bytes)
            mask_ext = mask_mime_type.split("/")[-1]
            if mask_ext == "jpeg":
                mask_ext = "jpg"
            form.add_field(
                "mask",
                mask_bytes,
                filename=f"mask.{mask_ext}",
                content_type=mask_mime_type,
            )
        return form

    async def generate_image(
        self,
        prompt: str,
        image_bytes: bytes | None = None,
        mask_bytes: bytes | None = None,
        n: int = 1,
        target_size: str | None = None,
    ) -> tuple[list[tuple[str | None, bytes | None]], str | None]:
        """调用 Grok 生图 API

        文生图: POST /v1/images/generations (JSON)
        图生图: POST /v1/images/edits (multipart/form-data)
        """
        if image_bytes:
            return await self._edit_image(
                prompt, image_bytes, n, target_size=target_size, mask_bytes=mask_bytes
            )

        base_url = self._get_base_url()
        api_url = f"{base_url}/v1/images/generations"
        configured_model = self._config.get(
            "grok_image_model", "grok-imagine-image-lite"
        )
        model = await self._resolve_model(
            configured_model=configured_model,
            fallback_models=["grok-imagine-image-lite"],
            scene="文生图",
        )

        resolved_size = target_size or self.DEFAULT_TEXT_IMAGE_SIZE
        last_error: str | None = None

        for response_format in self.IMAGE_RESPONSE_FORMAT_CANDIDATES:
            payload = {
                "model": model,
                "prompt": prompt,
                "n": max(1, min(n, self.MAX_IMAGE_COUNT)),
            }
            if response_format:
                payload["response_format"] = response_format
            if resolved_size:
                payload["size"] = resolved_size
                logger.info(f"[文生图] 发送尺寸参数: {resolved_size}")

            logger.info(f"[文生图] 完整请求参数: {payload}")
            for attempt in range(self.MAX_REQUEST_RETRIES):
                try:
                    session = await self._ensure_session()
                    async with session.post(
                        api_url,
                        headers=self._get_headers(),
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=self.IMAGE_TIMEOUT),
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            logger.error(
                                f"[文生图] API 请求失败 (状态码: {resp.status}): {text[:200]}"
                            )
                            detail = self._parser.extract_api_error_message(text)
                            translated_error = self._error_translator.translate(
                                detail or f"状态码: {resp.status}"
                            )
                            last_error = translated_error

                            if (
                                response_format
                                and self._error_translator.is_response_format_related_error(
                                    detail
                                )
                            ):
                                logger.warning(
                                    f"[文生图] 返回格式不兼容，自动切换模式重试: {detail[:120]}"
                                )
                                break

                            if (
                                self._is_retryable_status(resp.status)
                                and attempt < self.MAX_REQUEST_RETRIES - 1
                            ):
                                await asyncio.sleep(self._retry_delay_seconds(attempt))
                                continue
                            return [], translated_error

                        raw_content = await resp.read()
                        try:
                            data = json.loads(raw_content.decode("utf-8"))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            logger.error(
                                f"JSON解析失败，响应前200字节: {raw_content[:200]}"
                            )
                            return [], "API响应格式异常"

                        results = self._parser.parse_image_api_response(data)
                        if results:
                            return results, None
                        return [], "未能从响应中提取图片"

                except (asyncio.TimeoutError, aiohttp.ClientError):
                    if attempt < self.MAX_REQUEST_RETRIES - 1:
                        await asyncio.sleep(self._retry_delay_seconds(attempt))
                        continue
                    last_error = "请求超时，请重试"
                except Exception as e:
                    if attempt < self.MAX_REQUEST_RETRIES - 1:
                        await asyncio.sleep(self._retry_delay_seconds(attempt))
                        continue
                    logger.error(f"[文生图] 请求异常: {e}")
                    last_error = self._error_translator.translate(str(e))

        return [], last_error or "文生图请求失败"

    async def _edit_image(
        self,
        prompt: str,
        image_bytes: bytes,
        n: int = 1,
        target_size: str | None = None,
        mask_bytes: bytes | None = None,
    ) -> tuple[list[tuple[str | None, bytes | None]], str | None]:
        """调用 Grok 图片编辑 API"""
        base_url = self._get_base_url()
        api_url = f"{base_url}/v1/images/edits"
        configured_model = self._config.get("grok_edit_model", "grok-imagine-1.0-edit")
        model = await self._resolve_model(
            configured_model=configured_model,
            fallback_models=["grok-imagine-1.0-edit", "grok-imagine-image-lite"],
            scene="图生图",
        )
        resolved_size = target_size
        if not resolved_size:
            try:
                with Image.open(io.BytesIO(image_bytes)) as img:
                    source_resolution = img.size
            except Exception:
                source_resolution = None
            if source_resolution:
                resolved_size = self._get_closest_supported_size(*source_resolution)
            if not resolved_size:
                resolved_size = self.DEFAULT_TEXT_IMAGE_SIZE

        size_attempts: list[str | None] = [resolved_size] if resolved_size else [None]
        if resolved_size:
            size_attempts.append(None)

        last_error: str | None = None
        for current_size in size_attempts:
            fallback_next_size = False
            for response_format in self.IMAGE_RESPONSE_FORMAT_CANDIDATES:
                format_changed = False
                for attempt in range(self.MAX_REQUEST_RETRIES):
                    form = self._build_edit_image_form(
                        model=model,
                        prompt=prompt,
                        n=n,
                        image_bytes=image_bytes,
                        size=current_size,
                        response_format=response_format,
                        mask_bytes=mask_bytes,
                    )
                    try:
                        session = await self._ensure_session()
                        headers = {
                            "Authorization": f"Bearer {self._config.get('grok_api_key', '')}"
                        }
                        async with session.post(
                            api_url,
                            headers=headers,
                            data=form,
                            timeout=aiohttp.ClientTimeout(total=self.IMAGE_TIMEOUT),
                        ) as resp:
                            if resp.status != 200:
                                text = await resp.text()
                                logger.error(
                                    f"[图生图] API 请求失败 (状态码: {resp.status}): {text[:200]}"
                                )
                                detail = self._parser.extract_api_error_message(text)
                                translated_error = self._error_translator.translate(
                                    detail or f"状态码: {resp.status}"
                                )
                                last_error = translated_error

                                if (
                                    current_size
                                    and self._error_translator.is_size_related_error(
                                        detail
                                    )
                                ):
                                    logger.warning(
                                        f"[图生图] size={current_size} 失败，尝试降级为后端默认尺寸: {detail[:120]}"
                                    )
                                    fallback_next_size = True
                                    break

                                if (
                                    response_format
                                    and self._error_translator.is_response_format_related_error(
                                        detail
                                    )
                                ):
                                    logger.warning(
                                        f"[图生图] 返回格式不兼容，自动切换模式重试: {detail[:120]}"
                                    )
                                    format_changed = True
                                    break

                                if (
                                    self._is_retryable_status(resp.status)
                                    and attempt < self.MAX_REQUEST_RETRIES - 1
                                ):
                                    await asyncio.sleep(
                                        self._retry_delay_seconds(attempt)
                                    )
                                    continue
                                return [], translated_error

                            raw_content = await resp.read()
                            try:
                                data = json.loads(raw_content.decode("utf-8"))
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                logger.error(
                                    f"JSON解析失败，响应前200字节: {raw_content[:200]}"
                                )
                                return [], "API响应格式异常"

                            results = self._parser.parse_image_api_response(data)
                            if results:
                                return results, None
                            return [], "未能从响应中提取图片"

                    except (asyncio.TimeoutError, aiohttp.ClientError):
                        if attempt < self.MAX_REQUEST_RETRIES - 1:
                            await asyncio.sleep(self._retry_delay_seconds(attempt))
                            continue
                        last_error = "请求超时，请重试"
                    except Exception as e:
                        if attempt < self.MAX_REQUEST_RETRIES - 1:
                            await asyncio.sleep(self._retry_delay_seconds(attempt))
                            continue
                        logger.error(f"[图生图] 请求异常: {e}")
                        last_error = self._error_translator.translate(str(e))

                if fallback_next_size:
                    break
                if format_changed:
                    continue

            if fallback_next_size:
                continue

        return [], last_error or "图生图请求失败"

"""Grok 生图 API 客户端

移植自 astrbot_plugin_grok_suite (作者: 沐沐沐倾)，裁剪为文生图。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiohttp

from astrbot.api import logger

from .errors import ErrorTranslator
from .parser import ResponseParser


class GrokImageClient:
    """Grok 文生图 HTTP 客户端。"""

    MAX_IMAGE_COUNT = 10
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
            "/v1/video/generations",
            "/chat/completions",
            "/images/generations",
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

    @classmethod
    def _build_generation_payload(
        cls,
        model: str,
        prompt: str,
        n: int,
        response_format: str | None,
    ) -> dict[str, Any]:
        """构建不带尺寸或比例字段的文生图请求体。"""
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": max(1, min(n, cls.MAX_IMAGE_COUNT)),
        }
        if response_format:
            payload["response_format"] = response_format
        return payload

    async def generate_image(
        self,
        prompt: str,
        n: int = 1,
    ) -> tuple[list[tuple[str | None, bytes | None]], str | None]:
        """调用 Grok 文生图 API。"""
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

        last_error: str | None = None

        for response_format in self.IMAGE_RESPONSE_FORMAT_CANDIDATES:
            payload = self._build_generation_payload(
                model=model,
                prompt=prompt,
                n=n,
                response_format=response_format,
            )

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

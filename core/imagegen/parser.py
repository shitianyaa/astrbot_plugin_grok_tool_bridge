"""图片响应解析模块

移植自 astrbot_plugin_grok_suite (作者: 沐沐沐倾)，裁剪为生图所需部分。
"""

from __future__ import annotations

import base64
import json
import re


class ResponseParser:
    """图片生成响应解析器，支持 URL / Base64 / 多种 API 格式"""

    MIN_BASE64_LENGTH = 100

    # ==================== API 错误提取 ====================

    @staticmethod
    def extract_api_error_message(raw_text: str) -> str:
        """从 API 错误响应中提取可读信息"""
        if not raw_text:
            return ""

        text = raw_text.strip()
        if not text:
            return ""

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text[:500]

        if isinstance(data, dict):
            error_obj = data.get("error")
            if isinstance(error_obj, dict):
                message = str(error_obj.get("message", "")).strip()
                code = str(error_obj.get("code", "")).strip()
                param = str(error_obj.get("param", "")).strip()
                parts = []
                if message:
                    parts.append(message)
                if code and code not in message:
                    parts.append(f"code={code}")
                if param and param not in message:
                    parts.append(f"param={param}")
                if parts:
                    return " | ".join(parts)
            elif isinstance(error_obj, str) and error_obj.strip():
                return error_obj.strip()

            for key in ("message", "detail", "error_description"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return text[:500]

    # ==================== 图片响应解析 ====================

    @classmethod
    def parse_image_api_response(
        cls, data: dict
    ) -> list[tuple[str | None, bytes | None]]:
        """解析图片生成 API 响应，返回 [(url, bytes), ...]"""
        results: list[tuple[str | None, bytes | None]] = []
        # 标准 OpenAI 格式: {"data": [{"url": "..."} or {"b64_json": "..."}]}
        if "data" in data and isinstance(data["data"], list):
            for item in data["data"]:
                if isinstance(item, dict):
                    if item.get("url"):
                        results.append((item["url"], None))
                    elif item.get("b64_json"):
                        try:
                            img_bytes = base64.b64decode(item["b64_json"])
                            results.append((None, img_bytes))
                        except Exception:
                            pass

        # 其他格式: 尝试提取 URL 或 Base64
        if not results:
            url, b64, _ = cls.parse_json_response(data)
            if url:
                results.append((url, None))
            elif b64:
                try:
                    img_bytes = base64.b64decode(b64)
                    results.append((None, img_bytes))
                except Exception:
                    pass

        return results

    @classmethod
    def parse_json_response(
        cls, data: dict
    ) -> tuple[str | None, str | None, str | None]:
        """解析 JSON 响应，返回 (url, base64, text)"""
        url = None
        b64 = None
        text = None

        # OpenAI 图像生成格式: {"data": [{"url": "..."} or {"b64_json": "..."}]}
        if "data" in data and isinstance(data["data"], list):
            for item in data["data"]:
                if isinstance(item, dict):
                    if item.get("url"):
                        url = item["url"]
                    if item.get("b64_json"):
                        b64 = item["b64_json"]
                    if item.get("revised_prompt"):
                        text = item["revised_prompt"]

        # Chat Completions 格式
        if "choices" in data:
            for choice in data.get("choices", []):
                msg = choice.get("message") or choice.get("delta") or {}
                content = msg.get("content")
                if content:
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "image_url":
                                    img_url = part.get("image_url", {}).get("url", "")
                                    if img_url.startswith("data:"):
                                        b64 = cls.extract_base64_from_data_uri(img_url)
                                    else:
                                        url = img_url
                                elif part.get("type") == "text":
                                    text = part.get("text", "")

        # 直接字段
        for key in ("url", "image_url", "video_url", "media_url", "file_url"):
            if data.get(key):
                url = data[key]
                break

        for key in ("b64_json", "base64", "image_base64", "data"):
            val = data.get(key)
            if val and isinstance(val, str) and cls.is_base64(val):
                b64 = val
                break

        for key in ("content", "text", "result", "output", "message"):
            val = data.get(key)
            if val and isinstance(val, str):
                text = val
                break

        return url, b64, text

    @staticmethod
    def extract_base64_from_data_uri(data_uri: str) -> str | None:
        """从 data URI 中提取 Base64"""
        if "base64," in data_uri:
            return data_uri.split("base64,", 1)[1]
        return None

    @staticmethod
    def is_base64(s: str) -> bool:
        """检查字符串是否为有效的 Base64"""
        if not s or len(s) < 100:
            return False
        try:
            if re.match(r"^[A-Za-z0-9+/]+={0,2}$", s):
                base64.b64decode(s[:100])
                return True
        except Exception:
            pass
        return False

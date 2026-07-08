"""错误翻译模块 - 将英文错误消息转换为中文

移植自 astrbot_plugin_grok_suite (作者: 沐沐沐倾)。
"""

from __future__ import annotations

from typing import Any


class ErrorTranslator:
    """Grok API 错误消息翻译器"""

    ERROR_TRANSLATIONS = {
        "Session is closed": "会话已关闭，请重试",
        "Connection reset by peer": "连接被重置，请重试",
        "Connection refused": "连接被拒绝，请检查API地址",
        "Timeout": "请求超时，请重试",
        "TimeoutError": "请求超时，请重试",
        "Name or service not known": "无法解析API地址，请检查网络",
        "No route to host": "无法连接到服务器，请检查网络",
        "Network is unreachable": "网络不可达，请检查网络连接",
        "SSL": "SSL证书错误，请检查API地址",
        "Certificate": "证书验证失败",
        "Unauthorized": "API密钥无效或已过期",
        "Forbidden": "访问被拒绝，请检查权限",
        "Not Found": "API接口不存在，请检查配置",
        "Too Many Requests": "请求过于频繁，请稍后重试",
        "Rate limit": "已达到速率限制，请稍后重试",
        "Internal Server Error": "服务器内部错误，请稍后重试",
        "Bad Gateway": "网关错误，请稍后重试",
        "Service Unavailable": "服务暂时不可用，请稍后重试",
        "Gateway Timeout": "网关超时，请稍后重试",
        "Invalid API Key": "API密钥无效",
        "Insufficient quota": "API额度不足",
        "Model not found": "模型不存在，请检查配置",
        "Content policy": "内容违反使用政策",
        "Safety system": "触发安全系统限制",
    }

    @classmethod
    def translate(cls, error: Any) -> str:
        """将英文错误消息翻译为中文"""
        if error is None:
            return "未知错误"

        raw_error = str(error).strip()
        if not raw_error:
            return "未知错误"

        # 已经是中文，直接透传，避免二次翻译后信息丢失
        if any("一" <= c <= "鿿" for c in raw_error):
            return raw_error

        error_lower = raw_error.lower()

        # 检查是否匹配已知错误模式
        for en_pattern, zh_msg in cls.ERROR_TRANSLATIONS.items():
            if en_pattern.lower() in error_lower:
                return zh_msg

        if "invalid_size" in error_lower or "size must be" in error_lower:
            return f"尺寸参数不合法: {raw_error}"

        if "invalid_resolution" in error_lower or "resolution_name" in error_lower:
            return f"视频分辨率参数不合法: {raw_error}"

        # 处理 HTTP 状态码
        if "状态码: 401" in raw_error or "status: 401" in error_lower:
            return "API密钥无效或已过期"
        if "状态码: 403" in raw_error or "status: 403" in error_lower:
            return "访问被拒绝"
        if "状态码: 404" in raw_error or "status: 404" in error_lower:
            return "API接口不存在"
        if "状态码: 429" in raw_error or "status: 429" in error_lower:
            return "请求过于频繁，请稍后重试"
        if "状态码: 5" in raw_error or "status: 5" in error_lower:
            return "服务器错误，请稍后重试"

        # 处理 Errno 错误
        if "errno" in error_lower:
            if "104" in raw_error:
                return "连接被重置，请重试"
            if "111" in raw_error:
                return "连接被拒绝，请检查API地址"
            if "110" in raw_error:
                return "连接超时，请重试"
            if "113" in raw_error:
                return "无法连接到服务器"

        # 提取末尾更有价值的片段
        if ":" in raw_error:
            parts = raw_error.split(":")
            for part in reversed(parts):
                part = part.strip()
                if part and not part.startswith("[") and len(part) > 3:
                    return part[:200]

        return raw_error[:200]

    @staticmethod
    def is_size_related_error(error_message: str | None) -> bool:
        """判断是否是尺寸参数相关错误"""
        if not error_message:
            return False
        err = error_message.lower()
        if "invalid_size" in err or "size must be" in err:
            return True
        return "size" in err and (
            "invalid" in err
            or "unsupported" in err
            or "unknown" in err
            or "must be" in err
        )

    @staticmethod
    def is_resolution_related_error(error_message: str | None) -> bool:
        """判断是否是视频分辨率参数相关错误"""
        if not error_message:
            return False
        err = error_message.lower()
        if "invalid_resolution" in err:
            return True
        if "resolution_name" in err:
            return True
        return "resolution" in err and (
            "invalid" in err or "unsupported" in err or "must be" in err
        )

    @staticmethod
    def is_response_format_related_error(error_message: str | None) -> bool:
        """判断是否是媒体格式参数相关错误"""
        if not error_message:
            return False
        err = error_message.lower()
        if "response_format" in err:
            return True
        return "format" in err and (
            "invalid" in err or "unsupported" in err or "must be" in err
        )

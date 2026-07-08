"""权限检查模块 - 用户/群组白黑名单

移植自 astrbot_plugin_grok_suite (作者: 沐沐沐倾)。
"""

from __future__ import annotations

from typing import Any


class PermissionChecker:
    """权限检查器 - 支持用户/群组白黑名单"""

    @staticmethod
    def check_event_permissions(event: Any, config: Any) -> tuple[bool, str | None]:
        """检查事件是否有权限执行

        权限规则：
        1. 群组黑名单 > 群组白名单 > 全部允许
        2. 用户黑名单 > 用户白名单 > 全部允许

        Returns:
            (是否有权限, 拒绝原因或None)
        """
        # 获取群组 ID（兼容不同平台）
        group_id = None
        if hasattr(event, "get_group_id"):
            try:
                group_id = event.get_group_id()
            except Exception:
                pass

        group_blacklist = config.get("group_blacklist", [])
        if group_blacklist and group_id and group_id in group_blacklist:
            return False, f"群组 {group_id} 在黑名单中"

        group_whitelist = config.get("group_whitelist", [])
        if group_whitelist and (not group_id or group_id not in group_whitelist):
            return False, f"群组 {group_id} 不在白名单中"

        # 用户权限检查
        sender_id = None
        try:
            sender_id = event.get_sender_id()
        except Exception:
            pass

        user_blacklist = config.get("user_blacklist", [])
        if sender_id and sender_id in user_blacklist:
            return False, f"用户 {sender_id} 在黑名单中"

        user_whitelist = config.get("user_whitelist", [])
        if user_whitelist and (not sender_id or sender_id not in user_whitelist):
            return False, f"用户 {sender_id} 不在白名单中"

        return True, None

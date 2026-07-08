from __future__ import annotations

import re
from dataclasses import dataclass


REALTIME_HINT_RE = re.compile(
    r"(天气|气温|降雨|下雨|台风|预报|新闻|热搜|股价|汇率|比分|赛事|路况|航班|实时|今天|明天)",
    re.IGNORECASE,
)
TOOL_HINT_RE = re.compile(
    r"(文件|附件|readme|日志|log|grep|搜索文件|搜一下|查一下知识库|知识库|kb|群规|配置|代码|项目里|preferred_path=)",
    re.IGNORECASE,
)
DELIVERY_ONLY_HINT_RE = re.compile(
    r"(提醒|记得|通知|叫我|喝水|起床|吃药|打卡|休息)",
    re.IGNORECASE,
)
GENERATION_HINT_RE = re.compile(
    r"(问好|讲|说|写|生成|推荐|总结|分析|创作|语气|方式|可爱|自然|鼓励)",
    re.IGNORECASE,
)

VALID_MODES = {
    "auto",
    "grok_first",
    "tool_first",
    "hybrid",
    "delivery_only",
}


@dataclass(frozen=True)
class ProactivePlan:
    mode: str
    reason: str

    @property
    def needs_tool_prep(self) -> bool:
        return self.mode in {"tool_first", "hybrid"}

    @property
    def needs_content_generation(self) -> bool:
        return self.mode != "delivery_only"

    @property
    def allow_native_search(self) -> bool:
        return self.mode in {"grok_first", "hybrid"}


class ProactivePlanner:
    def plan(self, *, source_text: str, policy: str = "auto") -> ProactivePlan:
        normalized_policy = policy if policy in VALID_MODES else "auto"
        if normalized_policy != "auto":
            return ProactivePlan(
                mode=normalized_policy,
                reason=f"configured proactive_mode_policy={normalized_policy}",
            )

        has_realtime = bool(REALTIME_HINT_RE.search(source_text or ""))
        has_tool = bool(TOOL_HINT_RE.search(source_text or ""))
        if has_realtime and has_tool:
            return ProactivePlan("hybrid", "source needs realtime info and tools")
        if has_tool:
            return ProactivePlan("tool_first", "source needs local/tool material")
        if has_realtime:
            return ProactivePlan("grok_first", "source needs realtime info")
        if self._can_deliver_directly(source_text):
            return ProactivePlan("delivery_only", "source is a simple reminder")
        return ProactivePlan("grok_first", "source needs assistant wording")

    @staticmethod
    def _can_deliver_directly(source_text: str) -> bool:
        text = source_text or ""
        return bool(DELIVERY_ONLY_HINT_RE.search(text)) and not bool(
            GENERATION_HINT_RE.search(text)
        )

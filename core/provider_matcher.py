from __future__ import annotations

from typing import Any


def provider_identity_parts(provider_id: str, provider: Any | None) -> list[str]:
    parts = [provider_id]
    if provider is None:
        return [part for part in parts if part]

    get_model = getattr(provider, "get_model", None)
    if callable(get_model):
        try:
            parts.append(str(get_model() or ""))
        except Exception:
            pass

    provider_config = getattr(provider, "provider_config", None)
    if isinstance(provider_config, dict):
        for key in ("id", "type", "model", "api_base", "name"):
            value = provider_config.get(key)
            if value:
                parts.append(str(value))

    return [part for part in parts if part]


def is_target_provider(
    provider_id: str,
    provider: Any | None,
    keywords: list[str],
) -> bool:
    normalized_keywords = [keyword.lower() for keyword in keywords if keyword]
    if not normalized_keywords:
        return True

    identity = " ".join(provider_identity_parts(provider_id, provider)).lower()
    return any(keyword in identity for keyword in normalized_keywords)

import json
from pathlib import Path

from core.config_manager import (
    DEFAULT_AUTO_TOOLS,
    DEFAULT_MANUAL_TOOLS,
    DEFAULT_PROACTIVE_TOOLS,
    DEFAULT_RECENT_FILE_ALLOWED_EXTENSIONS,
    ConfigManager,
)


def test_config_defaults():
    config = ConfigManager({}).config

    assert config.enabled is True
    assert config.auto_mode is True
    assert config.proactive_mode is True
    assert config.enabled_auto_tools == DEFAULT_AUTO_TOOLS
    assert config.enabled_manual_tools == DEFAULT_MANUAL_TOOLS
    assert config.enabled_proactive_tools == DEFAULT_PROACTIVE_TOOLS
    assert (
        config.recent_file_allowed_extensions == DEFAULT_RECENT_FILE_ALLOWED_EXTENSIONS
    )
    assert config.recent_file_ttl_seconds == 1800
    assert config.recent_file_max_size_kb == 2048
    assert config.scheduled_file_retention_days == 7
    assert config.auto_process_uploaded_text_file is False
    assert config.diagnostic_command_enabled is True
    assert config.proactive_mode_policy == "auto"
    assert config.native_tool_passthrough_mode == "off"
    assert config.confidence_threshold == 0.65


def test_config_clamps_numbers_and_parses_lists():
    config = ConfigManager(
        {
            "confidence_threshold": 2,
            "max_steps": 99,
            "tool_call_timeout": 1,
            "target_provider_keywords": "grok,xai",
            "enabled_proactive_tools": "send_message_to_user,future_task",
            "recent_file_ttl_seconds": 999999,
            "recent_file_max_size_kb": 1,
            "scheduled_file_retention_days": 999,
            "recent_file_allowed_extensions": "txt,MD,.log",
            "proactive_mode_policy": "hybrid",
            "native_tool_passthrough_mode": "log_only",
        }
    ).config

    assert config.confidence_threshold == 1.0
    assert config.max_steps == 10
    assert config.tool_call_timeout == 5
    assert config.target_provider_keywords == ["grok", "xai"]
    assert config.enabled_proactive_tools == ["send_message_to_user", "future_task"]
    assert config.recent_file_ttl_seconds == 86400
    assert config.recent_file_max_size_kb == 64
    assert config.scheduled_file_retention_days == 30
    assert config.recent_file_allowed_extensions == [".txt", ".md", ".log"]
    assert config.proactive_mode_policy == "hybrid"
    assert config.native_tool_passthrough_mode == "log_only"


def test_config_falls_back_for_unknown_choices():
    config = ConfigManager(
        {
            "proactive_mode_policy": "unsafe",
            "native_tool_passthrough_mode": "enabled",
        }
    ).config

    assert config.proactive_mode_policy == "auto"
    assert config.native_tool_passthrough_mode == "off"


def test_conf_schema_stays_in_sync_with_runtime_defaults_and_ranges():
    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    defaults = ConfigManager({}).config

    fields = {
        "enabled": defaults.enabled,
        "auto_mode": defaults.auto_mode,
        "proactive_mode": defaults.proactive_mode,
        "manual_command_enabled": defaults.manual_command_enabled,
        "target_provider_keywords": defaults.target_provider_keywords,
        "router_provider_id": defaults.router_provider_id,
        "final_provider_id": defaults.final_provider_id,
        "proactive_agent_provider_id": defaults.proactive_agent_provider_id,
        "proactive_mode_policy": defaults.proactive_mode_policy,
        "native_tool_passthrough_mode": defaults.native_tool_passthrough_mode,
        "confidence_threshold": defaults.confidence_threshold,
        "max_steps": defaults.max_steps,
        "tool_call_timeout": defaults.tool_call_timeout,
        "enabled_auto_tools": defaults.enabled_auto_tools,
        "enabled_manual_tools": defaults.enabled_manual_tools,
        "enabled_proactive_tools": defaults.enabled_proactive_tools,
        "recent_file_bridge_enabled": defaults.recent_file_bridge_enabled,
        "recent_file_ttl_seconds": defaults.recent_file_ttl_seconds,
        "recent_file_max_size_kb": defaults.recent_file_max_size_kb,
        "scheduled_file_retention_days": defaults.scheduled_file_retention_days,
        "recent_file_allowed_extensions": defaults.recent_file_allowed_extensions,
        "auto_process_uploaded_text_file": defaults.auto_process_uploaded_text_file,
        "diagnostic_command_enabled": defaults.diagnostic_command_enabled,
        "send_progress_message": defaults.send_progress_message,
        "debug_mode": defaults.debug_mode,
    }

    for key, value in fields.items():
        assert schema[key]["default"] == value

    assert schema["proactive_mode_policy"]["options"] == [
        "auto",
        "grok_first",
        "tool_first",
        "hybrid",
        "delivery_only",
    ]
    assert schema["native_tool_passthrough_mode"]["options"] == [
        "off",
        "auto",
        "log_only",
    ]
    assert schema["recent_file_max_size_kb"]["slider"]["max"] == 102400
    assert "grok_edit_model" not in schema

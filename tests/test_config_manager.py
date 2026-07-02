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
    assert config.auto_process_uploaded_text_file is False
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
            "recent_file_allowed_extensions": "txt,MD,.log",
        }
    ).config

    assert config.confidence_threshold == 1.0
    assert config.max_steps == 10
    assert config.tool_call_timeout == 5
    assert config.target_provider_keywords == ["grok", "xai"]
    assert config.enabled_proactive_tools == ["send_message_to_user", "future_task"]
    assert config.recent_file_ttl_seconds == 86400
    assert config.recent_file_max_size_kb == 64
    assert config.recent_file_allowed_extensions == [".txt", ".md", ".log"]

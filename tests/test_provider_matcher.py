from core.provider_matcher import is_target_provider, provider_identity_parts


class FakeProvider:
    provider_config = {
        "id": "primary",
        "type": "xai_chat_completion",
        "model": "grok-4",
        "api_base": "https://api.x.ai/v1",
    }

    def get_model(self):
        return "grok-4"


def test_provider_identity_parts_collects_model_and_config():
    parts = provider_identity_parts("provider-1", FakeProvider())

    assert "provider-1" in parts
    assert "grok-4" in parts
    assert "xai_chat_completion" in parts


def test_is_target_provider_matches_keywords():
    assert is_target_provider("provider-1", FakeProvider(), ["grok"])
    assert is_target_provider("provider-1", FakeProvider(), ["xai"])
    assert not is_target_provider("provider-1", FakeProvider(), ["gemini"])
    assert is_target_provider("provider-1", FakeProvider(), [])

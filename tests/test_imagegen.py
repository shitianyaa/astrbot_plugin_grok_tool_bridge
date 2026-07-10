from __future__ import annotations

from pathlib import Path

from core.imagegen.client import GrokImageClient
from core.imagegen.command import GrokImageCommand
from core.imagegen.parser import ResponseParser


def _client() -> GrokImageClient:
    return GrokImageClient({})


def _command(tmp_path: Path) -> GrokImageCommand:
    return GrokImageCommand({}, tmp_path)


def test_parse_image_params_prompt_only_preserves_all_text(tmp_path: Path):
    command = _command(tmp_path)
    prompt, params = command._parse_image_params("一只猫，画面比例 16:9")
    assert prompt == "一只猫，画面比例 16:9"
    assert params == {"n": 1}


def test_parse_image_params_only_consumes_leading_valid_count(tmp_path: Path):
    command = _command(tmp_path)
    prompt, params = command._parse_image_params("4 3:2 日落海滩")
    assert prompt == "3:2 日落海滩"
    assert params == {"n": 4}


def test_parse_image_params_preserves_unusual_ratio(tmp_path: Path):
    command = _command(tmp_path)
    prompt, params = command._parse_image_params("7:5 未来城市")
    assert prompt == "7:5 未来城市"
    assert params == {"n": 1}


def test_parse_image_params_keeps_out_of_range_count_as_prompt(tmp_path: Path):
    command = _command(tmp_path)
    prompt, params = command._parse_image_params("11 只猫")
    assert prompt == "11 只猫"
    assert params == {"n": 1}


class Image:
    pass


class Reply:
    def __init__(self, chain):
        self.chain = chain


class _MessageObject:
    def __init__(self, message):
        self.message = message


class _EventWithSegments:
    def __init__(self, message):
        self.message_obj = _MessageObject(message)


def test_has_image_input_checks_direct_and_replied_images(tmp_path: Path):
    command = _command(tmp_path)
    assert command._has_image_input(_EventWithSegments([Image()])) is True
    assert command._has_image_input(_EventWithSegments([Reply([Image()])])) is True
    assert command._has_image_input(_EventWithSegments([])) is False


def test_generation_payload_omits_size_and_aspect_ratio():
    payload = GrokImageClient._build_generation_payload(
        model="grok-imagine-image-lite",
        prompt="16:9 日落海滩",
        n=12,
        response_format="url",
    )
    assert payload == {
        "model": "grok-imagine-image-lite",
        "prompt": "16:9 日落海滩",
        "n": 10,
        "response_format": "url",
    }
    assert "size" not in payload
    assert "aspect_ratio" not in payload


def test_parser_extracts_image_urls_and_b64():
    parser = ResponseParser()
    results = parser.parse_image_api_response(
        {"data": [{"url": "https://example.com/a.png"}]}
    )
    assert results == [("https://example.com/a.png", None)]


def test_parser_extract_api_error_message():
    parser = ResponseParser()
    detail = parser.extract_api_error_message('{"error": {"message": "bad size"}}')
    assert detail == "bad size"

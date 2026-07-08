from __future__ import annotations

from pathlib import Path

from core.imagegen.client import GrokImageClient
from core.imagegen.command import GrokImageCommand
from core.imagegen.parser import ResponseParser


def _client() -> GrokImageClient:
    return GrokImageClient({})


def _command(tmp_path: Path) -> GrokImageCommand:
    return GrokImageCommand({}, tmp_path)


def test_parse_size_string():
    client = _client()
    assert client._parse_size_string("1280x720") == (1280, 720)
    assert client._parse_size_string("720X1280") == (720, 1280)
    assert client._parse_size_string("bad") is None
    assert client._parse_size_string("0x100") is None


def test_normalize_supported_size_accepts_ratio_and_pixels():
    client = _client()
    assert client._normalize_supported_size("16:9") == "1280x720"
    assert client._normalize_supported_size("9:16") == "720x1280"
    assert client._normalize_supported_size("1024x1024") == "1024x1024"
    assert client._normalize_supported_size("100x100") is None
    assert client._normalize_supported_size("4:3") is None


def test_get_closest_supported_size_matches_by_ratio():
    client = _client()
    # 宽图应匹配到横向尺寸
    assert client._get_closest_supported_size(1920, 1080) == "1280x720"
    # 竖图应匹配到竖向尺寸
    assert client._get_closest_supported_size(1080, 1920) == "720x1280"
    # 方图
    assert client._get_closest_supported_size(1000, 1000) == "1024x1024"
    assert client._get_closest_supported_size(0, 100) is None


def test_get_aspect_ratio_display():
    client = _client()
    assert client._get_aspect_ratio_display("720x1280") == "9:16"
    assert client._get_aspect_ratio_display("1280x720") == "16:9"
    # 未知尺寸原样返回
    assert client._get_aspect_ratio_display("640x480") == "640x480"


def test_parse_image_params_count_size_prompt(tmp_path: Path):
    command = _command(tmp_path)
    prompt, params = command._parse_image_params("4 3:2 日落海滩")
    assert prompt == "日落海滩"
    assert params["n"] == 4
    assert params["size"] == "1792x1024"
    assert params["invalid_size"] is None


def test_parse_image_params_prompt_only_uses_defaults(tmp_path: Path):
    command = _command(tmp_path)
    prompt, params = command._parse_image_params("一只可爱的猫咪")
    assert prompt == "一只可爱的猫咪"
    assert params["n"] == 1
    assert params["size"] == command.DEFAULT_TEXT_IMAGE_SIZE


def test_parse_image_params_flags_invalid_size_when_strict(tmp_path: Path):
    command = _command(tmp_path)
    prompt, params = command._parse_image_params("100x100 猫", strict_size=True)
    assert prompt == "猫"
    assert params["invalid_size"] == "100x100"


def test_parse_image_params_ignores_invalid_size_when_not_strict(tmp_path: Path):
    command = _command(tmp_path)
    # 图生图场景 strict_size=False，不合法尺寸不拦截，作为提示词一部分
    prompt, params = command._parse_image_params("100x100 猫", strict_size=False)
    assert params["invalid_size"] is None
    assert "100x100" in prompt


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

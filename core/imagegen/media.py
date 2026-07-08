"""媒体处理模块 - 下载、保存、发送图片/视频

移植自 astrbot_plugin_grok_suite (作者: 沐沐沐倾)。
"""

from __future__ import annotations

import asyncio
import base64
import io
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any

import aiofiles
import aiohttp
from PIL import Image

from astrbot.api import logger
import astrbot.api.message_components as Comp


class MediaHandler:
    """媒体处理器 - 负责图片/视频的下载、保存和发送"""

    IMAGE_TIMEOUT = 120
    VIDEO_TIMEOUT = 300
    MAX_STREAM_LINES = 100000  # 增大限制，支持更长视频流

    def __init__(self, plugin_data_dir: Path):
        """初始化媒体处理器

        Args:
            plugin_data_dir: 插件数据目录
        """
        self.plugin_data_dir = plugin_data_dir
        self.temp_dir = plugin_data_dir / "temp"
        self.image_dir = plugin_data_dir / "images"
        self.video_dir = plugin_data_dir / "videos"
        self.temp_dir.mkdir(exist_ok=True, parents=True)
        self.image_dir.mkdir(exist_ok=True, parents=True)
        self.video_dir.mkdir(exist_ok=True, parents=True)

    # ==================== 工具方法 ====================

    @staticmethod
    def detect_mime_type(data: bytes) -> str:
        """检测图片 MIME 类型"""
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if data.startswith(b"RIFF") and len(data) > 12 and data[8:12] == b"WEBP":
            return "image/webp"
        if data.startswith(b"BM"):
            return "image/bmp"
        return "image/png"

    @staticmethod
    def guess_filename_from_source(source: str | None, fallback: str) -> str:
        """从来源 URL/路径猜测文件名"""
        if not source:
            return fallback
        try:
            from urllib.parse import urlparse, unquote

            if source.startswith("http"):
                parsed = urlparse(source)
                candidate = unquote(Path(parsed.path).name)
            else:
                candidate = Path(source).name
            if candidate:
                return candidate
        except Exception:
            pass
        return fallback

    @staticmethod
    def guess_mime_type_from_source(source: str | None, default: str) -> str:
        """从来源猜测 MIME 类型"""
        if source:
            guess, _ = mimetypes.guess_type(source)
            if guess:
                return guess
        return default

    @staticmethod
    def guess_audio_format_from_source(source: str | None) -> str:
        """从来源猜测音频格式"""
        if not source:
            return "mp3"
        name = source.split("?", 1)[0].lower()
        if name.endswith(".wav"):
            return "wav"
        if name.endswith(".flac"):
            return "flac"
        if name.endswith(".ogg"):
            return "ogg"
        if name.endswith(".m4a"):
            return "m4a"
        if name.endswith(".aac"):
            return "aac"
        if name.endswith(".opus"):
            return "opus"
        if name.endswith(".mp3"):
            return "mp3"
        return "mp3"

    @staticmethod
    def get_image_resolution(image_bytes: bytes) -> tuple[int, int] | None:
        """读取图片分辨率"""
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                width, height = img.size
            if width <= 0 or height <= 0:
                return None
            return width, height
        except Exception as e:
            logger.warning(f"读取图片分辨率失败: {e}")
            return None

    @staticmethod
    def is_segment_type(seg: Any, type_name: str) -> bool:
        """兼容不同平台实现的消息段类型判断"""
        cls = getattr(Comp, type_name, None)
        if cls is not None:
            try:
                if isinstance(seg, cls):
                    return True
            except Exception:
                pass
        return seg.__class__.__name__.lower() == type_name.lower()

    @staticmethod
    def extract_segment_sources(seg: Any) -> list[str]:
        """从消息段中提取资源 URL/路径"""
        sources: list[str] = []
        for key in ("file", "url", "path", "src"):
            value = getattr(seg, key, None)
            if isinstance(value, str) and value.strip():
                sources.append(value.strip())
        return list(dict.fromkeys(sources))

    # ==================== 下载与加载 ====================

    async def download_media(self, url: str) -> bytes | None:
        """下载媒体文件"""
        try:
            session = aiohttp.ClientSession()
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=self.IMAGE_TIMEOUT)
                ) as resp:
                    resp.raise_for_status()
                    return await resp.read()
            finally:
                await session.close()
        except Exception as e:
            logger.error(f"媒体下载失败: {e}")
            return None

    async def load_bytes(self, src: str) -> bytes | None:
        """从各种来源加载字节数据"""
        path = Path(src)
        if path.is_file():
            try:
                async with aiofiles.open(src, "rb") as f:
                    return await f.read()
            except Exception as e:
                logger.debug(f"读取本地文件失败 ({src[:50]}): {e}")
                return None
        elif src.startswith("http"):
            return await self.download_media(src)
        elif src.startswith("base64://"):
            try:
                return base64.b64decode(src[9:])
            except Exception as e:
                logger.debug(f"Base64解码失败: {e}")
                return None
        return None

    # ==================== 文件清理 ====================

    @staticmethod
    async def safe_remove_file(file_path: Path) -> None:
        """安全删除文件（使用线程避免阻塞）"""
        try:
            if file_path.exists():
                await asyncio.to_thread(os.remove, str(file_path))
        except Exception:
            pass

    # ==================== 媒体保存与发送 ====================

    async def save_and_send_media(
        self,
        event: Any,
        url: str,
        media_bytes: bytes,
        media_type: str = "image",
        save_media: bool = False,
    ):
        """保存并发送媒体文件"""
        if media_type == "video":
            ext = "mp4"
        else:
            mime_type = self.detect_mime_type(media_bytes)
            ext_map = {
                "image/png": "png",
                "image/jpeg": "jpg",
                "image/gif": "gif",
                "image/webp": "webp",
                "image/bmp": "bmp",
            }
            ext = ext_map.get(mime_type, "png")

        filename = f"grok_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"

        if save_media:
            save_dir = self.video_dir if media_type == "video" else self.image_dir
        else:
            save_dir = self.temp_dir

        file_path = (save_dir / filename).resolve()

        try:
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(media_bytes)

            # 兼容新旧 API
            fs_factory = getattr(Comp.Image, "from_file_system", None) or getattr(
                Comp.Image, "fromFileSystem", None
            )
            if media_type == "video":
                video_factory = getattr(
                    Comp.Video, "from_file_system", None
                ) or getattr(Comp.Video, "fromFileSystem", None)
                component = video_factory(path=str(file_path), name=filename)
            else:
                component = fs_factory(path=str(file_path))

            yield event.chain_result([component])

        except Exception as e:
            logger.error(f"媒体处理失败: {e}")
            yield event.plain_result("❌ 发送失败，请到后台查看")
        finally:
            if not save_media:
                await self.safe_remove_file(file_path)

    async def send_images_forward(
        self,
        event: Any,
        images_data: list[tuple[str, bytes]],
        failed_count: int = 0,
        save_media: bool = False,
    ):
        """使用合并转发发送多张图片"""
        saved_files: list[tuple[Path, bool]] = []
        nodes = []
        save_dir = self.image_dir if save_media else self.temp_dir

        try:
            self_id = event.get_self_id()
            try:
                self_id_int = int(self_id)
            except (ValueError, TypeError):
                self_id_int = 10000

            # 兼容新旧 API
            fs_factory = getattr(Comp.Image, "from_file_system", None) or getattr(
                Comp.Image, "fromFileSystem", None
            )

            for i, (_url, img_bytes) in enumerate(images_data):
                mime_type = self.detect_mime_type(img_bytes)
                ext_map = {
                    "image/png": "png",
                    "image/jpeg": "jpg",
                    "image/gif": "gif",
                    "image/webp": "webp",
                    "image/bmp": "bmp",
                }
                ext = ext_map.get(mime_type, "png")
                filename = f"grok_{int(time.time())}_{uuid.uuid4().hex[:8]}_{i}.{ext}"
                file_path = (save_dir / filename).resolve()

                async with aiofiles.open(file_path, "wb") as f:
                    await f.write(img_bytes)
                saved_files.append((file_path, save_media))

                nodes.append(
                    Comp.Node(
                        name="Grok",
                        uin=self_id_int,
                        content=[fs_factory(path=str(file_path))],
                    )
                )

            # 如果有失败的图片，添加提示节点
            if failed_count > 0:
                nodes.append(
                    Comp.Node(
                        name="Grok",
                        uin=self_id_int,
                        content=[
                            Comp.Plain(f"⚠️ {failed_count}张图片下载失败，请到后台查看")
                        ],
                    )
                )

            yield event.chain_result([Comp.Nodes(nodes)])

        except Exception as e:
            logger.error(f"合并转发发送失败: {e}")
            for _i, (url, img_bytes) in enumerate(images_data):
                async for result in self.save_and_send_media(
                    event, url, img_bytes, "image", save_media
                ):
                    yield result
        finally:
            for file_path, should_keep in saved_files:
                if not should_keep:
                    await self.safe_remove_file(file_path)

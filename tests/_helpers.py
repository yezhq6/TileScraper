"""测试辅助：不依赖真实网络的假 HTTP 会话 + 下载器构造工具。"""

import os
import threading
from unittest import mock

from src.downloader import base as base_module
from src.downloader.request import RequestSessionManager
from src.providers import ProviderManager

# 1x1 透明 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)


class _FakeResponse:
    status_code = 200
    headers = {"Content-Type": "image/png"}

    def __init__(self, chunk_delay=0.0):
        self.chunk_delay = chunk_delay

    def iter_content(self, chunk_size=8192):
        for _ in range(3):
            if self.chunk_delay:
                import time
                time.sleep(self.chunk_delay)
            yield PNG

    def close(self):
        pass


class _FakeSession:
    def __init__(self, chunk_delay=0.0):
        self.chunk_delay = chunk_delay

    def get(self, url, **kwargs):
        return _FakeResponse(self.chunk_delay)

    def close(self):
        pass


_counter = 0


def make_provider(name_prefix="test"):
    """注册一个返回假瓦片的自定义 provider，返回其名称。"""
    global _counter
    _counter += 1
    name = f"{name_prefix}_{_counter}_{os.getpid()}"
    ProviderManager.create_custom_provider(
        name, "http://tiles.local/{z}/{x}/{y}.png", min_zoom=0, max_zoom=22
    )
    return name


class FakeNetworkMixin:
    """在测试期间用假会话替换真实网络请求。"""

    chunk_delay = 0.0

    def setUp(self):
        super().setUp()
        delay = self.chunk_delay
        patcher = mock.patch.object(
            RequestSessionManager,
            "get_session",
            lambda mgr: _FakeSession(delay),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


def make_downloader(provider_name, output_dir, **kwargs):
    """构造 TileDownloader（延迟导入，确保上面的 mock 已生效）。"""
    return base_module.TileDownloader(provider_name, output_dir, **kwargs)

#!/usr/bin/env python3
"""对照实验：
  1) TCP_NODELAY 开关对吞吐的影响（判断瓶颈是测试服务器还是 TileScraper）
  2) trust_env=True 时环境代理导致的失败率（跑 3 轮 14981 瓦片）
"""

import http.server
import os
import shutil
import sys
import threading
import time
from pathlib import Path

PROJ = str(Path(__file__).resolve().parents[2])  # 仓库根目录（本脚本位于 tools/stress/）
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="CRITICAL")

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)


def make_handler(nodelay):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = nodelay

        def log_message(self, *a):
            pass

        def do_GET(self):
            time.sleep(self.server.delay)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(PNG)))
                self.end_headers()
                self.wfile.write(PNG)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return H


class S(http.server.ThreadingHTTPServer):
    request_queue_size = 512


def start(nodelay, delay):
    h = S(("127.0.0.1", 0), make_handler(nodelay))
    h.daemon_threads = True
    h.delay = delay
    threading.Thread(target=h.serve_forever, daemon=True).start()
    return h


def patch(trust_env):
    from src.downloader.request import RequestSessionManager

    orig = RequestSessionManager.create_session

    def p(self):
        s = orig(self)
        s.trust_env = trust_env
        return s

    RequestSessionManager.create_session = p


def download(port, out, z0, z1, threads):
    from src.config import config_manager
    from src.providers import ProviderManager
    from src.downloader.base import TileDownloader

    config_manager.config.setdefault("download", {})["task_queue_size"] = 500
    if Path(out).exists():
        shutil.rmtree(out)
    ProviderManager.create_custom_provider("bench", f"http://127.0.0.1:{port}/{{z}}/{{x}}/{{y}}.png")
    d = TileDownloader("bench", str(out), max_threads=threads, enable_resume=False,
                       save_format="directory")
    d.add_tasks_for_bbox(115.0, 39.0, 117.0, 41.0, z0, z1)
    t0 = time.time()
    d.start()
    el = time.time() - t0
    st = d.get_statistics()
    return d.total_tasks, st["failed"], el


if __name__ == "__main__":
    patch(trust_env=False)
    print("== 1) TCP_NODELAY 对照（TileScraper, z=13, 2928 瓦片, 32 线程, 服务端 4ms）==")
    for nodelay in (False, True):
        h = start(nodelay, 0.004)
        port = h.server_address[1]
        total, failed, el = download(port, f"/tmp/ts_bench_nd{int(nodelay)}", 13, 13, 32)
        print(f"   disable_nagle={nodelay!s:5} total={total} failed={failed} "
              f"elapsed={el:.2f}s 吞吐={total/el:.0f}/s 平均每瓦片={el/total*1000:.1f}ms")
        h.shutdown()

    print("\n== 2) trust_env=True（走环境代理）重复 3 轮：14981 瓦片, 32 线程 ==")
    patch(trust_env=True)
    for i in range(3):
        h = start(True, 0.004)
        port = h.server_address[1]
        out = f"/tmp/ts_bench_proxy{i}"
        total, failed, el = download(port, out, 12, 14, 32)
        n = sum(1 for _ in Path(out).rglob("*.png"))
        print(f"   round{i+1}: total={total} 成功文件={n} 失败={failed} "
              f"elapsed={el:.2f}s 吞吐={total/el:.0f}/s")
        h.shutdown()
    os._exit(0)

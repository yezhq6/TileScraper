#!/usr/bin/env python3
"""线程数扫描：不同并发下的吞吐 / CPU / 失败率，用于决定默认值。"""

import http.server
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import psutil

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


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True

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


class S(http.server.ThreadingHTTPServer):
    request_queue_size = 1024
    daemon_threads = True


class CpuSampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.evt = threading.Event()
        self.proc = psutil.Process()
        self.samples = []
        self.proc.cpu_percent(None)

    def run(self):
        while not self.evt.is_set():
            self.samples.append(self.proc.cpu_percent(None))
            self.evt.wait(0.1)

    def stop(self):
        self.evt.set()
        self.join(timeout=2)
        return max(self.samples) if self.samples else 0.0


def patch_proxy():
    from src.downloader.request import RequestSessionManager

    orig = RequestSessionManager.create_session

    def p(self):
        s = orig(self)
        return s

    RequestSessionManager.create_session = p


def run(port, out, threads, z0, z1):
    from src.config import config_manager
    from src.providers import ProviderManager
    from src.downloader.base import TileDownloader

    config_manager.config.setdefault("download", {})["task_queue_size"] = 20000
    if Path(out).exists():
        shutil.rmtree(out)
    ProviderManager.create_custom_provider(
        f"thr{threads}", f"http://127.0.0.1:{port}/{{z}}/{{x}}/{{y}}.png")
    d = TileDownloader(f"thr{threads}", str(out), max_threads=threads,
                       enable_resume=False, save_format="directory")
    d.add_tasks_for_bbox(115.0, 39.0, 117.0, 41.0, z0, z1)
    cpu = CpuSampler()
    cpu.start()
    t0 = time.time()
    d.start()
    el = time.time() - t0
    peak_cpu = cpu.stop()
    st = d.get_statistics()
    return st, el, peak_cpu


if __name__ == "__main__":
    patch_proxy()
    delay = float(os.environ.get("TS_DELAY", "0.005"))
    zoom = int(os.environ.get("TS_Z", "13"))
    z0, z1 = zoom, zoom
    thread_list = [int(x) for x in os.environ.get("TS_THREADS", "1,2,4,8,16,32,64,128,256").split(",")]
    h = S(("127.0.0.1", 0), H)
    h.delay = delay
    port = h.server_address[1]
    threading.Thread(target=h.serve_forever, daemon=True).start()

    print(f"服务端每请求延迟={delay*1000:.0f}ms  z={zoom}  CPU核心={os.cpu_count()}")
    print(f"{'线程':>5} {'耗时(s)':>8} {'吞吐(/s)':>10} {'失败':>5} {'峰值CPU%':>9}")
    for t in thread_list:
        st, el, cpu = run(port, f"/tmp/ts_thr_{t}", t, z0, z1)
        print(f"{t:>5} {el:>8.2f} {st['total']/el:>10.0f} {st['failed']:>5} {cpu:>9.0f}",
              flush=True)
    h.shutdown()
    os._exit(0)

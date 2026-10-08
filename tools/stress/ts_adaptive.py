#!/usr/bin/env python3
"""静态线程 vs 吞吐驱动动态并发：实测对比。

为什么要单独写这个脚本：`ts_threads.py` 把 HTTP 服务器和下载器放在**同一个
Python 进程**里，两者的 CPU 会合并计入，而且会共享同一个 GIL，导致"吞吐在
某个线程数后不再上升"无法区分是客户端瓶颈还是测试服务器瓶颈。

本脚本：

* 服务器跑在**独立子进程**中（可分别测 CPU），并支持一个"容量闸门"
  （semaphore）在运行中途变化，用来模拟 CDN 限流/后端拥塞；
* 实验一：稳定服务器下扫描线程数，得到"线程数 → 吞吐 / 客户端 CPU / 服务端 CPU"
  的曲线，看拐点在哪、被谁限制；
* 实验二：容量中途下降时，对比 static-N 与 AIMD 控制器（在 [min, cap] 内
  自动增减并发）的耗时与 CPU。

运行::

    python -u tools/stress/ts_adaptive.py
"""

import http.server
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

PROJ = str(Path(__file__).resolve().parents[2])
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

# --------------------------------------------------------------------------- #
# 服务器（子进程）
# --------------------------------------------------------------------------- #
_CAP_SEM = None
_CAP_VALUE = 0
_CAP_LOCK = threading.Lock()
_FIRST_REQ = threading.Event()
_INFLIGHT = 0
_MAX_INFLIGHT = 0
_INFLIGHT_LOCK = threading.Lock()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True

    def log_message(self, *args):
        pass

    def do_GET(self):
        global _INFLIGHT, _MAX_INFLIGHT
        _FIRST_REQ.set()
        sem = _CAP_SEM
        if sem is not None:
            sem.acquire()
        with _INFLIGHT_LOCK:
            _INFLIGHT += 1
            _MAX_INFLIGHT = max(_MAX_INFLIGHT, _INFLIGHT)
        try:
            time.sleep(self.server.delay)
            body = PNG
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT -= 1
            if sem is not None:
                sem.release()


class _Server(http.server.ThreadingHTTPServer):
    request_queue_size = 1024
    daemon_threads = True


def run_server(delay: float, schedule: str):
    """
    子进程入口。

    schedule 形如 ``"0:64,6:8"``：从第 0 秒到第 6 秒容量 64，之后容量 8。
    ``"0:0"`` 表示不限制。
    """
    global _CAP_SEM, _CAP_VALUE
    import signal

    points = []
    for part in schedule.split(","):
        at, cap = part.split(":")
        points.append((float(at), int(cap)))
    points.sort()

    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.delay = delay
    port = httpd.server_address[1]
    print(port, flush=True)

    def apply_cap():
        global _CAP_SEM, _CAP_VALUE
        # 以"第一个请求到达"为计时起点，保证客户端与服务端的时间轴对齐
        _FIRST_REQ.wait(timeout=60)
        start = time.time()
        for at, cap in points:
            wait = at - (time.time() - start)
            if wait > 0:
                time.sleep(wait)
            with _CAP_LOCK:
                if cap <= 0:
                    _CAP_SEM = None
                else:
                    _CAP_SEM = threading.Semaphore(cap)
                _CAP_VALUE = cap
            print(f"# cap={cap} at {time.time()-start:.1f}s", flush=True)

    threading.Thread(target=apply_cap, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *a: os._exit(0))
    httpd.serve_forever()


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
def make_tiles(count, z=10):
    return [(i % 1024, (i // 1024) % 1024, z) for i in range(count)]


class CpuSampler(threading.Thread):
    def __init__(self, pid=None):
        super().__init__(daemon=True)
        self.evt = threading.Event()
        self.proc = psutil.Process(pid) if pid else psutil.Process()
        self.peak = 0.0
        self.proc.cpu_percent(None)

    def run(self):
        while not self.evt.is_set():
            value = self.proc.cpu_percent(None)
            self.peak = max(self.peak, value)
            self.evt.wait(0.05)

    def stop(self):
        self.evt.set()
        self.join(timeout=2)
        return self.peak


def run_static(port, cap_threads, tiles, out, start_limit=0, controller=None):
    """跑一次下载；返回 (统计, 耗时, 客户端峰值CPU%)。"""
    from src.config import config_manager
    from src.downloader.base import TileDownloader
    from src.providers import ProviderManager

    config_manager.config.setdefault("download", {})["task_queue_size"] = 20000
    if Path(out).exists():
        shutil.rmtree(out)
    name = f"ad_{int(time.time()*1000)%100000}"
    ProviderManager.create_custom_provider(
        name, f"http://127.0.0.1:{port}/{{z}}/{{x}}/{{y}}.png",
        min_zoom=0, max_zoom=20,
    )
    downloader = TileDownloader(
        name, str(out), max_threads=cap_threads, enable_resume=False,
        save_format="directory",
    )
    if start_limit:
        # 让 limiter 以较低并发起步（上限仍是 cap_threads）
        original = downloader._make_limiter

        def patched(threads):
            limiter = original(threads)
            if limiter is not None:
                limiter.set_limit(start_limit)
            return limiter

        downloader._make_limiter = patched

    downloader.add_tasks(tiles)
    cpu = CpuSampler()
    cpu.start()

    ctl_stop = None
    ctl_thread = None
    if controller is not None:
        ctl_stop = threading.Event()
        ctl_thread = threading.Thread(
            target=controller,
            args=(downloader, time.time(), ctl_stop),
            daemon=True,
        )
        ctl_thread.start()

    t0 = time.time()
    downloader.start()
    elapsed = time.time() - t0
    if ctl_stop is not None:
        ctl_stop.set()
    if ctl_thread is not None:
        ctl_thread.join(timeout=3)
    peak_cpu = cpu.stop()
    return downloader.get_statistics(), elapsed, peak_cpu


# --------------------------------------------------------------------------- #
def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--server":
        run_server(float(sys.argv[2]), sys.argv[3])
        return 0

    only = sys.argv[1] if len(sys.argv) > 1 else ""

    tiles = make_tiles(6000)
    if only != "exp2":
        print("=" * 78)
        print("实验一：稳定服务器下线程数扫描（服务器在独立进程，CPU 可分别测量）")
        print("=" * 78)

        for delay in (0.005, 0.05):
            proc = subprocess.Popen(
                [sys.executable, __file__, "--server", str(delay), "0:0"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            port = int(proc.stdout.readline().strip())
            server_cpu = CpuSampler(proc.pid)
            print(f"\n--- 服务端每请求延迟 {delay*1000:.0f}ms ---")
            print(f"{'线程':>5} {'耗时(s)':>8} {'吞吐/s':>8} {'客户端CPU%':>10} {'服务端CPU%':>10} {'失败':>5}")
            for threads in (1, 2, 4, 8, 16, 32, 64, 128, 256):
                stats, elapsed, cpu = run_static(
                    port, threads, tiles, f"/tmp/ts_ad_{threads}"
                )
                scpu = server_cpu.proc.cpu_percent(None)
                print(
                    f"{threads:>5} {elapsed:>8.2f} {stats['total']/elapsed:>8.0f} "
                    f"{cpu:>10.0f} {scpu:>10.0f} {stats['failed']:>5}",
                    flush=True,
                )
            proc.terminate()
            proc.wait(timeout=10)

    # 实验二：服务器容量中途下降。用足够大的任务量，保证"降容"真的落在任务中间。
    print()
    print("=" * 78)
    print("实验二：服务端容量 2s 时 64 → 8（模拟 CDN 收紧），12000 瓦片 @20ms")
    print("=" * 78)
    print(f"{'策略':>16} {'耗时(s)':>8} {'吞吐/s':>8} {'客户端CPU%':>10} {'请求数':>7} {'失败':>5}")

    tiles2 = make_tiles(12000)
    strategies = [
        ("static-8", 8, 0, None),
        ("static-64", 64, 0, None),
        ("static-128", 128, 0, None),
        ("aria2 ramp", 128, 4, "ramp"),
        ("naive aimd", 128, 4, "aimd"),
    ]
    for index, (label, cap, start, ctl) in enumerate(strategies):
        proc = subprocess.Popen(
            [sys.executable, __file__, "--server", "0.02", "0:64,2:8"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        port = int(proc.stdout.readline().strip())
        controller = {"aimd": aimd_controller, "ramp": ramp_controller}.get(ctl)
        stats, elapsed, cpu = run_static(
            port, cap, tiles2, f"/tmp/ts_ad_s{index}",
            start_limit=start, controller=controller,
        )
        print(
            f"{label:>16} {elapsed:>8.2f} {stats['total']/elapsed:>8.0f} "
            f"{cpu:>10.0f} {stats['downloaded']+stats['failed']:>7} {stats['failed']:>5}",
            flush=True,
        )
        proc.terminate()
        proc.wait(timeout=10)

    print()
    print("注：服务端闸门 = 硬瓶颈时，客户端降并发不会更快（只省 CPU）。")
    print("    容量下降前把并发拉满的静态配置在 makespan 上占优；")
    print("    动态控制的价值在'不把服务端逼到限流 + 省本地 CPU'。")
    return 0


# --------------------------------------------------------------------------- #
# AIMD 控制器（实验用）：以 1s 为窗口测量吞吐，涨得快/降得也快
# --------------------------------------------------------------------------- #
_CTL_STATE = {"limit": 8, "last_total": 0, "last_time": 0.0, "best": 0.0}


def aimd_controller(downloader, started_at, stop_event):
    """朴素吞吐 AIMD（用于对照：说明"只看吞吐"的控制器的振荡问题）。"""
    state = _CTL_STATE
    state.update(limit=4, last_total=0, last_time=time.time(), best=0.0)
    while not stop_event.is_set() and downloader.limiter is None:
        time.sleep(0.05)
    if downloader.limiter is None:
        return
    downloader.limiter.set_limit(state["limit"])
    while not stop_event.wait(1.0):
        now = time.time()
        stats = downloader.get_statistics()
        total = stats["downloaded"] + stats["failed"]
        dt = now - state["last_time"]
        rate = (total - state["last_total"]) / dt if dt > 0 else 0.0
        state["last_total"] = total
        state["last_time"] = now
        current = downloader.limiter.limit
        if rate > state["best"] * 1.05:
            state["best"] = rate
            downloader.limiter.set_limit(current + max(1, current // 4))
        else:
            downloader.limiter.set_limit(max(2, int(current * 0.7)))


def ramp_controller(downloader, started_at, stop_event):
    """
    aria2 风格的前馈映射：``N = A + B*log10(观测带宽 Mbps)``（只向上爬、不探测错误）。

    这里按 20KB/瓦片把"瓦片/秒"折算成 Mbps。它是开环的，不会因为吞吐停滞而
    收缩，因此不会振荡；缺点是服务端闸门远低于此值时仍会把并发开大。
    """
    import math

    while not stop_event.is_set() and downloader.limiter is None:
        time.sleep(0.05)
    if downloader.limiter is None:
        return
    downloader.limiter.set_limit(4)
    last_total, last_t = 0, time.time()
    while not stop_event.wait(1.0):
        now = time.time()
        stats = downloader.get_statistics()
        total = stats["downloaded"] + stats["failed"]
        dt = max(1e-6, now - last_t)
        tiles_per_s = (total - last_total) / dt
        last_total, last_t = total, now
        mbps = tiles_per_s * 20 * 1024 * 8 / 1e6
        target = 5 + 25 * math.log10(max(mbps, 0.1))
        downloader.limiter.set_limit(int(target))


if __name__ == "__main__":
    os._exit(main())

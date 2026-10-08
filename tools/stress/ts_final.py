#!/usr/bin/env python3
"""TileScraper 并行 + 万级批量下载最终测试。

关键点：
  * 真实本地 HTTP 服务器（backlog=512），观测服务端并发度；
  * 可切换 trust_env，量化"环境代理"对下载的影响；
  * 全部场景做正确性校验（文件数 / MBTiles 行数 / 重复请求 / 失败数）。
"""

import http.server
import os
import shutil
import sqlite3
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import psutil

PROJ = str(Path(__file__).resolve().parents[2])  # 仓库根目录（本脚本位于 tools/stress/）
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="ERROR", format="{message}")

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)

OUT = Path("/tmp/ts_final_out")
RESULTS = []


# --------------------------------------------------------------------------- #
class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        srv = self.server
        with srv.lock:
            srv.inflight += 1
            srv.max_inflight = max(srv.max_inflight, srv.inflight)
            srv.requests += 1
            srv.paths[self.path] += 1
        try:
            if srv.delay:
                time.sleep(srv.delay)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(PNG)))
                self.end_headers()
                self.wfile.write(PNG)
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            with srv.lock:
                srv.inflight -= 1


class _Server(http.server.ThreadingHTTPServer):
    request_queue_size = 512


class TileServer:
    def __init__(self, delay=0.0):
        self.httpd = _Server(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.delay = delay
        self.httpd.lock = threading.Lock()
        self._reset()
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def _reset(self):
        h = self.httpd
        h.inflight = 0
        h.max_inflight = 0
        h.requests = 0
        h.paths = Counter()

    def reset(self, delay=None):
        with self.httpd.lock:
            self._reset()
        if delay is not None:
            self.httpd.delay = delay

    def stats(self):
        h = self.httpd
        with h.lock:
            return dict(
                requests=h.requests, max_inflight=h.max_inflight,
                unique=len(h.paths),
                dup=sum(1 for c in h.paths.values() if c > 1),
                max_hits=max(h.paths.values()) if h.paths else 0,
            )

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/{{z}}/{{x}}/{{y}}.png"


class Mem(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.evt = threading.Event()
        self.proc = psutil.Process()
        self.peak = self.proc.memory_info().rss

    def run(self):
        while not self.evt.is_set():
            rss = self.proc.memory_info().rss
            if rss > self.peak:
                self.peak = rss
            self.evt.wait(0.02)

    def stop(self):
        self.evt.set()
        self.join(timeout=2)


# --------------------------------------------------------------------------- #
def set_proxy(value):
    """通过项目自身的配置控制代理（""=直连，"env"=环境变量，其余=代理地址）。"""
    from src.config import config_manager

    config_manager.config.setdefault("download", {})["proxy"] = value


def run(name, url, out, bbox, z0, z1, threads, save_format="directory",
        resume=True, queue_size=None, pause_after=None):
    from src.config import config_manager
    from src.providers import ProviderManager
    from src.downloader.base import TileDownloader

    if queue_size is not None:
        config_manager.config.setdefault("download", {})["task_queue_size"] = queue_size

    out = Path(out)
    pname = f"fin_{name}"
    ProviderManager.create_custom_provider(pname, url, min_zoom=0, max_zoom=22)
    d = TileDownloader(pname, str(out), max_threads=threads, enable_resume=resume,
                       save_format=save_format, scheme="xyz")
    d.add_tasks_for_bbox(bbox["west"], bbox["south"], bbox["east"], bbox["north"], z0, z1)
    total = d.total_tasks

    mem = Mem()
    mem.start()
    t0 = time.time()
    paused = False
    alive = False
    if pause_after:
        th = threading.Thread(target=d.start, daemon=True)
        th.start()
        time.sleep(pause_after)
        d.pause()
        paused = d.is_paused()
        time.sleep(1.0)
        d.resume()
        th.join(timeout=900)
        alive = th.is_alive()
    else:
        d.start()
    el = time.time() - t0
    mem.stop()
    return dict(name=name, total=total, stats=d.get_statistics(), elapsed=el,
                peak_mb=mem.peak / 1048576, paused=paused, alive=alive, out=out,
                save_format=save_format)


def n_files(out):
    return sum(1 for _ in Path(out).rglob("*.png"))


def n_rows(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    finally:
        conn.close()


def check(r, srv, extra_ok=True, extra=""):
    st = r["stats"]
    s = srv.stats()
    if r["save_format"] == "mbtiles":
        persisted = n_rows(r["out"])
        pname = f"MBTiles行数={persisted}"
    else:
        persisted = n_files(r["out"])
        pname = f"落盘文件={persisted}"
    # 数据完整性：抽样校验文件内容与源瓦片一致
    bad = 0
    if r["save_format"] == "directory":
        import itertools
        for p in itertools.islice(Path(r["out"]).rglob("*.png"), 500):
            if p.read_bytes() != PNG:
                bad += 1
    ok = (st["failed"] == 0 and st["remaining"] == 0 and persisted == r["total"]
          and s["dup"] == 0 and bad == 0 and extra_ok)
    RESULTS.append((r["name"], ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {r['name']}")
    print(f"    任务={r['total']} 成功={st['downloaded']} 跳过={st['skipped']} "
          f"失败={st['failed']} 剩余={st['remaining']} | {pname} 内容校验异常={bad}")
    print(f"    耗时={r['elapsed']:.2f}s 吞吐={r['total']/r['elapsed']:.0f}/s "
          f"峰值RSS={r['peak_mb']:.1f}MB | 服务端峰值并发={s['max_inflight']} "
          f"请求={s['requests']} 重复URL={s['dup']}")
    if extra:
        print(f"    {extra}")
    print(flush=True)
    return ok


BBOX_B = dict(west=116.2, south=39.7, east=116.6, north=40.1)   # z13 = 154
BBOX_C = dict(west=115.0, south=39.0, east=117.0, north=41.0)   # z12-14 = 14981


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    set_proxy("")   # 直连本地服务器（排除环境代理干扰）

    print("=" * 78)
    print("A. 并行度与线程加速比（154 瓦片，服务端每请求 50ms）")
    print("=" * 78, flush=True)
    srv = TileServer(delay=0.05)
    try:
        for th in (1, 4, 16, 64):
            srv.reset()
            r = run(f"scale_t{th}", srv.url, OUT / f"scale{th}", BBOX_B, 13, 13, th)
            check(r, srv, extra=f"配置线程={th} 服务端观测峰值并发={srv.stats()['max_inflight']}")
    finally:
        srv.stop()

    print("=" * 78)
    print("B. 万级批量下载（14981 瓦片，z=12..14）")
    print("=" * 78, flush=True)
    srv = TileServer(delay=0.004)
    try:
        srv.reset()
        r = run("bulk_dir_32t", srv.url, OUT / "bulk_dir", BBOX_C, 12, 14, 32,
                queue_size=500)
        check(r, srv, extra="有界任务队列=500（验证背压路径）")

        srv.reset()
        r = run("bulk_dir_32t_nosave", srv.url, OUT / "bulk_dir_nr", BBOX_C, 12, 14, 32,
                resume=False)
        check(r, srv, extra="关闭断点续传（对比进度库写入开销）")

        srv.reset()
        r = run("bulk_mbtiles_32t", srv.url, OUT / "bulk.mbtiles", BBOX_C, 12, 14, 32,
                save_format="mbtiles")
        check(r, srv)

        srv.reset()
        r = run("bulk_resume_rerun", srv.url, OUT / "bulk_dir", BBOX_C, 12, 14, 32,
                queue_size=500)
        check(r, srv, extra_ok=(r["stats"]["downloaded"] == 0 and srv.stats()["requests"] == 0),
              extra=f"服务端请求数={srv.stats()['requests']}（应为 0）")

        srv.reset(delay=0.05)
        r = run("bulk_128t", srv.url, OUT / "bulk_128", BBOX_C, 13, 13, 128)
        check(r, srv, extra="配置线程=128")

        srv.reset(delay=0.01)
        r = run("bulk_pause_resume", srv.url, OUT / "bulk_pr", BBOX_C, 12, 14, 32,
                queue_size=500, pause_after=0.8)
        check(r, srv, extra_ok=(not r["alive"]) and r["paused"],
              extra=f"暂停生效={r['paused']} 线程已退出={not r['alive']}")
    finally:
        srv.stop()

    print("=" * 78)
    print("C. 环境代理影响（同一本地服务器，14981 瓦片，32 线程）")
    print("=" * 78, flush=True)
    set_proxy("env")    # 走系统环境变量代理（复现原失败场景）
    srv = TileServer(delay=0.004)
    try:
        srv.reset()
        r = run("proxy_trust_env_true", srv.url, OUT / "proxy_on", BBOX_C, 12, 14, 32)
        s = srv.stats()
        RESULTS.append(("proxy_trust_env_true", r["stats"]["failed"] == 0))
        print(f"[{'PASS' if r['stats']['failed']==0 else 'FAIL'}] proxy_trust_env_true")
        print(f"    任务={r['total']} 成功={r['stats']['downloaded']} "
              f"失败={r['stats']['failed']} 耗时={r['elapsed']:.2f}s "
              f"吞吐={r['total']/r['elapsed']:.0f}/s")
        print(f"    服务端实际收到请求={s['requests']} 峰值并发={s['max_inflight']}\n")
    finally:
        srv.stop()
        set_proxy("")

    print("=" * 78)
    print("D. Web API 端到端（/api/download + /api/progress）")
    print("=" * 78, flush=True)
    srv = TileServer(delay=0.004)
    try:
        from app import app
        client = app.test_client()
        out = OUT / "api_tiles"
        payload = dict(provider_url=srv.url, subdomains=[], tile_format="png",
                       save_format="directory", output_dir=str(out), threads=32,
                       tms=False, min_zoom=12, max_zoom=14, **BBOX_C)
        total = client.post("/api/download", json=payload).get_json()["total"]
        sse = None
        with client.get("/api/progress", buffered=False) as stream:
            for chunk in stream.response:
                t = chunk.decode("utf-8", "replace")
                if t.startswith("data:"):
                    sse = t.strip()
                    break
        deadline = time.time() + 300
        while time.time() < deadline:
            if not client.get("/api/download-status").get_json().get("is_downloading"):
                break
            time.sleep(0.1)
        files = n_files(out)
        ok = total == 14981 and files == total
        RESULTS.append(("web_api_10k", ok))
        print(f"[{'PASS' if ok else 'FAIL'}] web_api_10k")
        print(f"    API total={total} 落盘文件={files}")
        print(f"    SSE 首事件={sse[:110] if sse else None}\n")
    finally:
        srv.stop()

    print("=" * 78)
    for n, ok in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    fails = [n for n, ok in RESULTS if not ok]
    print(f"\n合计 {len(RESULTS)} 项，失败 {len(fails)} 项 {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

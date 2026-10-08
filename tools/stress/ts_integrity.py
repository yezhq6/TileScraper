#!/usr/bin/env python3
"""TileScraper 数据正确性 / 内容校验 / 限流自适应真实服务器回归。

覆盖本轮修复（单元测试用假网络测不到的部分）：

  1. 原子写入：不残留 .part，且不产生"假的已完成"文件；
  2. 内容校验：HTTP 200 + HTML 错误页不会被当作瓦片落盘；
  3. 产物校验：删掉瓦片文件（目录）/ 删掉 tiles 行（MBTiles）后能重新下载；
  4. 失败状态：failed 记录不会让下次运行永久跳过；
  5. 限流自适应：收到 429 + Retry-After 会重试成功，并降低并发上限；
  6. 信号收尾：SIGTERM 只请求停止，start() 正常返回并保存进度。

运行（仓库根目录，需要能绑定本地端口）::

    python -u tools/stress/ts_integrity.py
"""

import http.server
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

PROJ = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="ERROR", format="{message}")

from src.downloader.base import TileDownloader  # noqa: E402
from src.providers import ProviderManager  # noqa: E402

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)
HTML_ERROR = b"<!DOCTYPE html><html><body>403 Forbidden</body></html>"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# --------------------------------------------------------------------------- #
class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        srv = self.server
        parts = self.path.strip("/").split("/")
        tile = None
        if len(parts) == 3:
            try:
                tile = (int(parts[1]), int(parts[2].split(".")[0]), int(parts[0]))
            except ValueError:
                tile = None

        with srv.lock:
            srv.requests += 1
            request_no = srv.requests
            srv.paths[self.path] += 1
            throttling = request_no <= srv.throttle_first

        if throttling:
            self._send(429, b"", "text/plain", {"Retry-After": "1"})
            return

        if srv.delay:
            time.sleep(srv.delay)

        if tile is not None and tile in srv.error_tiles:
            self._send(200, HTML_ERROR, "image/png")
            return

        self._send(200, PNG, "image/png")


class _QuietServer(http.server.ThreadingHTTPServer):
    """子进程被 SIGKILL 时会留下连接重置，属于预期噪音，不再打印堆栈。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class TileServer:
    def __init__(self, delay=0.0, throttle_first=0, error_tiles=()):
        self.httpd = _QuietServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.delay = delay
        self.httpd.throttle_first = throttle_first
        self.httpd.error_tiles = set(error_tiles)
        self.httpd.requests = 0
        self.httpd.paths = Counter()
        self.httpd.lock = threading.Lock()
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.httpd.server_address[1]

    @property
    def url_template(self):
        return f"http://127.0.0.1:{self.port}/{{z}}/{{x}}/{{y}}.png"

    @property
    def requests(self):
        with self.httpd.lock:
            return self.httpd.requests

    def snapshot_paths(self):
        with self.httpd.lock:
            return Counter(self.httpd.paths)

    def reset_paths(self):
        with self.httpd.lock:
            self.httpd.paths = Counter()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_downloader(name, server, out, **kwargs):
    ProviderManager.create_custom_provider(
        name, server.url_template, min_zoom=0, max_zoom=20
    )
    return TileDownloader(name, str(out), **kwargs)


def list_tiles(out):
    return sorted(
        os.path.join(root, f)
        for root, _, files in os.walk(out)
        for f in files
        if f.endswith(".png")
    )


def list_parts(out):
    return [
        os.path.join(root, f)
        for root, _, files in os.walk(out)
        for f in files
        if f.endswith(".part")
    ]


TILES = [(0, 0, 5), (1, 0, 5), (0, 1, 5), (1, 1, 5)]


# --------------------------------------------------------------------------- #
def scenario_atomic_and_validation():
    server = TileServer(error_tiles={(0, 0, 5)})
    out = Path("/tmp/ts_integrity_dir")
    shutil.rmtree(out, ignore_errors=True)
    try:
        dl = make_downloader(
            "integ_atomic", server, out, max_threads=2, enable_resume=False,
            save_format="directory",
        )
        dl.add_tasks(TILES)
        dl.start()

        check(
            "内容校验：HTML 错误页被判为失败",
            dl.failed_count == 1 and dl.downloaded_count == 3,
            f"downloaded={dl.downloaded_count} failed={dl.failed_count}",
        )
        bad = dl.provider.get_tile_path(0, 0, 5, out)
        check("内容校验：错误页未落盘", not bad.exists(), str(bad))
        check("原子写入：无 .part 残留", list_parts(out) == [])
        check(
            "原子写入：成功瓦片数量正确",
            len(list_tiles(out)) == 3,
            f"files={len(list_tiles(out))}",
        )
    finally:
        server.close()


def scenario_artifact_verification():
    server = TileServer()
    out = Path("/tmp/ts_integrity_resume")
    shutil.rmtree(out, ignore_errors=True)
    try:
        first = make_downloader(
            "integ_resume", server, out, max_threads=4, save_format="directory"
        )
        first.add_tasks(TILES)
        first.start()
        check("产物校验：首轮下载 4 个", first.downloaded_count == 4)

        victims = list_tiles(out)[:2]
        for path in victims:
            os.remove(path)

        second = make_downloader(
            "integ_resume2", server, out, max_threads=4, save_format="directory"
        )
        second.add_tasks(TILES)
        second.start()
        stats = second.get_statistics()
        check(
            "产物校验：删除的文件被重新下载",
            stats["downloaded"] == 2 and stats["redownloaded"] == 2
            and stats["skipped"] == 2,
            f"{stats}",
        )
        check("产物校验：最终文件完整", len(list_tiles(out)) == 4)
    finally:
        server.close()


def scenario_mbtiles_verification():
    server = TileServer()
    out = Path("/tmp/ts_integrity/t.mbtiles")
    shutil.rmtree(out.parent, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        first = make_downloader(
            "integ_mb", server, out, max_threads=4, save_format="mbtiles"
        )
        first.add_tasks(TILES)
        first.start()
        conn = sqlite3.connect(out)
        before = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        conn.execute("DELETE FROM tiles WHERE rowid IN (SELECT rowid FROM tiles LIMIT 2)")
        conn.commit()
        conn.close()
        check("MBTiles 产物校验：首轮 4 行", before == 4, f"rows={before}")

        second = make_downloader(
            "integ_mb2", server, out, max_threads=4, save_format="mbtiles"
        )
        second.add_tasks(TILES)
        second.start()
        conn = sqlite3.connect(out)
        after = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        conn.close()
        check(
            "MBTiles 产物校验：缺失行被补齐",
            after == 4 and second.redownload_count == 2,
            f"rows={after} redownloaded={second.redownload_count}",
        )
        check("MBTiles 收尾：写队列已排空", second.pending_mbtiles_writes() == 0)
    finally:
        server.close()


def scenario_failed_status_retried():
    server = TileServer()
    out = Path("/tmp/ts_integrity_failed")
    shutil.rmtree(out, ignore_errors=True)
    try:
        first = make_downloader(
            "integ_fail", server, out, max_threads=5, enable_resume=True,
            save_format="directory", retries=1,
        )
        first.add_tasks(TILES)
        first.start()
        # 人为写入一条 failed 记录（模拟上次运行失败）
        conn = sqlite3.connect(out / "progress.db")
        conn.execute(
            "INSERT OR REPLACE INTO processed_tiles (x, y, z, status) "
            "VALUES (?, ?, ?, 'failed')",
            (0, 0, 5),
        )
        conn.commit()
        conn.close()
        os.remove(first.provider.get_tile_path(0, 0, 5, out))

        second = make_downloader(
            "integ_fail2", server, out, max_threads=2, enable_resume=True,
            save_format="directory",
        )
        second.add_tasks(TILES)
        second.start()
        check(
            "失败状态：failed 记录会重新下载",
            second.downloaded_count == 1 and second.skipped_count == 3,
            f"downloaded={second.downloaded_count} skipped={second.skipped_count}",
        )
    finally:
        server.close()


def scenario_rate_limit():
    server = TileServer(throttle_first=4)
    out = Path("/tmp/ts_integrity_rate")
    shutil.rmtree(out, ignore_errors=True)
    try:
        tiles = [(x, y, 6) for x in range(4) for y in range(4)]
        dl = make_downloader(
            "integ_rate", server, out, max_threads=4, enable_resume=False,
            save_format="directory",
        )
        dl.add_tasks(tiles)
        dl.start()
        stats = dl.get_statistics()
        check(
            "限流自适应：429 后全部重试成功",
            stats["downloaded"] == len(tiles) and stats["failed"] == 0,
            f"{stats}",
        )
        check(
            "限流自适应：并发上限被下调",
            dl.limiter is not None and dl.limiter.limit < 4
            and dl.limiter.throttle_events >= 1,
            (
                f"limit={dl.limiter.limit if dl.limiter else None} "
                f"events={dl.limiter.throttle_events if dl.limiter else None}"
            ),
        )
    finally:
        server.close()


def scenario_signal_graceful():
    server = TileServer(delay=0.02)
    out = Path("/tmp/ts_integrity_signal")
    shutil.rmtree(out, ignore_errors=True)
    try:
        tiles = [(x, y, 7) for x in range(12) for y in range(12)]
        dl = make_downloader(
            "integ_signal", server, out, max_threads=4, enable_resume=True,
            save_format="directory",
        )
        dl.add_tasks(tiles)
        thread = threading.Thread(target=dl.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGTERM)
        thread.join(timeout=30)

        check("信号收尾：start() 正常返回", not thread.is_alive())
        progress_db = out / "progress.db"
        rows = 0
        if progress_db.exists():
            conn = sqlite3.connect(progress_db)
            rows = conn.execute("SELECT COUNT(*) FROM processed_tiles").fetchone()[0]
            conn.close()
        check(
            "信号收尾：进度已保存",
            progress_db.exists() and rows > 0,
            f"rows={rows} downloaded={dl.downloaded_count}/{dl.total_tasks}",
        )
        check("信号收尾：清理无残留 .part", list_parts(out) == [])
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# 崩溃恢复：下载中途 SIGKILL（无法捕获，等价于断电/被强杀），再重跑
# --------------------------------------------------------------------------- #
CRASH_TILES = 3000


def _tile_list(count, z=8):
    """确定性的 (x, y, z) 列表，z=8 内 256×256 足够放 count 个不重复瓦片。"""
    return [(i % 256, (i // 256) % 256, z) for i in range(count)]


def _run_child(url, out, count, save_format):
    """子进程：正常启动下载，等父进程 SIGKILL（不会走到收尾）。"""
    ProviderManager.create_custom_provider(
        "crash_child", url, min_zoom=0, max_zoom=22
    )
    downloader = TileDownloader(
        "crash_child", out, max_threads=8, enable_resume=True,
        save_format=save_format, scheme="xyz",
    )
    downloader.add_tasks(_tile_list(count))
    downloader.start()


def _wait_and_kill(proc, ready, timeout=60):
    """等 ready() 为真（或超时）后 SIGKILL 子进程，返回 (ok, waited_seconds)。"""
    deadline = time.time() + timeout
    started = time.time()
    while time.time() < deadline:
        if ready():
            time.sleep(0.3)  # 让写入再飞一会儿，制造"进行中"状态
            proc.kill()      # SIGKILL：不可捕获
            proc.wait(timeout=15)
            return True, time.time() - started
        if proc.poll() is not None:
            return False, time.time() - started
        time.sleep(0.05)
    proc.kill()
    proc.wait(timeout=15)
    return False, time.time() - started


def _count_rows(db_path, table="tiles"):
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def scenario_crash_resume_directory():
    """目录模式：强杀后不能有半截瓦片，重跑后必须补齐且不重复请求。"""
    server = TileServer(delay=0.004)
    out = Path("/tmp/ts_integrity_crash_dir")
    shutil.rmtree(out, ignore_errors=True)
    try:
        proc = subprocess.Popen(
            [sys.executable, __file__, "--child", server.url_template,
             str(out), str(CRASH_TILES), "directory"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        ok, waited = _wait_and_kill(proc, lambda: len(list_tiles(out)) >= 300)
        check("目录崩溃：成功在下载中强杀子进程", ok, f" waited≈{waited:.1f}s")
        if not ok:
            return

        files_after_kill = list_tiles(out)
        check(
            "目录崩溃：已落盘文件全部完整（无半截瓦片）",
            files_after_kill and all(os.path.getsize(p) == len(PNG) for p in files_after_kill),
            f"files={len(files_after_kill)}",
        )

        server.reset_paths()
        before = server.requests
        dl = make_downloader(
            "crash_dir_resume", server, out, max_threads=8, enable_resume=True,
            save_format="directory",
        )
        dl.add_tasks(_tile_list(CRASH_TILES))
        dl.start()
        resume_requests = server.requests - before

        final = list_tiles(out)
        check(
            "目录崩溃恢复：最终瓦片数 = 任务数（无丢失）",
            len(final) == CRASH_TILES,
            f"files={len(final)}/{CRASH_TILES}",
        )
        check(
            "目录崩溃恢复：所有文件完整",
            all(os.path.getsize(p) == len(PNG) for p in final),
        )
        paths = server.snapshot_paths()
        check(
            "目录崩溃恢复：同一 URL 不重复请求",
            bool(paths) and max(paths.values()) == 1,
            f"max_requests_per_url={max(paths.values()) if paths else 0}",
        )
        missing = CRASH_TILES - len(files_after_kill)
        # 目录模式：已存在文件由 worker 的 exists() 检查跳过（无 HTTP 请求），
        # 缺失文件恰好请求一次 → 请求数应精确等于缺失数
        check(
            "目录崩溃恢复：只补缺失文件、已存在的绝不重下",
            resume_requests == missing,
            f"requests={resume_requests} missing={missing}",
        )
    finally:
        server.close()


def scenario_crash_resume_mbtiles():
    """MBTiles 模式：强杀会丢写线程缓冲区；重跑必须能自愈到完整行数。"""
    server = TileServer(delay=0.004)
    out = Path("/tmp/ts_integrity_crash_mb/t.mbtiles")
    shutil.rmtree(out.parent, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(
            [sys.executable, __file__, "--child", server.url_template,
             str(out), str(CRASH_TILES), "mbtiles"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        ok, waited = _wait_and_kill(proc, lambda: _count_rows(out) >= 300)
        check("MBTiles 崩溃：成功在下载中强杀子进程", ok, f" waited≈{waited:.1f}s")
        if not ok:
            return

        rows_after_kill = _count_rows(out)
        server.reset_paths()
        before = server.requests
        dl = make_downloader(
            "crash_mb_resume", server, out, max_threads=8, enable_resume=True,
            save_format="mbtiles",
        )
        dl.add_tasks(_tile_list(CRASH_TILES))
        dl.start()
        resume_requests = server.requests - before
        final_rows = _count_rows(out)

        check(
            "MBTiles 崩溃恢复：行数自愈到完整（verify_artifacts 生效）",
            final_rows == CRASH_TILES,
            f"rows={final_rows}/{CRASH_TILES}（崩溃时 {rows_after_kill}）",
        )
        missing = CRASH_TILES - rows_after_kill
        check(
            "MBTiles 崩溃恢复：只补缺失行、已提交的绝不重下",
            resume_requests == missing,
            f"requests={resume_requests} missing={missing}",
        )
        paths = server.snapshot_paths()
        check(
            "MBTiles 崩溃恢复：同一 URL 不重复请求",
            bool(paths) and max(paths.values()) == 1,
            f"max_requests_per_url={max(paths.values()) if paths else 0}",
        )
        check(
            "MBTiles 崩溃恢复：写失败计数为 0",
            dl.mbtiles_write_failures() == 0,
        )
    finally:
        server.close()


def _wait_until(proc, ready, timeout=60):
    """等 ready() 为真（子进程存活期间）；返回是否等到。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ready():
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.05)
    return False


def _spawn_child(server, out, count, save_format):
    return subprocess.Popen(
        [sys.executable, __file__, "--child", server.url_template,
         str(out), str(count), save_format],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def scenario_output_lock():
    """输出目录锁：同一路径第二个下载器必须被拒绝，进程死后自动释放。"""
    from src.exceptions import OutputLockedError

    server = TileServer(delay=0.01)
    out = Path("/tmp/ts_integrity_lock/tiles")
    shutil.rmtree(out.parent, ignore_errors=True)
    try:
        proc = _spawn_child(server, out, 2000, "directory")
        ready = _wait_until(proc, lambda: len(list_tiles(out)) >= 20, timeout=40)
        blocked = False
        probe = None
        try:
            probe = make_downloader(
                "lock_probe", server, out, max_threads=1, enable_resume=False
            )
        except OutputLockedError:
            blocked = True
        finally:
            if probe is not None:
                probe.close()
        check(
            "输出目录锁：子进程持锁时第二个下载器被拒绝",
            ready and blocked,
            f"ready={ready} blocked={blocked}",
        )
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=15)

        released = False
        try:
            after = make_downloader(
                "lock_after", server, out, max_threads=1, enable_resume=False
            )
            after.close()
            released = True
        except OutputLockedError:
            released = False
        check("输出目录锁：进程被强杀后锁自动释放", released)
    finally:
        server.close()


def scenario_ack_no_loss_without_verify():
    """
    ack 的核心价值：即使关掉 verify_artifacts，强杀也不会丢数据。

    因为进度只在写线程 commit 成功后才记录，"进度说成功"必然意味着
    "磁盘上真有"，跳过决策可以放心只信进度库。
    """
    server = TileServer(delay=0.004)
    out = Path("/tmp/ts_integrity_ack/t.mbtiles")
    shutil.rmtree(out.parent, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = _spawn_child(server, out, CRASH_TILES, "mbtiles")
        ok, _ = _wait_and_kill(proc, lambda: _count_rows(out) >= 300)
        check("ack：成功在下载中强杀子进程", ok)
        if not ok:
            return

        downloader = make_downloader(
            "ack_resume", server, out, max_threads=8, save_format="mbtiles"
        )
        downloader.verify_artifacts = False  # 故意关掉产物校验
        downloader.add_tasks(_tile_list(CRASH_TILES))
        downloader.start()

        final_rows = _count_rows(out)
        check(
            "ack：关掉 verify_artifacts 后强杀重跑仍不丢数据",
            final_rows == CRASH_TILES,
            f"rows={final_rows}/{CRASH_TILES}（verify_artifacts=false）",
        )
        check("ack：无写失败", downloader.mbtiles_write_failures() == 0)
    finally:
        server.close()


def scenario_failed_retry():
    """失败清单 + 只补失败：先制造 2 个失败，修好服务端后只重下这 2 个。"""
    bad_tiles = {(0, 0, 8), (1, 0, 8)}
    server = TileServer(error_tiles=bad_tiles)
    out = Path("/tmp/ts_integrity_retry")
    shutil.rmtree(out, ignore_errors=True)
    try:
        tiles = _tile_list(200, z=8)
        first = make_downloader(
            "retry_first", server, out, max_threads=4, save_format="directory"
        )
        first.add_tasks(tiles)
        first.start()
        manifest = first.failed_manifest_path()
        check(
            "失败重试：首轮 2 个失败且生成清单",
            first.failed_count == 2 and manifest.exists(),
            f"failed={first.failed_count} manifest={manifest.exists()}",
        )
        if manifest.exists():
            lines = [
                line.strip() for line in manifest.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]
            check(
                "失败重试：清单内容就是失败的瓦片",
                sorted(lines) == ["8 0 0", "8 1 0"],
                f"{lines}",
            )

        # 修好服务端，只补失败
        server.httpd.error_tiles = set()
        server.reset_paths()
        before = server.requests
        second = make_downloader(
            "retry_second", server, out, max_threads=4, save_format="directory"
        )
        count = second.add_failed_tasks()
        second.start()
        requests = server.requests - before

        check(
            "失败重试：只请求失败的 2 个瓦片",
            count == 2 and requests == 2,
            f"count={count} requests={requests}",
        )
        check("失败重试：补下后没有失败", second.failed_count == 0)
        check(
            "失败重试：最终文件数完整",
            len(list_tiles(out)) == len(tiles),
            f"files={len(list_tiles(out))}/{len(tiles)}",
        )
        check("失败重试：陈旧清单已被清理", not manifest.exists())
    finally:
        server.close()


# --------------------------------------------------------------------------- #
def main():
    print("=" * 70)
    print("TileScraper 数据正确性 / 限流 / 崩溃恢复 真实服务器回归")
    print("=" * 70)
    for scenario in (
        scenario_atomic_and_validation,
        scenario_artifact_verification,
        scenario_mbtiles_verification,
        scenario_failed_status_retried,
        scenario_rate_limit,
        scenario_signal_graceful,
        scenario_crash_resume_directory,
        scenario_crash_resume_mbtiles,
        scenario_output_lock,
        scenario_ack_no_loss_without_verify,
        scenario_failed_retry,
    ):
        print(f"\n--- {scenario.__name__} ---")
        try:
            scenario()
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            check(scenario.__name__, False, f"异常: {exc}")

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 70)
    print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    for name in failed:
        print(f"  FAIL: {name}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        # 崩溃恢复场景的子进程入口：正常下载，等父进程 SIGKILL
        _run_child(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
        sys.exit(0)
    sys.exit(main())

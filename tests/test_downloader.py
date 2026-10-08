"""回归测试：覆盖曾出现的严重缺陷以及核心下载路径。

运行方式（在项目根目录）::

    python -m unittest discover -s tests -v
"""

import os
import signal
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from src.downloader.request import RequestSessionManager
from src.tile_math import TileMath
from tests._helpers import PNG, FakeNetworkMixin, make_downloader, make_provider

# 北京五环内一小块区域，瓦片数量适中
BBOX = dict(west=116.30, south=39.80, east=116.50, north=40.00)


class TileMathTests(unittest.TestCase):
    def test_count_matches_generated_list(self):
        w, s, e, n = BBOX["west"], BBOX["south"], BBOX["east"], BBOX["north"]
        for zoom in (8, 10, 12):
            count = TileMath.count_tiles_in_bbox(w, s, e, n, zoom)
            listed = len(TileMath.calculate_tiles_in_bbox(w, s, e, n, zoom))
            self.assertEqual(count, listed)

    def test_count_is_o1_for_huge_range(self):
        # 全球 z=18：不应构造列表，仅返回数量
        count = TileMath.count_tiles_in_bbox(-180, -85, 180, 85, 18)
        self.assertGreater(count, 10 ** 9)


class DownloadTests(FakeNetworkMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()

    def _run(self, **kwargs):
        provider = make_provider()
        downloader = make_downloader(provider, **kwargs)
        downloader.add_tasks_for_bbox(**BBOX, min_zoom=10, max_zoom=11)
        downloader.start()
        return downloader

    def test_directory_download(self):
        out = os.path.join(self.tmp, "tiles")
        downloader = self._run(output_dir=out, max_threads=4, save_format="directory")
        stats = downloader.get_statistics()
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(stats["remaining"], 0)
        files = [
            f for _, _, fs in os.walk(out) for f in fs if f.endswith(".png")
        ]
        self.assertEqual(len(files), stats["downloaded"])

    def test_mbtiles_rows_persisted(self):
        """回归：下载线程与写线程曾使用不同队列，导致瓦片从未落库。"""
        out = os.path.join(self.tmp, "t.mbtiles")
        downloader = self._run(
            output_dir=out, max_threads=4, save_format="mbtiles", scheme="xyz"
        )
        stats = downloader.get_statistics()

        conn = sqlite3.connect(out)
        rows = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        meta = dict(conn.execute("SELECT name, value FROM metadata").fetchall())
        conn.close()

        self.assertEqual(rows, stats["downloaded"])
        self.assertGreater(rows, 0)
        self.assertEqual(meta["scheme"], "xyz")

    def test_resume_skips_processed(self):
        out = os.path.join(self.tmp, "tiles")
        first = self._run(output_dir=out, max_threads=4, save_format="directory")
        self.assertGreater(first.downloaded_count, 0)

        # 第二次对同一 bbox 运行：全部应被跳过
        provider = make_provider()
        second = make_downloader(provider, out, max_threads=4, save_format="directory")
        second.add_tasks_for_bbox(**BBOX, min_zoom=10, max_zoom=11)
        second.start()
        self.assertEqual(second.downloaded_count, 0)
        self.assertEqual(second.skipped_count, second.total_tasks)

    def test_start_returns(self):
        """回归：旧实现中 start() 会因工作线程永不退出而永久阻塞。"""
        out = os.path.join(self.tmp, "tiles")
        provider = make_provider()
        downloader = make_downloader(provider, out, max_threads=2, save_format="directory")
        downloader.add_tasks_for_bbox(**BBOX, min_zoom=9, max_zoom=9)
        thread = threading.Thread(target=downloader.start, daemon=True)
        thread.start()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "start() 未在超时内返回")

    def test_cancel_stops_workers(self):
        out = os.path.join(self.tmp, "tiles")
        provider = make_provider()
        downloader = make_downloader(provider, out, max_threads=4, save_format="directory")
        downloader.add_tasks_for_bbox(-30, -20, 30, 20, 3, 7)
        thread = threading.Thread(target=downloader.start, daemon=True)
        thread.start()
        time.sleep(0.2)
        stats = downloader.cancel()
        thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertIn("downloaded", stats)


class PauseResumeTests(FakeNetworkMixin, unittest.TestCase):
    chunk_delay = 0.03

    def test_pause_does_not_kill_workers(self):
        tmp = tempfile.mkdtemp()
        provider = make_provider()
        downloader = make_downloader(
            provider, os.path.join(tmp, "tiles"), max_threads=4, save_format="directory"
        )
        downloader.add_tasks_for_bbox(-40, -30, 40, 30, 3, 7)
        thread = threading.Thread(target=downloader.start, daemon=True)
        thread.start()
        time.sleep(0.4)
        downloader.pause()
        time.sleep(0.3)

        def live_workers():
            return sum(
                1 for t in threading.enumerate() if t.name.startswith("Downloader-")
            )

        before = live_workers()
        time.sleep(0.8)
        after = live_workers()

        downloader.resume()
        thread.join(timeout=120)

        self.assertTrue(downloader.is_paused() is False)
        self.assertEqual(before, after, "暂停后工作线程数量发生了变化（线程被误杀）")
        self.assertFalse(thread.is_alive())
        self.assertEqual(downloader.get_statistics()["remaining"], 0)


def _seed_progress_db(progress_dir, rows, status="success"):
    """在目录模式下预置一个已存在的进度库（模拟历史下载记录）。

    ``rows`` 可以是 ``(x, y, z)``（统一用 ``status``）或
    ``(x, y, z, status)``（逐行指定状态）。
    """
    def _normalize(row):
        if len(row) >= 4:
            return row[0], row[1], row[2], row[3]
        return row[0], row[1], row[2], status

    os.makedirs(progress_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(progress_dir, "progress.db"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS processed_tiles ("
        "x INTEGER, y INTEGER, z INTEGER, status TEXT, PRIMARY KEY (x, y, z))"
    )
    conn.executemany(
        "INSERT OR REPLACE INTO processed_tiles (x, y, z, status) VALUES (?, ?, ?, ?)",
        [_normalize(row) for row in rows],
    )
    conn.commit()
    conn.close()


class ResumeStreamingTests(FakeNetworkMixin, unittest.TestCase):
    """断点续传的内存/规模解耦：流式归并 + 分页查询。

    这些用例只关心"归并跳过"本身，因此关掉产物存在性校验（默认开启），
    否则预置的进度记录会因为磁盘上没有对应文件而被判定为需要重下。
    """

    def _make(self, out, **kwargs):
        provider = make_provider()
        downloader = make_downloader(provider, out, **kwargs)
        downloader.verify_artifacts = False
        return downloader

    def _tiles(self, zoom=10):
        min_x, min_y, max_x, max_y = TileMath.bbox_tile_bounds(
            BBOX["west"], BBOX["south"], BBOX["east"], BBOX["north"], zoom
        )
        return [
            (x, y, zoom, "success")
            for x in range(min_x, max_x + 1)
            for y in range(min_y, max_y + 1)
        ]

    def test_partial_history_skipped_with_paging(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        all_tiles = self._tiles(10)
        seeded = all_tiles[: len(all_tiles) // 2]
        _seed_progress_db(out, seeded)

        provider = make_provider()
        downloader = make_downloader(provider, out, max_threads=4, save_format="directory")
        downloader.verify_artifacts = False
        # 强制走多页，验证 keyset 分页逻辑
        downloader.progress_manager.page_size = 2
        downloader.add_tasks_for_bbox(**BBOX, min_zoom=10, max_zoom=10)
        downloader.start()

        stats = downloader.get_statistics()
        self.assertEqual(stats["skipped"], len(seeded))
        self.assertEqual(stats["downloaded"], len(all_tiles) - len(seeded))

    def test_large_history_does_not_use_inmemory_set(self):
        """核心回归：不得再把全量已处理瓦片读进内存集合。"""
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        # 造一个较大的历史：z=13 覆盖一个较大范围（数万条）
        min_x, min_y, max_x, max_y = TileMath.bbox_tile_bounds(
            -10, -10, 10, 10, 13
        )
        seeded = [
            (x, y, 13, "success")
            for x in range(min_x, max_x + 1)
            for y in range(min_y, max_y + 1)
        ]
        _seed_progress_db(out, seeded)
        self.assertGreater(len(seeded), 20000)

        provider = make_provider()
        downloader = make_downloader(provider, out, max_threads=4, save_format="directory")
        downloader.verify_artifacts = False
        # 这里刻意把页大小设为很小，确保多页场景也能正确归并
        downloader.progress_manager.page_size = 1000

        # 关键断言：进度处理器不再持有全量内存集合
        self.assertFalse(
            hasattr(downloader.progress_manager, "processed_tiles"),
            "progress_manager 不应再维护全量 processed_tiles 集合",
        )

        downloader.add_tasks_for_bbox(-10, -10, 10, 10, 13, 13)
        downloader.start()
        stats = downloader.get_statistics()
        self.assertEqual(stats["skipped"], len(seeded))
        self.assertEqual(stats["downloaded"], 0)


class ApiTests(FakeNetworkMixin, unittest.TestCase):
    def test_api_mbtiles_roundtrip(self):
        from app import app

        client = app.test_client()
        out = os.path.join(tempfile.mkdtemp(), "api.mbtiles")
        payload = dict(
            provider_url="http://tiles.local/{z}/{x}/{y}.png",
            subdomains=["a", "b"],
            tile_format="png",
            save_format="mbtiles",
            output_dir=out,
            threads=4,
            tms=False,
            min_zoom=11,
            max_zoom=12,
            **BBOX,
        )
        resp = client.post("/api/download", json=payload)
        self.assertEqual(resp.status_code, 200)
        total = resp.get_json()["total"]

        for _ in range(300):
            status = client.get("/api/download-status").get_json()
            if not status.get("is_downloading"):
                break
            time.sleep(0.1)

        rows = sqlite3.connect(out).execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        self.assertEqual(rows, total)
        self.assertGreater(rows, 0)

    def test_api_rejects_bad_params(self):
        from app import app

        client = app.test_client()
        resp = client.post("/api/download", json={"north": 1})
        self.assertEqual(resp.status_code, 400)


class SaturatedQueuePauseTests(FakeNetworkMixin, unittest.TestCase):
    """回归：有界任务队列被生产者打满时，暂停曾导致永久死锁。

    旧实现在暂停时把任务 ``put`` 回队列（且没有超时）。队列满 + 生产者仍在
    生产 + 没有消费者，三者会互相等待，``start()`` 永不返回。
    """

    chunk_delay = 0.005

    def setUp(self):
        super().setUp()
        from src.config import config_manager

        self.tmp = tempfile.mkdtemp()
        self._old_queue_size = config_manager.config["download"].get("task_queue_size")
        # 故意把队列压到很小，确保生产者一定会把它填满
        config_manager.config["download"]["task_queue_size"] = 4
        self.addCleanup(
            config_manager.config["download"].__setitem__,
            "task_queue_size",
            self._old_queue_size,
        )

    def test_pause_resume_with_saturated_queue(self):
        out = os.path.join(self.tmp, "tiles")
        provider = make_provider()
        downloader = make_downloader(provider, out, max_threads=4, save_format="directory")
        # 用足量任务保证暂停时下载仍在进行（约 700 个瓦片）
        downloader.add_tasks_for_bbox(-1, -1, 1, 1, 8, 11)
        self.assertGreater(downloader.total_tasks, downloader.task_queue.maxsize)

        thread = threading.Thread(target=downloader.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        downloader.pause()
        # 暂停后生产者仍会继续填充队列直到打满，这正是旧实现的死锁条件
        time.sleep(0.3)
        queue_was_full = downloader.task_queue.qsize() == downloader.task_queue.maxsize
        producer_still_running = not downloader.all_tasks_added.is_set()
        downloader.resume()

        thread.join(timeout=60)
        self.assertFalse(thread.is_alive(), "暂停/恢复后 start() 未返回（死锁回归）")
        self.assertTrue(queue_was_full, "测试未复现出'队列打满'的前置条件")
        self.assertTrue(producer_still_running, "测试未复现出'生产者仍在生产'的前置条件")

        stats = downloader.get_statistics()
        self.assertEqual(stats["remaining"], 0)
        self.assertEqual(stats["failed"], 0)
        files = [f for _, _, fs in os.walk(out) for f in fs if f.endswith(".png")]
        self.assertEqual(len(files), stats["downloaded"])


class PreloadTaskTests(FakeNetworkMixin, unittest.TestCase):
    """回归：start() 之前预置超过队列容量的任务曾永久阻塞调用方。"""

    def setUp(self):
        super().setUp()
        from src.config import config_manager

        self.tmp = tempfile.mkdtemp()
        self._old_queue_size = config_manager.config["download"].get("task_queue_size")
        config_manager.config["download"]["task_queue_size"] = 3
        self.addCleanup(
            config_manager.config["download"].__setitem__,
            "task_queue_size",
            self._old_queue_size,
        )

    def test_add_tasks_over_capacity_uses_streaming(self):
        out = os.path.join(self.tmp, "tiles")
        provider = make_provider()
        downloader = make_downloader(provider, out, max_threads=2, save_format="directory")

        tiles = [(x, y, 12) for x in range(2) for y in range(5)]
        self.assertGreater(len(tiles), downloader.task_queue.maxsize)

        waiter = threading.Thread(target=downloader.add_tasks, args=(tiles,), daemon=True)
        waiter.start()
        waiter.join(timeout=5)
        self.assertFalse(waiter.is_alive(), "add_tasks() 在 start() 之前被永久阻塞")

        downloader.start()
        stats = downloader.get_statistics()
        self.assertEqual(stats["total"], len(tiles))
        self.assertEqual(stats["downloaded"], len(tiles))
        self.assertEqual(stats["remaining"], 0)

    def test_add_task_raises_clear_error_when_full(self):
        provider = make_provider()
        downloader = make_downloader(
            provider, os.path.join(self.tmp, "t2"), max_threads=1, save_format="directory"
        )
        for i in range(3):
            downloader.add_task(i, i, 12)
        with self.assertRaises(ValueError):
            downloader.add_task(99, 99, 12)


class DownloaderConfigTests(unittest.TestCase):
    """超时 / 重试 / 代理等配置要真正生效。"""

    def test_timeout_and_retries_from_config(self):
        from src.config import config_manager

        provider = make_provider()
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        with mock.patch.dict(
            config_manager.config["download"], {"timeout": 17, "max_retries": 5}
        ):
            downloader = make_downloader(provider, out, max_threads=1)
            self.assertEqual(downloader.timeout, 17)
            self.assertEqual(downloader.retries, 5)

        # 显式传参优先级高于配置（换一个输出目录，避免与上一个实例的输出锁冲突）
        out2 = os.path.join(tempfile.mkdtemp(), "tiles")
        downloader = make_downloader(
            provider, out2, max_threads=1, timeout=3, retries=1
        )
        self.assertEqual(downloader.timeout, 3)
        self.assertEqual(downloader.retries, 1)

    def test_proxy_defaults_to_direct_connection(self):
        from src.config import config_manager

        provider = make_provider()
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        with mock.patch.dict(
            os.environ,
            {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"},
        ), mock.patch.dict(config_manager.config["download"], {"proxy": ""}):
            downloader = make_downloader(provider, out, max_threads=1)
            self.assertEqual(downloader.proxy, "")


class RequestSessionTests(unittest.TestCase):
    """代理语义：默认直连，可显式指定代理或用环境变量代理。"""

    def setUp(self):
        env = mock.patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://proxy.local:7890",
                "HTTPS_PROXY": "http://proxy.local:7890",
                "http_proxy": "http://proxy.local:7890",
                "https_proxy": "http://proxy.local:7890",
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def test_default_direct_ignores_env_proxy(self):
        from src.downloader.request import RequestSessionManager

        session = RequestSessionManager(proxy="").create_session()
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies, {})
        merged = session.merge_environment_settings(
            "http://tiles.example.com/1/2/3.png", {}, True, True, None
        )
        self.assertEqual(merged["proxies"], {}, "环境变量代理不应被使用")

    def test_explicit_proxy_is_used(self):
        from src.downloader.request import RequestSessionManager

        session = RequestSessionManager(proxy="http://127.0.0.1:7890").create_session()
        self.assertFalse(session.trust_env)
        self.assertEqual(
            session.proxies,
            {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
        )

    def test_env_mode_follows_environment(self):
        from src.downloader.request import RequestSessionManager

        session = RequestSessionManager(proxy="env").create_session()
        self.assertTrue(session.trust_env)
        merged = session.merge_environment_settings(
            "http://tiles.example.com/1/2/3.png", {}, True, True, None
        )
        self.assertEqual(merged["proxies"].get("http"), "http://proxy.local:7890")

    def test_recreate_replaces_session(self):
        from src.downloader.request import RequestSessionManager

        manager = RequestSessionManager(proxy="")
        old = manager.create_session()
        new = manager.recreate()
        self.assertIsNot(old, new)
        self.assertIs(manager.get_session(), new)


class ThreadCountTests(unittest.TestCase):
    """线程数解析：默认值、auto 自动、硬上限截断。"""

    def test_explicit_value_and_hard_limit(self):
        from src.config import config_manager
        from src.downloader.utils import resolve_thread_count

        hard_limit = int(config_manager.get("download.max_threads_hard_limit", 128))
        self.assertEqual(resolve_thread_count(4), 4)
        self.assertEqual(resolve_thread_count(8), 8)
        self.assertEqual(resolve_thread_count(hard_limit + 100), hard_limit)
        self.assertEqual(resolve_thread_count(0), resolve_thread_count("auto"))
        self.assertEqual(resolve_thread_count(-5), resolve_thread_count("auto"))

    def test_auto_is_clamped(self):
        from src.config import config_manager
        from src.downloader.utils import resolve_thread_count

        low = int(config_manager.get("download.threads_auto_min", 8))
        high = int(config_manager.get("download.threads_auto_max", 32))
        expected = min(high, max(low, (os.cpu_count() or 4) * 4))
        expected = min(expected, int(config_manager.get("download.max_threads_hard_limit", 128)))
        self.assertEqual(resolve_thread_count("auto"), expected)

    def test_none_falls_back_to_config(self):
        from src.config import config_manager
        from src.downloader.utils import resolve_thread_count

        with mock.patch.dict(config_manager.config["download"], {"threads": 11}):
            self.assertEqual(resolve_thread_count(None), 11)
        with mock.patch.dict(config_manager.config["download"], {"threads": "auto"}):
            self.assertEqual(resolve_thread_count(None), resolve_thread_count("auto"))

    def test_parse_threads_param(self):
        from src.downloader.controller import parse_threads_param

        self.assertIsNone(parse_threads_param(None))
        self.assertEqual(parse_threads_param(""), "auto")
        self.assertEqual(parse_threads_param("auto"), "auto")
        self.assertEqual(parse_threads_param("  AUTO "), "auto")
        self.assertEqual(parse_threads_param(0), "auto")
        self.assertEqual(parse_threads_param("12"), 12)
        self.assertEqual(parse_threads_param(12), 12)
        for bad in ("abc", -1, 1.5, True):
            with self.assertRaises(ValueError):
                parse_threads_param(bad)


# --------------------------------------------------------------------------- #
# 本轮新增：数据正确性 / 内容校验 / 限流自适应 / 收尾安全
# --------------------------------------------------------------------------- #

class _ScriptedResponse:
    """可按需构造状态码 / 响应头 / 响应体的假响应。"""

    def __init__(self, status_code=200, body=PNG, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers if headers is not None else {"Content-Type": "image/png"}

    def iter_content(self, chunk_size=8192):
        if self._body:
            yield self._body

    def close(self):
        pass


class _ScriptedSession:
    """把 (url, 调用序号) 映射成响应或异常的假会话。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        result = self._handler(url, len(self.calls))
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


def _list_tiles(out):
    return sorted(
        os.path.join(root, f)
        for root, _, files in os.walk(out)
        for f in files
        if f.endswith(".png")
    )


class AtomicWriteTests(FakeNetworkMixin, unittest.TestCase):
    """回归：目录模式直接写目标文件会留下半截瓦片，且被断点续传永久跳过。"""

    def test_successful_write_leaves_no_temp_files(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        provider = make_provider()
        downloader = make_downloader(
            provider, out, max_threads=2, save_format="directory"
        )
        downloader.add_tasks_for_bbox(**BBOX, min_zoom=12, max_zoom=12)
        downloader.start()

        self.assertEqual(downloader.failed_count, 0)
        self.assertGreater(len(_list_tiles(out)), 0)
        leftovers = [
            os.path.join(root, f)
            for root, _, files in os.walk(out)
            for f in files
            if f.endswith(".part")
        ]
        self.assertEqual(leftovers, [], "临时文件未被清理")

    def test_replace_failure_leaves_no_partial_target(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        provider = make_provider()
        downloader = make_downloader(
            provider, out, max_threads=1, retries=1, save_format="directory"
        )
        downloader.add_task(3, 4, 10)

        with mock.patch(
            "src.downloader.worker.os.replace", side_effect=OSError("disk full")
        ):
            downloader.start()

        target = downloader.provider.get_tile_path(3, 4, 10, out)
        self.assertFalse(target.exists(), "写盘失败时不应留下（可能半截的）目标文件")
        self.assertEqual(downloader.failed_count, 1)
        leftovers = [
            os.path.join(root, f)
            for root, _, files in os.walk(out)
            for f in files
            if f.endswith(".part")
        ]
        self.assertEqual(leftovers, [], "失败后应清理临时文件")


    def test_stale_part_is_cleaned_on_rewrite(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        provider = make_provider()
        downloader = make_downloader(
            provider, out, max_threads=1, save_format="directory"
        )
        target = downloader.provider.get_tile_path(3, 4, 10, out)
        os.makedirs(target.parent, exist_ok=True)

        stale = target.parent / f".{target.name}.stale123.part"
        stale.write_bytes(b"half-written")
        old = time.time() - 25 * 3600
        os.utime(stale, (old, old))
        fresh = target.parent / f".{target.name}.inflight.part"
        fresh.write_bytes(b"another-runner")

        downloader.add_task(3, 4, 10)
        downloader.start()

        self.assertFalse(stale.exists(), "超龄残留临时文件应被清理")
        self.assertTrue(fresh.exists(), "未超龄的临时文件不应被删除")
        self.assertTrue(target.exists(), "目标瓦片应正常写出")
        self.assertTrue(target.read_bytes().startswith(PNG), "目标文件应是完整瓦片")


class ArtifactVerificationTests(FakeNetworkMixin, unittest.TestCase):
    """回归：进度库是"唯一权威"时，删掉产物就再也下不回来。"""

    def test_deleted_files_are_redownloaded(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        first = make_downloader(
            make_provider(), out, max_threads=4, save_format="directory"
        )
        first.add_tasks_for_bbox(**BBOX, min_zoom=11, max_zoom=11)
        first.start()
        self.assertGreater(first.downloaded_count, 3)
        total = first.downloaded_count

        # 删掉 3 个瓦片文件，但保留 progress.db
        victims = _list_tiles(out)[:3]
        self.assertEqual(len(victims), 3)
        for path in victims:
            os.remove(path)

        second = make_downloader(
            make_provider(), out, max_threads=4, save_format="directory"
        )
        second.add_tasks_for_bbox(**BBOX, min_zoom=11, max_zoom=11)
        second.start()

        stats = second.get_statistics()
        self.assertEqual(stats["downloaded"], 3, "被删除的产物应被重新下载")
        self.assertEqual(stats["redownloaded"], 3)
        self.assertEqual(len(_list_tiles(out)), total, "最终产物数量应恢复完整")

    def test_failed_records_are_not_treated_as_done(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        min_x, min_y, max_x, max_y = TileMath.bbox_tile_bounds(
            BBOX["west"], BBOX["south"], BBOX["east"], BBOX["north"], 10
        )
        all_tiles = [
            (x, y, 10) for x in range(min_x, max_x + 1) for y in range(min_y, max_y + 1)
        ]
        _seed_progress_db(
            out, [(x, y, z, "failed") for x, y, z in all_tiles], status="failed"
        )

        downloader = make_downloader(
            make_provider(), out, max_threads=4, save_format="directory"
        )
        downloader.add_tasks_for_bbox(**BBOX, min_zoom=10, max_zoom=10)
        downloader.start()

        self.assertEqual(downloader.skipped_count, 0, "failed 记录不应被跳过")
        self.assertEqual(downloader.downloaded_count, len(all_tiles))

    def test_missing_mbtiles_rows_are_redownloaded(self):
        tmp = tempfile.mkdtemp()
        out = os.path.join(tmp, "t.mbtiles")
        first = make_downloader(
            make_provider(), out, max_threads=4, save_format="mbtiles"
        )
        first.add_tasks_for_bbox(**BBOX, min_zoom=11, max_zoom=12)
        first.start()

        conn = sqlite3.connect(out)
        before = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        self.assertGreater(before, 3)
        conn.execute(
            "DELETE FROM tiles WHERE rowid IN (SELECT rowid FROM tiles LIMIT 3)"
        )
        conn.commit()
        conn.close()

        second = make_downloader(
            make_provider(), out, max_threads=4, save_format="mbtiles"
        )
        second.add_tasks_for_bbox(**BBOX, min_zoom=11, max_zoom=12)
        second.start()

        self.assertEqual(second.redownload_count, 3)
        conn = sqlite3.connect(out)
        after = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        conn.close()
        self.assertEqual(after, before, "缺失的 MBTiles 行应被补齐")

    def test_existing_rows_are_not_redownloaded_without_progress_db(self):
        """
        崩溃/强杀后 ``progress.db`` 可能落后甚至丢失，但 tiles 表是权威产物记录：
        已提交的行绝不能被重新请求（否则就是"重复下载"）。
        """
        tmp = tempfile.mkdtemp()
        out = os.path.join(tmp, "t.mbtiles")
        first = make_downloader(
            make_provider(), out, max_threads=4, save_format="mbtiles"
        )
        first.add_tasks_for_bbox(**BBOX, min_zoom=11, max_zoom=12)
        first.start()
        total = first.downloaded_count
        self.assertGreater(total, 3)

        # 删掉进度库：模拟崩溃后 progress 完全没有落盘
        os.remove(os.path.join(tmp, "t.progress.db"))

        second = make_downloader(
            make_provider(), out, max_threads=4, save_format="mbtiles"
        )
        second.add_tasks_for_bbox(**BBOX, min_zoom=11, max_zoom=12)
        second.start()

        self.assertEqual(second.downloaded_count, 0, "已提交的 tiles 被重复下载了")
        self.assertEqual(second.skipped_count, total)
        conn = sqlite3.connect(out)
        rows = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        conn.close()
        self.assertEqual(rows, total)


class ContentValidationTests(FakeNetworkMixin, unittest.TestCase):
    """回归：HTTP 200 + 错误页/空白瓦片曾被当作成功落盘。"""

    def _run_with(self, response_body, content_type="image/png", attr_overrides=None):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        provider = make_provider()
        downloader = make_downloader(
            provider, out, max_threads=1, retries=1, save_format="directory"
        )
        for key, value in (attr_overrides or {}).items():
            setattr(downloader, key, value)
        session = _ScriptedSession(
            lambda url, n: _ScriptedResponse(
                200, response_body, {"Content-Type": content_type}
            )
        )
        with mock.patch.object(
            RequestSessionManager, "get_session", lambda mgr: session
        ):
            downloader.add_task(5, 6, 10)
            downloader.start()
        return downloader, downloader.provider, out

    def test_html_error_page_is_rejected(self):
        html = b"<!DOCTYPE html><html><body>403 Forbidden</body></html>"
        downloader, provider, out = self._run_with(html)
        self.assertEqual(downloader.failed_count, 1)
        self.assertEqual(downloader.downloaded_count, 0)
        self.assertFalse(provider.get_tile_path(5, 6, 10, out).exists())

    def test_valid_png_is_accepted(self):
        downloader, _, _ = self._run_with(PNG)
        self.assertEqual(downloader.downloaded_count, 1)
        self.assertEqual(downloader.failed_count, 0)

    def test_blank_tile_filter_is_optional(self):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (256, 256), (255, 255, 255)).save(buffer, format="PNG")
        blank = buffer.getvalue()

        # 默认不过滤纯色瓦片
        downloader, _, _ = self._run_with(blank)
        self.assertEqual(downloader.downloaded_count, 1)

        # 打开开关后应被拒绝
        downloader, _, _ = self._run_with(
            blank, attr_overrides={"reject_blank_tiles": True}
        )
        self.assertEqual(downloader.failed_count, 1)
        self.assertEqual(downloader.downloaded_count, 0)


class SignalHandlerTests(FakeNetworkMixin, unittest.TestCase):
    """回归：信号处理里的 sys.exit 会绕过 start() 的收尾路径。"""

    chunk_delay = 0.02

    def test_signal_handler_does_not_exit(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=1, save_format="directory"
        )
        try:
            downloader.signal_handler._signal_handler(signal.SIGINT, None)
        except SystemExit:
            self.fail("信号处理不应调用 sys.exit()，否则会绕过收尾")
        self.assertTrue(downloader.stop_event.is_set())
        self.assertTrue(downloader.pause_event.is_set())

    def test_signal_during_download_returns_and_saves_progress(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=4, save_format="directory"
        )
        downloader.add_tasks_for_bbox(-40, -30, 40, 30, 3, 7)

        thread = threading.Thread(target=downloader.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        downloader.signal_handler._signal_handler(signal.SIGTERM, None)

        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "收到信号后 start() 未正常返回")

        progress_db = os.path.join(out, "progress.db")
        self.assertTrue(os.path.exists(progress_db), "收尾时应保存进度库")
        conn = sqlite3.connect(progress_db)
        rows = conn.execute("SELECT COUNT(*) FROM processed_tiles").fetchone()[0]
        conn.close()
        self.assertGreater(rows, 0)


class RetryAfterTests(unittest.TestCase):
    def test_seconds(self):
        from src.downloader.rate_limiter import parse_retry_after

        self.assertEqual(parse_retry_after("120"), 120.0)
        self.assertEqual(parse_retry_after("0"), 0.0)
        self.assertEqual(parse_retry_after(30), 30.0)

    def test_http_date(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime

        from src.downloader.rate_limiter import parse_retry_after

        when = datetime.now(timezone.utc) + timedelta(seconds=60)
        seconds = parse_retry_after(format_datetime(when))
        self.assertIsNotNone(seconds)
        self.assertGreater(seconds, 30)
        self.assertLessEqual(seconds, 61)

    def test_invalid_values(self):
        from src.downloader.rate_limiter import parse_retry_after

        for bad in (None, "", "not-a-date", "Wed, 99 Xxx 9999 99:99:99 GMT"):
            self.assertIsNone(parse_retry_after(bad))


class ConcurrencyLimiterTests(unittest.TestCase):
    def test_throttle_shrinks_then_recovers(self):
        from src.downloader.rate_limiter import ConcurrencyLimiter

        limiter = ConcurrencyLimiter(
            limit=8, min_limit=1, shrink_interval=0, recover_interval=0,
            recover_successes=1,
        )
        cooldown = limiter.note_throttled(3)
        self.assertEqual(cooldown, 3.0)
        self.assertLess(limiter.limit, 8)
        self.assertEqual(limiter.throttle_events, 1)

        shrunk = limiter.limit
        limiter.note_throttled(0)
        self.assertLess(limiter.limit, shrunk)

        # 冷却结束后连续成功 → 逐步恢复
        limiter._cooldown_until = 0
        for _ in range(60):
            limiter.note_success()
        self.assertEqual(limiter.limit, 8)

    def test_cooldown_is_capped(self):
        from src.downloader.rate_limiter import ConcurrencyLimiter

        limiter = ConcurrencyLimiter(limit=4, cooldown_max=5)
        self.assertEqual(limiter.note_throttled(999), 5.0)

    def test_acquire_release_and_abort(self):
        from src.downloader.rate_limiter import ConcurrencyLimiter

        limiter = ConcurrencyLimiter(limit=1)
        self.assertTrue(limiter.acquire())
        self.assertEqual(limiter.active, 1)
        limiter.release()
        self.assertEqual(limiter.active, 0)

        stop_event = threading.Event()
        self.assertTrue(limiter.acquire())
        stop_event.set()
        self.assertFalse(limiter.acquire(stop_event=stop_event), "停止时应放弃获取槽位")
        limiter.release()


class RateLimitWorkerTests(FakeNetworkMixin, unittest.TestCase):
    """收到 429/Retry-After 时应重试并降低并发上限。"""

    def test_429_is_retried_and_shrinks_concurrency(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        provider = make_provider()
        downloader = make_downloader(
            provider, out, max_threads=4, retries=3, delay=0.0,
            save_format="directory",
        )

        def handler(url, n):
            if n == 1:
                return _ScriptedResponse(
                    429, b"", {"Content-Type": "text/plain", "Retry-After": "0"}
                )
            return _ScriptedResponse(200, PNG)

        session = _ScriptedSession(handler)
        with mock.patch.object(
            RequestSessionManager, "get_session", lambda mgr: session
        ):
            downloader.add_task(1, 2, 10)
            downloader.start()

        self.assertEqual(downloader.downloaded_count, 1, "限流后重试应成功")
        self.assertEqual(downloader.failed_count, 0)
        self.assertIsNotNone(downloader.limiter)
        self.assertEqual(downloader.limiter.throttle_events, 1)
        self.assertLess(downloader.limiter.limit, 4, "限流后应降低并发上限")

    def test_adaptive_concurrency_can_be_disabled(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=2, save_format="directory"
        )
        downloader.adaptive_concurrency = False
        downloader.add_task(1, 1, 5)
        downloader.start()
        self.assertIsNone(downloader.limiter)


class MBTilesDrainTests(FakeNetworkMixin, unittest.TestCase):
    """收尾安全：写线程已死且仍有未落盘数据时，必须报错且不许关连接。"""

    def test_undrained_close_keeps_connection(self):
        out = os.path.join(tempfile.mkdtemp(), "t.mbtiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=1, save_format="mbtiles"
        )
        handler = downloader.mbtiles_handler

        # 停掉写线程，再塞入一个永远不会被消费的瓦片
        handler._stop_mbtiles_writer()
        downloader.mbtiles_write_queue.put((10, 1, 2, b"data"))

        self.assertFalse(handler.wait_drained(timeout=0.2))
        self.assertFalse(handler.finalize())

        handler.close()
        self.assertIsNotNone(
            handler.mbtiles_conn, "仍有数据未落盘时不应关闭数据库连接"
        )

        # 清理：把假的未完成任务标记完成并关掉连接
        downloader.mbtiles_write_queue.task_done()
        handler._safe_close(handler.mbtiles_conn)
        handler.mbtiles_conn = None

    def test_normal_mbtiles_run_drains_completely(self):
        out = os.path.join(tempfile.mkdtemp(), "t.mbtiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=4, save_format="mbtiles"
        )
        downloader.add_tasks_for_bbox(**BBOX, min_zoom=11, max_zoom=11)
        downloader.start()

        self.assertEqual(downloader.mbtiles_write_queue.unfinished_tasks, 0)
        conn = sqlite3.connect(out)
        rows = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        conn.close()
        self.assertEqual(rows, downloader.downloaded_count)


class AddTasksStatsTests(FakeNetworkMixin, unittest.TestCase):
    """回归：add_tasks 把已跳过瓦片计入 skipped 却不计入 total，进度会超过 100%。"""

    def test_skipped_tiles_counted_in_total(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        _seed_progress_db(out, [(1, 1, 5, "success")], status="success")

        downloader = make_downloader(
            make_provider(), out, max_threads=1, save_format="directory"
        )
        downloader.verify_artifacts = False  # 只关心统计口径
        downloader.add_tasks([(1, 1, 5), (2, 2, 5)])

        self.assertEqual(downloader.total_tasks, 2, "跳过的瓦片也要计入 total")
        self.assertEqual(downloader.skipped_count, 1)

        downloader.start()
        stats = downloader.get_statistics()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["downloaded"], 1)
        processed = stats["downloaded"] + stats["failed"] + stats["skipped"]
        self.assertLessEqual(processed, stats["total"], "processed 不应超过 total")


class ProviderIsolationTests(unittest.TestCase):
    """回归：下载器曾直接修改全局 provider 的 extension / is_tms。"""

    def test_tile_format_does_not_leak_to_global_provider(self):
        from src.providers import ProviderManager

        name = make_provider()
        global_provider = ProviderManager.get_provider(name)
        before = global_provider.extension

        out = os.path.join(tempfile.mkdtemp(), "tiles")
        downloader = make_downloader(name, out, max_threads=1, tile_format="jpg")
        self.assertEqual(downloader.provider.extension, "jpg")
        self.assertIsNot(downloader.provider, global_provider)
        self.assertEqual(
            ProviderManager.get_provider(name).extension, before,
            "全局 provider 的扩展名被下载器改掉了",
        )

    def test_builtin_provider_cannot_be_overwritten(self):
        from src.providers import ProviderManager

        before = ProviderManager.get_provider("bing").url_template
        with self.assertRaises(ValueError):
            ProviderManager.create_custom_provider(
                "bing", "http://evil.local/{z}/{x}/{y}.png"
            )
        after = ProviderManager.get_provider("bing").url_template
        self.assertEqual(before, after, "内置 Bing 模板被自定义源劫持了")


class TmsSemanticsTests(FakeNetworkMixin, unittest.TestCase):
    """
    TMS 约定（修正后）：任务坐标始终是 XYZ，``is_tms`` 只决定"URL 里的行号"。

    * URL：``is_tms=True`` 时 ``{y}`` 翻转；``{-y}`` 无论开关都翻转；
    * 落盘路径：始终用 XYZ 行号（目录布局与开关无关）；
    * MBTiles 的 ``tile_row``：按规范始终写 TMS 行号（``2^z-1-y``）。
    """

    @staticmethod
    def _custom_provider(is_tms, template="http://tiles.local/{z}/{x}/{y}.png"):
        from src.providers import ProviderManager

        provider = ProviderManager.create_custom_provider(
            f"tmstest_{is_tms}_{abs(hash(template)) % 10000}", template
        )
        provider.is_tms = is_tms
        return provider

    def test_tms_flag_flips_the_url_row_only(self):
        z, x, y = 3, 1, 2
        tms = self._custom_provider(True)
        xyz = self._custom_provider(False)
        self.assertTrue(tms.get_tile_url(x, y, z).endswith(f"/{z}/{x}/{2**z - 1 - y}.png"))
        self.assertTrue(xyz.get_tile_url(x, y, z).endswith(f"/{z}/{x}/{y}.png"))
        # {-y} 无论开关都表示翻转后的行号
        for flag in (True, False):
            provider = self._custom_provider(flag, "http://tiles.local/{z}/{x}/{-y}.png")
            self.assertTrue(
                provider.get_tile_url(x, y, z).endswith(f"/{z}/{x}/{2**z - 1 - y}.png")
            )

    def test_path_layout_is_xyz_regardless_of_tms(self):
        z, x, y = 3, 1, 2
        for flag in (True, False):
            provider = self._custom_provider(flag)
            path = provider.get_tile_path(x, y, z, "/tmp/tiles")
            self.assertEqual(path.name, f"{y}.png", f"is_tms={flag} 不应改变落盘布局")
            self.assertEqual(path.parent.name, str(x))
            self.assertEqual(path.parent.parent.name, str(z))

    def test_directory_download_with_tms_flag(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=1, is_tms=True, save_format="directory"
        )
        z, x, y = 3, 1, 2
        downloader.add_task(x, y, z)
        downloader.start()
        self.assertTrue(downloader.provider.is_tms)
        self.assertTrue(downloader.provider.get_tile_path(x, y, z, out).exists())

    def test_mbtiles_row_is_always_tms(self):
        for flag in (True, False):
            out = os.path.join(tempfile.mkdtemp(), "t.mbtiles")
            downloader = make_downloader(
                make_provider(), out, max_threads=1, is_tms=flag,
                save_format="mbtiles",
            )
            downloader.add_task(1, 2, 3)
            downloader.start()
            conn = sqlite3.connect(out)
            row = conn.execute("SELECT tile_row FROM tiles").fetchone()[0]
            conn.close()
            self.assertEqual(
                row, (2 ** 3) - 1 - 2,
                f"tile_row 必须按规范写 TMS 行号（is_tms={flag}）",
            )

    def test_has_tile_uses_xyz_task_coordinates(self):
        """断点续传按任务坐标（XYZ）查表，内部自己翻转，不能被 is_tms 影响。"""
        out = os.path.join(tempfile.mkdtemp(), "t.mbtiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=1, is_tms=True, save_format="mbtiles"
        )
        downloader.add_task(1, 2, 3)
        downloader.start()
        handler = downloader.mbtiles_handler
        self.assertTrue(handler.has_tile(3, 1, 2))
        self.assertFalse(handler.has_tile(3, 1, 5))


class DatabaseConfigTests(FakeNetworkMixin, unittest.TestCase):
    """回归：config.yaml 的 database 段以前是硬编码的，改了不生效。"""

    def test_mbtiles_connection_uses_config_pragmas(self):
        from src.config import config_manager

        out = os.path.join(tempfile.mkdtemp(), "t.mbtiles")
        with mock.patch.dict(
            config_manager.config["database"],
            {"busy_timeout": 12345, "cache_size": 4000},
        ):
            downloader = make_downloader(
                make_provider(), out, max_threads=1, save_format="mbtiles"
            )
        conn = downloader.mbtiles_handler.mbtiles_conn
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 12345)
        self.assertEqual(conn.execute("PRAGMA cache_size").fetchone()[0], 4000)
        self.assertEqual(
            str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(), "wal"
        )
        downloader.close()

    def test_progress_connection_uses_config_pragmas(self):
        from src.config import config_manager

        out = os.path.join(tempfile.mkdtemp(), "tiles")
        with mock.patch.dict(
            config_manager.config["database"], {"busy_timeout": 12345}
        ):
            downloader = make_downloader(
                make_provider(), out, max_threads=1, save_format="directory"
            )
            conn = downloader.progress_manager._get_connection()
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 12345)
        downloader.close()

    def test_invalid_pragma_values_fall_back_to_defaults(self):
        from src.config import config_manager

        out = os.path.join(tempfile.mkdtemp(), "t.mbtiles")
        with mock.patch.dict(
            config_manager.config["database"],
            {"journal_mode": "'; DROP TABLE tiles;--", "synchronous": "WEIRD"},
        ):
            downloader = make_downloader(
                make_provider(), out, max_threads=1, save_format="mbtiles"
            )
        conn = downloader.mbtiles_handler.mbtiles_conn
        self.assertEqual(
            str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(), "wal"
        )
        self.assertEqual(
            str(conn.execute("PRAGMA synchronous").fetchone()[0]), "1"  # NORMAL
        )
        downloader.close()


class MBTilesWriteFailureTests(FakeNetworkMixin, unittest.TestCase):
    """回归：MBTiles 写入失败曾被静默吞掉，最终仍报告全部成功。"""

    class _FailingConn:
        def executemany(self, *args, **kwargs):
            raise sqlite3.OperationalError("database or disk is full")

        def commit(self):
            pass

        def close(self):
            pass

    def test_write_failure_is_recorded_and_finalize_fails(self):
        out = os.path.join(tempfile.mkdtemp(), "t.mbtiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=1, save_format="mbtiles"
        )
        handler = downloader.mbtiles_handler
        handler.mbtiles_conn = self._FailingConn()

        downloader.add_task(1, 2, 5)
        downloader.start()

        self.assertGreaterEqual(handler.write_failures, 1, "写入失败未被记录")
        self.assertEqual(downloader.mbtiles_write_failures(), handler.write_failures)
        self.assertEqual(handler.mbtiles_write_queue.unfinished_tasks, 0)

    def test_locked_error_is_retried_without_recursion(self):
        out = os.path.join(tempfile.mkdtemp(), "t.mbtiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=1, save_format="mbtiles"
        )
        handler = downloader.mbtiles_handler
        calls = {"n": 0}

        class _FlakyConn:
            def executemany(self, *args, **kwargs):
                calls["n"] += 1
                if calls["n"] < 2:
                    raise sqlite3.OperationalError("database is locked")

            def commit(self):
                pass

            def close(self):
                pass

        handler.mbtiles_conn = _FlakyConn()
        self.assertTrue(handler._write_batch(5, [(1, 2, b"x", 2)]))
        self.assertEqual(calls["n"], 2, "锁定应重试且不递归")


class MBTilesAckTests(FakeNetworkMixin, unittest.TestCase):
    """ack：只有写线程 commit 成功才标记 success，写失败必须标 failed。"""

    class _FailingConn:
        def executemany(self, *args, **kwargs):
            raise sqlite3.OperationalError("database or disk is full")

        def commit(self):
            pass

        def close(self):
            pass

    @staticmethod
    def _progress_status(progress_db):
        conn = sqlite3.connect(progress_db)
        try:
            return {
                (x, y, z): status
                for x, y, z, status in conn.execute(
                    "SELECT x, y, z, status FROM processed_tiles"
                )
            }
        finally:
            conn.close()

    def test_success_is_written_after_commit(self):
        tmp = tempfile.mkdtemp()
        out = os.path.join(tmp, "t.mbtiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=2, save_format="mbtiles"
        )
        downloader.add_tasks([(1, 2, 5), (3, 4, 5)])
        downloader.start()

        status = self._progress_status(os.path.join(tmp, "t.progress.db"))
        self.assertEqual(status[(1, 2, 5)], "success")
        self.assertEqual(status[(3, 4, 5)], "success")
        self.assertEqual(downloader.downloaded_count, 2)
        conn = sqlite3.connect(out)
        rows = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        conn.close()
        self.assertEqual(rows, 2)

    def test_write_failure_marks_failed_instead_of_success(self):
        tmp = tempfile.mkdtemp()
        out = os.path.join(tmp, "t.mbtiles")
        downloader = make_downloader(
            make_provider(), out, max_threads=1, save_format="mbtiles"
        )
        downloader.mbtiles_handler.mbtiles_conn = self._FailingConn()
        downloader.add_task(1, 2, 5)
        downloader.start()

        status = self._progress_status(os.path.join(tmp, "t.progress.db"))
        self.assertEqual(status[(1, 2, 5)], "failed", "写失败不能谎报成功")
        self.assertEqual(downloader.downloaded_count, 0)
        self.assertEqual(downloader.failed_count, 1)

    def test_ack_disabled_keeps_legacy_enqueue_semantics(self):
        from src.config import config_manager

        tmp = tempfile.mkdtemp()
        out = os.path.join(tmp, "t.mbtiles")
        with mock.patch.dict(config_manager.config["download"], {"mbtiles_ack": False}):
            downloader = make_downloader(
                make_provider(), out, max_threads=1, save_format="mbtiles"
            )
        self.assertFalse(downloader.mbtiles_handler.ack_enabled)
        downloader.add_task(1, 2, 5)
        downloader.start()

        status = self._progress_status(os.path.join(tmp, "t.progress.db"))
        self.assertEqual(status[(1, 2, 5)], "success")


class OutputLockTests(FakeNetworkMixin, unittest.TestCase):
    """输出目录锁：同一路径不允许两个下载器同时写。"""

    def test_second_lock_on_same_dir_is_rejected(self):
        from src.downloader.output_lock import OutputLock

        directory = tempfile.mkdtemp()
        first = OutputLock(directory)
        second = OutputLock(directory)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire(), "同一目录的第二个锁不应成功")
        first.release()
        self.assertTrue(second.acquire(), "释放后应能重新获取")
        second.release()

    def test_downloader_raises_when_output_is_locked(self):
        from src.downloader.output_lock import OutputLock
        from src.exceptions import OutputLockedError

        out = os.path.join(tempfile.mkdtemp(), "tiles")
        holder = OutputLock(out)
        self.assertTrue(holder.acquire())
        try:
            with self.assertRaises(OutputLockedError):
                make_downloader(make_provider(), out, max_threads=1)
        finally:
            holder.release()

        # 锁释放后应能正常构造并收尾
        downloader = make_downloader(make_provider(), out, max_threads=1)
        downloader.close()

    def test_lock_is_released_after_start(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        first = make_downloader(make_provider(), out, max_threads=2)
        first.add_tasks([(1, 1, 5)])
        first.start()  # _cleanup() 会释放锁

        second = make_downloader(make_provider(), out, max_threads=1)
        self.assertIsNotNone(second.output_lock)
        second.close()


class FailedRetryTests(FakeNetworkMixin, unittest.TestCase):
    """失败瓦片：可见、可只补失败、有清单。"""

    def test_add_failed_tasks_only_enqueues_failures(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        _seed_progress_db(out, [(1, 1, 5, "failed"), (2, 2, 5, "failed")], status="failed")
        _seed_progress_db(out, [(3, 3, 5, "success")], status="success")

        downloader = make_downloader(
            make_provider(), out, max_threads=2, save_format="directory"
        )
        count = downloader.add_failed_tasks()
        self.assertEqual(count, 2)
        self.assertEqual(downloader.total_tasks, 2)

        downloader.start()
        stats = downloader.get_statistics()
        self.assertEqual(stats["downloaded"], 2)
        self.assertEqual(stats["failed"], 0)
        # 补下后进度库里应变成 success
        conn = sqlite3.connect(os.path.join(out, "progress.db"))
        statuses = dict(
            ((x, y, z), s)
            for x, y, z, s in conn.execute("SELECT x, y, z, status FROM processed_tiles")
        )
        conn.close()
        self.assertEqual(statuses[(1, 1, 5)], "success")
        self.assertEqual(statuses[(2, 2, 5)], "success")

    def test_add_failed_tasks_without_failures(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        _seed_progress_db(out, [(1, 1, 5, "success")], status="success")
        downloader = make_downloader(
            make_provider(), out, max_threads=1, save_format="directory"
        )
        downloader.verify_artifacts = False
        self.assertEqual(downloader.add_failed_tasks(), 0)
        self.assertEqual(downloader.total_tasks, 0)
        downloader.close()

    def test_list_failed_tiles_reports_total_and_rows(self):
        from src.downloader.progress_handler import list_failed_tiles

        out = os.path.join(tempfile.mkdtemp(), "tiles")
        _seed_progress_db(
            out,
            [(1, 1, 5, "failed"), (2, 2, 6, "failed"), (3, 3, 7, "success")],
            status="failed",
        )
        total, tiles = list_failed_tiles(out, is_mbtiles=False, limit=1)
        self.assertEqual(total, 2)
        self.assertEqual(len(tiles), 1, "limit 应限制返回条数（内存有界）")
        self.assertIn("x", tiles[0])

    def test_failed_manifest_is_written(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        provider = make_provider()
        downloader = make_downloader(
            provider, out, max_threads=2, retries=1, save_format="directory"
        )
        session = _ScriptedSession(
            lambda url, n: _ScriptedResponse(
                200, b"<!DOCTYPE html><html>bad</html>", {"Content-Type": "image/png"}
            )
        )
        with mock.patch.object(
            RequestSessionManager, "get_session", lambda mgr: session
        ):
            downloader.add_tasks([(1, 1, 5), (2, 2, 5)])
            downloader.start()

        self.assertEqual(downloader.failed_count, 2)
        manifest = downloader.failed_manifest_path()
        self.assertTrue(manifest.exists(), f"失败清单未生成: {manifest}")
        lines = [
            line.strip()
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertEqual(sorted(lines), ["5 1 1", "5 2 2"])


class DedupTests(FakeNetworkMixin, unittest.TestCase):
    """同一坐标在"已入队未处理"期间只下载一次。"""

    def test_add_task_ignores_duplicates(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        downloader = make_downloader(make_provider(), out, max_threads=1)
        downloader.add_task(1, 1, 5)
        downloader.add_task(1, 1, 5)
        downloader.add_task(2, 2, 5)
        self.assertEqual(downloader.total_tasks, 2)
        downloader.start()
        stats = downloader.get_statistics()
        self.assertEqual(stats["downloaded"], 2)
        self.assertEqual(stats["remaining"], 0)
        self.assertEqual(len(downloader._pending_tiles), 0, "去重集合应已清空")

    def test_add_tasks_dedupes_within_call_and_across_calls(self):
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        downloader = make_downloader(make_provider(), out, max_threads=2)
        downloader.add_tasks([(1, 1, 5), (2, 2, 5), (1, 1, 5)])
        self.assertEqual(downloader.total_tasks, 2, "同一次调用内的重复应被去掉")
        downloader.add_tasks([(1, 1, 5), (2, 2, 5)])
        self.assertEqual(downloader.total_tasks, 2, "跨调用的重复不应重复计数")
        downloader.start()
        stats = downloader.get_statistics()
        self.assertEqual(stats["downloaded"], 2)
        self.assertEqual(stats["remaining"], 0)

    def test_streaming_path_dedupes_and_accounts(self):
        from src.config import config_manager

        old = config_manager.config["download"].get("task_queue_size")
        config_manager.config["download"]["task_queue_size"] = 4
        self.addCleanup(
            config_manager.config["download"].__setitem__, "task_queue_size", old
        )
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        downloader = make_downloader(make_provider(), out, max_threads=2)
        tiles = [(1, 1, 5), (2, 2, 5), (3, 3, 5), (4, 4, 5)]
        downloader.add_tasks(tiles)   # 直接把队列填满
        downloader.add_tasks(tiles)   # 剩余容量 0 → 走流式生产，全部被判为重复

        downloader.start()
        stats = downloader.get_statistics()
        self.assertEqual(stats["total"], 8)
        self.assertEqual(stats["downloaded"], 4)
        self.assertEqual(stats["skipped"], 4, "被去重的那批要计入 skipped 才能收敛")
        self.assertEqual(stats["remaining"], 0)
        self.assertEqual(len(downloader._pending_tiles), 0)


class FailedTilesApiTests(FakeNetworkMixin, unittest.TestCase):
    def test_failed_tiles_endpoint(self):
        from app import app

        client = app.test_client()
        out = os.path.join(tempfile.mkdtemp(), "tiles")
        payload = dict(
            provider_url="http://tiles.local/{z}/{x}/{y}.png",
            output_dir=out,
            threads=2,
            tms=False,
            min_zoom=10,
            max_zoom=10,
            **BBOX,
        )
        session = _ScriptedSession(
            lambda url, n: _ScriptedResponse(
                200, b"<!DOCTYPE html><html>bad</html>", {"Content-Type": "image/png"}
            )
        )
        with mock.patch.object(
            RequestSessionManager, "get_session", lambda mgr: session
        ):
            resp = client.post("/api/download", json=payload)
            self.assertEqual(resp.status_code, 200)
            for _ in range(200):
                if not client.get("/api/download-status").get_json().get("is_downloading"):
                    break
                time.sleep(0.05)

        body = client.get("/api/failed-tiles").get_json()
        self.assertTrue(body["success"])
        self.assertGreaterEqual(body["total"], 1)
        self.assertEqual(body["count"], len(body["tiles"]))
        self.assertTrue(body["tiles"][0]["z"] == 10)


class ProgressPathTests(unittest.TestCase):
    """progress_generator 必须写到下载器真正读取的路径。"""

    def test_generator_path_matches_downloader(self):
        from pathlib import Path

        from src.downloader.progress_handler import resolve_progress_db_path
        from src.progress_generator import _resolve_progress_db_path

        for target, is_mbtiles in (("/tmp/a/tiles", False), ("/tmp/a/t.mbtiles", True)):
            self.assertEqual(
                _resolve_progress_db_path(Path(target), is_mbtiles),
                resolve_progress_db_path(target, is_mbtiles),
            )


class SseBroadcastTests(unittest.TestCase):
    """回归：单个 queue.Queue 会被多个 SSE 订阅者瓜分事件。"""

    def test_all_subscribers_receive_same_snapshot(self):
        from src.downloader.controller import DownloadController

        controller = DownloadController()
        controller._reset_progress()
        controller._emit_progress(5, 10)

        stream1 = controller.progress_stream()
        stream2 = controller.progress_stream()
        try:
            event1 = next(stream1)
            event2 = next(stream2)
        finally:
            stream1.close()
            stream2.close()

        self.assertIn('"downloaded": 5', event1)
        self.assertEqual(event1, event2, "两个订阅者应收到同一份快照")


if __name__ == "__main__":
    unittest.main()

"""回归测试：覆盖曾出现的严重缺陷以及核心下载路径。

运行方式（在项目根目录）::

    python -m unittest discover -s tests -v
"""

import os
import sqlite3
import tempfile
import threading
import time
import unittest

from src.tile_math import TileMath
from tests._helpers import FakeNetworkMixin, make_downloader, make_provider

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


def _seed_progress_db(progress_dir, rows):
    """在目录模式下预置一个已存在的进度库（模拟历史下载记录）。"""
    os.makedirs(progress_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(progress_dir, "progress.db"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS processed_tiles ("
        "x INTEGER, y INTEGER, z INTEGER, status TEXT, PRIMARY KEY (x, y, z))"
    )
    conn.executemany(
        "INSERT OR REPLACE INTO processed_tiles (x, y, z, status) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


class ResumeStreamingTests(FakeNetworkMixin, unittest.TestCase):
    """断点续传的内存/规模解耦：流式归并 + 分页查询。"""

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


if __name__ == "__main__":
    unittest.main()

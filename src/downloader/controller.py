# src/downloader/controller.py

"""下载会话控制器：把"当前下载任务 + 进度广播"从 Flask 路由中解耦出来。

路由层只负责解析 / 校验 HTTP 请求，其余状态全部收敛到 ``DownloadController``，
便于单测与后续替换 Web 框架。
"""

import json
import queue
import threading
import time

from loguru import logger

from ..providers import ProviderManager
from .base import TileDownloader


class DownloadController:
    """管理当前下载任务的生命周期与进度事件。"""

    def __init__(self, max_progress_events: int = 1000):
        self._lock = threading.RLock()
        self._downloader = None
        self._params = None
        self._thread = None
        # 进度是"状态"而非"事件流"，队列有界，满时丢弃最旧的一条即可
        self._progress_queue = queue.Queue(maxsize=max_progress_events)
        self._speed = {'last_downloaded': 0, 'last_bytes': 0, 'last_time': 0.0}

    # ------------------------------------------------------------------ #
    # 进度广播
    # ------------------------------------------------------------------ #
    def _push(self, payload: dict):
        try:
            self._progress_queue.put_nowait(payload)
        except queue.Full:
            try:
                self._progress_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._progress_queue.put_nowait(payload)
            except queue.Full:
                pass

    def _emit_progress(self, downloaded, total, total_bytes=0, completed=False, stats=None):
        """构造并推送一条进度事件（计算速度与预计剩余时间）。"""
        now = time.time()
        speed = 0.0
        if now > self._speed['last_time'] and downloaded > self._speed['last_downloaded']:
            time_diff = now - self._speed['last_time']
            bytes_diff = total_bytes - self._speed['last_bytes']
            speed = bytes_diff / (1024 * time_diff)
            self._speed.update(
                last_downloaded=downloaded, last_bytes=total_bytes, last_time=now
            )

        eta = "-"
        if speed > 0 and total > downloaded:
            avg_bytes = total_bytes / downloaded if downloaded > 0 else 0
            remaining_seconds = (total - downloaded) * avg_bytes / (speed * 1024)
            eta = self._format_eta(remaining_seconds)

        self._push({
            'downloaded': downloaded,
            'total': total,
            'total_bytes': total_bytes,
            'percentage': int(downloaded / total * 100) if total > 0 else 0,
            'speed': round(speed, 2),
            'eta': eta,
            'completed': completed,
            'stats': stats,
        })

    @staticmethod
    def _format_eta(seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}秒"
        if seconds < 3600:
            return f"{int(seconds // 60)}分{int(seconds % 60)}秒"
        return f"{int(seconds // 3600)}小时{int((seconds % 3600) // 60)}分"

    def progress_stream(self):
        """SSE 生成器：把进度事件推送给浏览器。"""
        def generator():
            while True:
                try:
                    data = self._progress_queue.get(timeout=0.5)
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    # 心跳，保持连接活跃
                    yield ": keep-alive\n\n"
                except GeneratorExit:
                    return
        return generator()

    # ------------------------------------------------------------------ #
    # 任务控制
    # ------------------------------------------------------------------ #
    def start(self, params: dict) -> dict:
        """校验参数、创建下载器并启动后台下载线程。"""
        normalized = self._normalize_params(params)

        # 先取消正在进行的任务
        self._cancel_current()

        if normalized['provider_url']:
            ProviderManager.create_custom_provider(
                name=normalized['provider_name'],
                url_template=normalized['provider_url'],
                subdomains=normalized['subdomains'],
                min_zoom=normalized['min_zoom'],
                max_zoom=normalized['max_zoom'],
            )

        scheme = 'tms' if normalized['tms'] else 'xyz'
        downloader = TileDownloader(
            normalized['provider_name'],
            normalized['output_dir'],
            max_threads=normalized['threads'],
            is_tms=normalized['tms'],
            progress_callback=self._emit_progress,
            tile_format=normalized['tile_format'],
            save_format=normalized['save_format'],
            scheme=scheme,
        )
        downloader.add_tasks_for_bbox(
            normalized['west'],
            normalized['south'],
            normalized['east'],
            normalized['north'],
            normalized['min_zoom'],
            normalized['max_zoom'],
        )

        with self._lock:
            self._downloader = downloader
            self._params = normalized
            self._speed = {'last_downloaded': 0, 'last_bytes': 0, 'last_time': time.time()}

        self._emit_progress(0, downloader.total_tasks)

        thread = threading.Thread(
            target=self._run, args=(downloader,), name="DownloadSession", daemon=True
        )
        with self._lock:
            self._thread = thread
        thread.start()

        logger.info(
            f"下载任务已启动: provider={normalized['provider_name']}, "
            f"bbox=[{normalized['west']},{normalized['south']},{normalized['east']},{normalized['north']}], "
            f"zoom={normalized['min_zoom']}-{normalized['max_zoom']}, tiles={downloader.total_tasks}"
        )
        return {'success': True, 'message': '下载任务已开始', 'total': downloader.total_tasks}

    def _run(self, downloader: TileDownloader):
        try:
            downloader.start()
        except Exception as e:  # noqa: BLE001
            logger.error(f"下载线程异常: {e}")
        finally:
            stats = downloader.get_statistics()
            self._emit_progress(
                stats['downloaded'], stats['total'], downloader.total_bytes,
                completed=True, stats=stats,
            )
            logger.info(f"下载结束统计: {stats}")
            with self._lock:
                if self._downloader is downloader:
                    self._downloader = None

    def pause(self) -> dict:
        with self._lock:
            downloader = self._downloader
        if not downloader:
            return {'success': False, 'message': '没有正在进行的下载任务'}
        downloader.pause()
        return {'success': True, 'message': '下载已暂停'}

    def resume(self) -> dict:
        with self._lock:
            downloader = self._downloader
        if not downloader:
            return {'success': False, 'message': '没有正在进行的下载任务'}
        downloader.resume()
        return {'success': True, 'message': '下载已恢复'}

    def cancel(self) -> dict:
        with self._lock:
            downloader = self._downloader
        if not downloader:
            return {'success': False, 'message': '没有正在进行的下载任务'}
        stats = downloader.cancel()
        with self._lock:
            if self._downloader is downloader:
                self._downloader = None
        return {'success': True, 'message': '下载已取消', 'stats': stats}

    def _cancel_current(self):
        with self._lock:
            downloader = self._downloader
            self._downloader = None
        if downloader:
            logger.info("发现正在进行的下载任务，先取消...")
            try:
                downloader.cancel()
            except Exception as e:  # noqa: BLE001
                logger.error(f"取消旧任务失败: {e}")

    def status(self) -> dict:
        with self._lock:
            downloader = self._downloader
        if not downloader:
            return {'is_downloading': False}
        return {
            'is_downloading': True,
            'is_paused': downloader.is_paused(),
            'stats': downloader.get_statistics(),
        }

    def params(self) -> dict:
        with self._lock:
            if not self._params:
                return {'success': False, 'error': '没有当前下载参数'}
            return {'success': True, 'params': self._params}

    # ------------------------------------------------------------------ #
    # 参数校验
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_params(data: dict) -> dict:
        def _require(key):
            value = data.get(key)
            if value is None or value == '':
                raise ValueError(f"缺少必需参数: {key}")
            return value

        subdomains = data.get('subdomains', [])
        if isinstance(subdomains, str):
            subdomains = [s.strip() for s in subdomains.split(',') if s.strip()]

        min_zoom = int(_require('min_zoom'))
        max_zoom = int(_require('max_zoom'))
        if min_zoom > max_zoom:
            raise ValueError("最小缩放级别不能大于最大缩放级别")

        west = float(_require('west'))
        east = float(_require('east'))
        south = float(_require('south'))
        north = float(_require('north'))
        if west >= east or south >= north:
            raise ValueError("边界坐标无效：需要 西<东 且 南<北")

        save_format = data.get('save_format', 'directory')
        if save_format not in ('directory', 'mbtiles'):
            raise ValueError(f"不支持的保存格式: {save_format}")

        return {
            'provider_url': data.get('provider_url'),
            'provider_name': data.get('provider_name', 'custom') or 'custom',
            'north': north,
            'south': south,
            'west': west,
            'east': east,
            'min_zoom': min_zoom,
            'max_zoom': max_zoom,
            'output_dir': _require('output_dir'),
            'threads': int(data.get('threads', 4) or 4),
            'tms': bool(data.get('tms', False)),
            'subdomains': subdomains,
            'tile_format': data.get('tile_format'),
            'save_format': save_format,
        }

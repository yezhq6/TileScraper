# src/downloader/controller.py

"""下载会话控制器：把"当前下载任务 + 进度广播"从 Flask 路由中解耦出来。

路由层只负责解析 / 校验 HTTP 请求，其余状态全部收敛到 ``DownloadController``，
便于单测与后续替换 Web 框架。
"""

import json
import math
import threading
import time

from loguru import logger

from ..providers import ProviderManager
from .base import TileDownloader

# 缩放级别的合理范围（超过这个范围没有实际瓦片服务）
MIN_ALLOWED_ZOOM = 0
MAX_ALLOWED_ZOOM = 24


def parse_threads_param(value):
    """
    解析下载线程数参数。

    * 缺省（None）——交给 ``download.threads`` 配置决定；
    * 空串 / 0 / "auto"——表示自动选择；
    * 其余必须是正整数。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"下载线程数无效: {value!r}（应为正整数或 auto）")
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ('', 'auto'):
            return 'auto'
        try:
            number = int(text)
        except ValueError:
            raise ValueError(f"下载线程数无效: {value!r}（应为正整数或 auto）") from None
    else:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"下载线程数无效: {value!r}（应为正整数或 auto）")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"下载线程数无效: {value!r}（应为正整数或 auto）") from None
    if number < 0:
        raise ValueError(f"下载线程数无效: {value!r}（应为正整数或 auto）")
    return number if number > 0 else 'auto'


class DownloadController:
    """管理当前下载任务的生命周期与进度事件。"""

    def __init__(self, max_progress_events: int = 1000):
        self._lock = threading.RLock()
        self._downloader = None
        self._params = None
        self._thread = None
        # 进度是"最新状态"而不是"事件流"：只保留最新一份并广播给所有订阅者。
        # 旧实现用单个 queue.Queue，多个标签页/SSE 重连会互相抢事件，
        # 新连上的浏览器还会收到上一次任务的陈旧事件。
        self._progress_cond = threading.Condition()
        self._latest_progress = None
        self._progress_revision = 0
        self._speed = {'last_downloaded': 0, 'last_bytes': 0, 'last_time': 0.0}

    # ------------------------------------------------------------------ #
    # 进度广播
    # ------------------------------------------------------------------ #
    def _push(self, payload: dict):
        with self._progress_cond:
            self._latest_progress = payload
            self._progress_revision += 1
            self._progress_cond.notify_all()

    def _reset_progress(self):
        """清空上一轮任务的进度快照（新任务开始时调用）。"""
        with self._progress_cond:
            self._latest_progress = None
            self._progress_revision += 1
            self._progress_cond.notify_all()

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
        """SSE 生成器：把最新进度广播给每一个订阅者。"""
        def generator():
            # 从 -1 开始：连上就先把当前快照推一次，UI 不必等下一次进度事件
            last_revision = -1
            while True:
                with self._progress_cond:
                    if self._progress_revision == last_revision:
                        self._progress_cond.wait(timeout=0.5)
                    if self._progress_revision == last_revision:
                        data = None
                    else:
                        last_revision = self._progress_revision
                        data = self._latest_progress
                if data is None:
                    # 心跳，保持连接活跃
                    yield ": keep-alive\n\n"
                else:
                    yield f"data: {json.dumps(data)}\n\n"
        return generator()

    # ------------------------------------------------------------------ #
    # 任务控制
    # ------------------------------------------------------------------ #
    def start(self, params: dict) -> dict:
        """校验参数、创建下载器并启动后台下载线程。"""
        normalized = self._normalize_params(params)

        # 先取消正在进行的任务，并等它真正收尾（否则两个下载器会同时操作
        # 同一个 progress.db / .mbtiles，写线程互相抢锁甚至丢数据）
        self._cancel_current()

        provider = None
        if normalized['provider_url']:
            # 直接拿实例交给下载器（而不是先注册再按名字取），
            # 避免并发的两个请求互相覆盖全局注册表里的同名 provider
            provider = ProviderManager.create_custom_provider(
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
            provider=provider,
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
        # 清掉上一轮进度快照，避免新连上的浏览器看到旧任务的 completed 事件
        self._reset_progress()

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
            logger.info(f"下载结束统计: {stats}")
            with self._lock:
                is_current = self._downloader is downloader
                if is_current:
                    self._downloader = None
            # 只有仍是"当前任务"时才广播完成事件：
            # 已被新任务取代的旧会话若发出 completed=True，前端会误以为新任务已完成
            if is_current:
                self._emit_progress(
                    stats['downloaded'], stats['total'], downloader.total_bytes,
                    completed=True, stats=stats,
                )

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

    def stop(self, timeout: float = 0.0) -> dict:
        """
        请求优雅停止当前下载（进程关停时用）。

        与 :meth:`cancel` 不同：这里不丢弃状态、不提前清理资源，而是等下载
        线程走完 ``start()`` 的正常收尾（保存进度、提交 MBTiles）。

        Args:
            timeout: 等待下载线程结束的最长秒数；0 表示不等待。
        """
        with self._lock:
            downloader = self._downloader
            thread = self._thread
        if not downloader:
            return {'success': False, 'message': '没有正在进行的下载任务'}
        downloader.stop()
        if thread is not None and thread.is_alive() and timeout:
            thread.join(timeout)
        return {'success': True, 'message': '已请求停止下载'}

    def _cancel_current(self, timeout: float = 60.0):
        """
        取消正在进行的任务，并等待它真正收尾。

        不等的话，两个下载器会同时操作同一个 ``progress.db`` / ``.mbtiles``：
        新下载器的 ``_init_mbtiles`` 可能因为旧写线程持锁而失败，两个写线程
        同时插入也很容易触发 "database is locked"。
        """
        with self._lock:
            downloader = self._downloader
            thread = self._thread
            self._downloader = None
        if not downloader:
            return
        logger.info("发现正在进行的下载任务，先取消...")
        try:
            downloader.cancel()
        except Exception as e:  # noqa: BLE001
            logger.error(f"取消旧任务失败: {e}")
        if thread is not None and thread.is_alive():
            thread.join(timeout)
            if thread.is_alive():
                logger.error(
                    "上一个下载任务未能在超时内退出；新任务可能与它共用输出文件"
                )

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

    def failed_tiles(self, limit: int = 1000) -> dict:
        """
        读取当前（或最近一次）任务输出目录里的失败瓦片清单。

        只使用控制器自己记录的 ``output_dir``，**不接受外部传路径**，
        避免把任意文件当作进度库读取。
        """
        from .progress_handler import list_failed_tiles

        with self._lock:
            params = self._params
            downloader = self._downloader
        if not params:
            return {'success': False, 'error': '没有当前下载参数'}
        output_dir = params['output_dir']
        is_mbtiles = params.get('save_format') == 'mbtiles'
        total, tiles = list_failed_tiles(output_dir, is_mbtiles=is_mbtiles, limit=limit)
        manifest = None
        if downloader is not None:
            manifest = str(downloader.failed_manifest_path())
        return {
            'success': True,
            'output_dir': output_dir,
            'total': total,
            'count': len(tiles),
            'manifest': manifest,
            'tiles': tiles,
        }

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

        def _as_int(key, value):
            try:
                number = int(value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"参数 {key} 不是有效整数: {value!r}") from None
            return number

        def _as_float(key, value):
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"参数 {key} 不是有效数字: {value!r}") from None
            if not math.isfinite(number):
                raise ValueError(f"参数 {key} 不是有限数字: {value!r}")
            return number

        # 子域名：既可能是 "t0,t1,t2" 字符串，也可能是前端 split(',') 后的数组；
        # 两种形式都要 trim / 去空 / 去重（否则会拼出 "ecn. t1..." 或 "ecn..tiles..."，
        # 导致大部分瓦片直接连接失败）
        subdomains = data.get('subdomains', [])
        if isinstance(subdomains, str):
            subdomains = subdomains.split(',')
        if subdomains is None:
            subdomains = []
        if not isinstance(subdomains, (list, tuple)):
            raise ValueError("subdomains 必须是字符串或字符串数组")
        cleaned = []
        for item in subdomains:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        subdomains = cleaned

        provider_url = (data.get('provider_url') or '').strip() or None
        if provider_url:
            if '{s}' in provider_url and not subdomains:
                raise ValueError(
                    "URL 模板里含 {s} 但子域名列表为空：请填写子域名（例如 t0,t1,t2,t3），"
                    "或把模板里的 {s} 换成固定子域"
                )
            if not any(p in provider_url for p in ('{z}', '{x}', '{y}', '{-y}', '{q}')):
                raise ValueError(
                    "URL 模板缺少占位符：至少要包含 {z}/{x}/{y}（Bing 用 {q}）"
                )

        min_zoom = _as_int('min_zoom', _require('min_zoom'))
        max_zoom = _as_int('max_zoom', _require('max_zoom'))
        if min_zoom > max_zoom:
            raise ValueError("最小缩放级别不能大于最大缩放级别")
        if not (MIN_ALLOWED_ZOOM <= min_zoom and max_zoom <= MAX_ALLOWED_ZOOM):
            raise ValueError(
                f"缩放级别超出范围 {MIN_ALLOWED_ZOOM}-{MAX_ALLOWED_ZOOM}: "
                f"{min_zoom}-{max_zoom}"
            )

        west = _as_float('west', _require('west'))
        east = _as_float('east', _require('east'))
        south = _as_float('south', _require('south'))
        north = _as_float('north', _require('north'))
        if west >= east or south >= north:
            raise ValueError("边界坐标无效：需要 西<东 且 南<北")
        if not (-180 <= west <= 180 and -180 <= east <= 180):
            raise ValueError("经度必须在 -180 ~ 180 之间")
        if not (-90 <= south <= 90 and -90 <= north <= 90):
            raise ValueError("纬度必须在 -90 ~ 90 之间")

        save_format = data.get('save_format', 'directory')
        if save_format not in ('directory', 'mbtiles'):
            raise ValueError(f"不支持的保存格式: {save_format}")

        threads = parse_threads_param(data.get('threads', None))

        # 代理是"环境/部署"级别的设置，只从 config.yaml（或环境变量）读取，
        # 不接受请求级覆盖：否则任何能访问 HTTP 端口的人都能让服务端
        # 通过一个攻击者指定的代理去下载（中间人/凭据泄漏/SSRF 跳板）。
        requested_proxy = data.get('proxy')
        if requested_proxy:
            logger.warning(
                "忽略请求里的 proxy 字段；代理请在 config.yaml 的 download.proxy "
                "或环境变量 TILESCRAPER_DOWNLOAD_PROXY 中配置"
            )

        return {
            'provider_url': provider_url,
            'provider_name': data.get('provider_name', 'custom') or 'custom',
            'north': north,
            'south': south,
            'west': west,
            'east': east,
            'min_zoom': min_zoom,
            'max_zoom': max_zoom,
            'output_dir': _require('output_dir'),
            'threads': threads,
            'tms': bool(data.get('tms', False)),
            'subdomains': subdomains,
            'tile_format': data.get('tile_format'),
            'save_format': save_format,
        }

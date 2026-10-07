# src/downloader/worker.py

import time
import threading
from queue import Empty, Full
from loguru import logger
import requests

from .request import RequestSessionManager
from .utils import ensure_directory


class WorkerManager:
    """
    工作线程管理器：负责创建、调度和回收下载工作线程。

    设计要点：
    * 线程只在 stop_event 置位、或"任务生产完毕且队列已排空"时退出；
    * 暂停/停止通过"把任务放回队列"实现，绝不使用 return 结束线程，
      避免暂停后线程数不断减少。
    """

    def __init__(self, downloader):
        self.downloader = downloader
        self.worker_threads = []

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start_workers(self):
        """启动工作线程。"""
        import os

        cpu_cores = os.cpu_count() or 4
        actual_threads = max(1, min(self.downloader.max_threads, cpu_cores * 8, 128))
        logger.info(f"实际使用线程数: {actual_threads}, CPU核心数: {cpu_cores}")

        self.worker_threads = []
        for i in range(actual_threads):
            t = threading.Thread(target=self._worker, name=f"Downloader-{i+1}", daemon=True)
            self.worker_threads.append(t)
            t.start()
        logger.info(f"已启动 {actual_threads} 个下载线程")

    def wait_for_completion(self):
        """等待所有工作线程结束。"""
        for t in self.worker_threads:
            t.join()

    # ------------------------------------------------------------------ #
    # 工作线程主体
    # ------------------------------------------------------------------ #
    def _worker(self):
        d = self.downloader
        name = threading.current_thread().name
        session_manager = RequestSessionManager()
        session_manager.get_session()

        processed = 0
        failed = 0
        started = time.time()
        logger.debug(f"{name} 启动")

        try:
            while not d.stop_event.is_set():
                # 暂停时阻塞等待；若暂停期间任务已全部完成则结束线程
                if not d.pause_event.is_set():
                    if self._wait_for_resume():
                        break
                    continue

                try:
                    task = d.task_queue.get(timeout=0.2)
                except Empty:
                    if d.all_tasks_added.is_set():
                        break
                    continue

                try:
                    outcome = self._handle_task(task, session_manager)
                except Exception as e:  # noqa: BLE001 - 单个任务异常不应拖垮整个线程
                    logger.error(f"{name} - 任务处理异常: {e}")
                    import traceback
                    traceback.print_exc()
                    try:
                        x, y, z = task
                        d._mark_tile_processed(x, y, z, 'failed')
                        d._update_progress()
                    except Exception:
                        pass
                    outcome = 'failed'
                if outcome == 'success':
                    processed += 1
                elif outcome == 'failed':
                    failed += 1
        except Exception as e:  # noqa: BLE001 - 线程内兜底，避免静默退出
            logger.error(f"{name} - 线程异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            try:
                session_manager.close()
            except Exception:
                pass
            logger.info(
                f"{name} 结束 - 运行 {time.time() - started:.2f}s，成功 {processed}，失败 {failed}"
            )

    def _wait_for_resume(self) -> bool:
        """暂停中阻塞等待；返回 True 表示当前线程应当结束。"""
        d = self.downloader
        while not d.pause_event.is_set():
            if d.stop_event.is_set():
                return True
            # 暂停时如果任务已全部处理完，则无需继续占用线程
            if d.all_tasks_added.is_set() and d.task_queue.empty() and d.pending_mbtiles_writes() == 0:
                return True
            d.pause_event.wait(0.2)
        return False

    def _handle_task(self, task, session_manager):
        """
        处理单个任务。

        Returns:
            'success' 处理成功（含跳过）、'failed' 失败、None 被重新入队或中断。
        """
        d = self.downloader
        x, y, z = task
        try:
            # 取出任务后若已被暂停，放回队列
            if not d.pause_event.is_set():
                d.task_queue.put(task)
                return None

            # zoom 越界直接跳过
            if not (d.provider.min_zoom <= z <= d.provider.max_zoom):
                logger.debug(f"[跳过] z={z} 超出 {d.provider.min_zoom}-{d.provider.max_zoom}")
                d._mark_tile_processed(x, y, z, 'skipped')
                d._update_progress()
                return 'success'

            # 目录模式下先检查是否已存在
            file_path = None
            if not d.is_mbtiles:
                file_path = d.provider.get_tile_path(x, y, z, d.output_dir)
                if file_path.exists():
                    d._mark_tile_processed(x, y, z, 'skipped')
                    d._update_progress()
                    return 'success'
                ensure_directory(file_path.parent)

            url = d.provider.get_tile_url(x, y, z)
            permanent_error = False

            for attempt in range(d.retries):
                if d.stop_event.is_set():
                    return None
                if not d.pause_event.is_set():
                    d.task_queue.put(task)
                    return None

                try:
                    response = session_manager.get_session().get(
                        url, stream=True, timeout=5, allow_redirects=True
                    )
                except requests.exceptions.RequestException as e:
                    logger.warning(f"[重试 {attempt + 1}/{d.retries}] 请求失败: {url} - {e}")
                    try:
                        session_manager.create_session()
                    except Exception:
                        pass
                    action = self._interruptible_sleep(d.delay * (2 ** attempt))
                    if self._requeue_if_needed(action, task):
                        return None
                    continue

                try:
                    status = response.status_code
                    if status != 200:
                        logger.warning(f"[HTTP {status}] {url}")
                        if status in (403, 404, 410):
                            permanent_error = True
                            break
                        action = self._interruptible_sleep(d.delay * (2 ** attempt))
                        if self._requeue_if_needed(action, task):
                            return None
                        continue

                    ctype = (response.headers.get('Content-Type') or '').lower()
                    if ctype and not any(
                        t in ctype for t in ('image', 'jpeg', 'jpg', 'png', 'octet-stream')
                    ):
                        logger.warning(f"非图片响应({ctype}): {url}")
                        permanent_error = True
                        break

                    action, data = self._read_body(response)
                    if action == 'stop':
                        return None
                    if action == 'pause':
                        d.task_queue.put(task)
                        return None

                    if not data:
                        logger.warning(f"下载数据为空: {url}")
                        action = self._interruptible_sleep(d.delay * (2 ** attempt))
                        if self._requeue_if_needed(action, task):
                            return None
                        continue
                finally:
                    try:
                        response.close()
                    except Exception:
                        pass

                if self._persist_tile(x, y, z, data, file_path):
                    d._add_bytes(len(data))
                    d._mark_tile_processed(x, y, z, 'success')
                    d._update_progress()
                    logger.debug(f"下载成功: z={z} x={x} y={y} ({len(data)} 字节)")
                    return 'success'

                logger.warning(f"保存失败: z={z} x={x} y={y}")

            # 重试结束
            if not permanent_error and not d.stop_event.is_set():
                logger.error(f"下载失败(已重试 {d.retries} 次): {url}")
            d._mark_tile_processed(x, y, z, 'failed')
            d._update_progress()
            return 'failed'
        finally:
            # 每个从队列取出的任务恰好调用一次 task_done，
            # 若中途重新入队则 put(+1) 与 task_done(-1) 相互抵消。
            d.task_queue.task_done()

    # ------------------------------------------------------------------ #
    # 辅助方法
    # ------------------------------------------------------------------ #
    def _requeue_if_needed(self, action: str, task) -> bool:
        """根据中断动作决定是否把任务放回队列并结束本次处理。"""
        if action == 'stop':
            return True
        if action == 'pause':
            self.downloader.task_queue.put(task)
            return True
        return False

    def _read_body(self, response):
        """分块读取响应体，过程中可响应暂停/停止。"""
        d = self.downloader
        chunks = []
        for chunk in response.iter_content(chunk_size=8192):
            if d.stop_event.is_set():
                return 'stop', None
            if not d.pause_event.is_set():
                return 'pause', None
            if chunk:
                chunks.append(chunk)
        return 'ok', b''.join(chunks)

    def _interruptible_sleep(self, seconds: float) -> str:
        """可被暂停/停止打断的休眠，返回 'ok' | 'pause' | 'stop'。"""
        d = self.downloader
        deadline = time.time() + min(seconds, 5)
        while time.time() < deadline:
            if d.stop_event.is_set():
                return 'stop'
            if not d.pause_event.is_set():
                return 'pause'
            time.sleep(0.1)
        return 'ok'

    def _persist_tile(self, x: int, y: int, z: int, data: bytes, file_path) -> bool:
        """把下载到的瓦片落盘（文件系统或 MBTiles 写入队列）。"""
        d = self.downloader
        if d.is_mbtiles:
            # MBTiles 的 tile_row 自底部计数，需要把 XYZ 的 y 翻转
            mbtiles_row = (2 ** z) - 1 - y
            return d._enqueue_mbtiles((z, x, mbtiles_row, data))

        try:
            with open(file_path, "wb") as f:
                f.write(data)
            return True
        except IOError as e:
            logger.error(f"文件写入错误 {file_path}: {e}")
            try:
                ensure_directory(file_path.parent)
                with open(file_path, "wb") as f:
                    f.write(data)
                return True
            except Exception as retry_error:
                logger.error(f"重试写入失败: {retry_error}")
                return False

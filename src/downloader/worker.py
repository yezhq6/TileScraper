# src/downloader/worker.py

import os
import tempfile
import time
import threading
from pathlib import Path
from queue import Empty
from loguru import logger
import requests

from .rate_limiter import parse_retry_after
from .request import RequestSessionManager
from .utils import ensure_directory, is_auto_thread_count, resolve_thread_count


def looks_like_image(data: bytes) -> bool:
    """通过文件头判断数据是否为常见位图格式（PNG/JPEG/GIF/BMP/WEBP）。"""
    if not data or len(data) < 12:
        return False
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return True
    if data.startswith(b'\xff\xd8\xff'):
        return True
    if data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
        return True
    if data.startswith(b'BM'):
        return True
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return True
    return False


def is_blank_tile(data: bytes) -> bool:
    """判断图片是否为纯色（空白）瓦片；需要 Pillow，缺失或解码失败时返回 False。"""
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(BytesIO(data)) as image:
            gray = image.convert('L')
            low, high = gray.getextrema()
            return low == high
    except Exception:  # noqa: BLE001 - 无法解码时不做判断
        return False


class WorkerManager:
    """
    工作线程管理器：负责创建、调度和回收下载工作线程。

    设计要点：
    * 线程只在 stop_event 置位、或"任务生产完毕且队列已排空"时退出；
    * 暂停时由当前线程**原地持有**正在处理的任务并等待恢复，不把任务塞回队列。
      这一点很关键：任务队列是有界的，暂停时生产者可能已经把队列填满，
      如果此时多个线程都去 ``task_queue.put()`` 回队就会互相等待（队列满 +
      没有消费者）造成永久死锁。原地持有任务彻底避开了这条路径。
    * 暂停在"瓦片边界"生效：一个瓦片会读到完整并落盘后再暂停，
      因此既不会产生半截数据，也不会重复请求已经在途的瓦片。
    * 绝不使用 return 提前结束线程，避免暂停后线程数不断减少。
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
        requested = self.downloader.max_threads
        actual_threads = resolve_thread_count(requested)
        if is_auto_thread_count(requested):
            logger.info(f"线程数自动选择: {actual_threads}（CPU核心数 {cpu_cores}）")
        elif actual_threads != requested:
            logger.warning(
                f"请求线程数 {requested} 超过硬上限，已截断为 {actual_threads}"
                f"（download.max_threads_hard_limit）"
            )
        else:
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
        session_manager = RequestSessionManager(proxy=d.proxy)
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

                task_started = time.time()
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
                if d.performance_monitor:
                    d.performance_monitor.record_task_processing(time.time() - task_started)
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
            'success' 处理成功（含跳过）、'failed' 失败、None 被中断。
        """
        d = self.downloader
        try:
            x, y, z = task
            # 取出任务后若已被暂停：原地等待恢复，任务由本线程继续持有
            if not self._wait_until_resumed():
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
            elif (
                d.enable_resume
                and d.verify_artifacts
                and d.mbtiles_handler is not None
                and d.mbtiles_handler.has_tile(z, x, y)
            ):
                # MBTiles 模式：tiles 表就是权威产物记录。
                # 崩溃/强杀后 progress.db 可能落后于已提交的行，这里按表跳过，
                # 避免把已经落库的瓦片再请求一遍（目录模式等价于上面的 exists 检查）。
                d._mark_tile_processed(x, y, z, 'skipped')
                d._update_progress()
                return 'success'

            url = d.provider.get_tile_url(x, y, z)
            permanent_error = False

            for attempt in range(d.retries):
                if d.stop_event.is_set():
                    return None
                # 每个瓦片开始前检查暂停（瓦片边界暂停，不打断在途请求）
                if not self._wait_until_resumed():
                    return None
                # 全局限流冷却：被 429/503 后所有线程一起等
                if d.limiter is not None and not d.limiter.wait_for_cooldown(d.stop_event):
                    return None
                # 并发闸门：限流时会被动态收缩。
                # 槽位覆盖"请求 + 读取响应体"的完整过程（否则限流收缩后，
                # 仍在传输的响应体不受约束，实际并发会超过上限）。
                if d.limiter is not None and not d.limiter.acquire(d.stop_event):
                    return None

                try:
                    request_started = time.time()
                    try:
                        response = session_manager.get_session().get(
                            url, stream=True, timeout=d.timeout, allow_redirects=True
                        )
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"[重试 {attempt + 1}/{d.retries}] 请求失败: {url} - {e}")
                        session_manager.recreate()
                        if self._interruptible_sleep(d.delay * (2 ** attempt)) == 'stop':
                            return None
                        continue

                    try:
                        status = response.status_code
                        if status != 200:
                            logger.warning(f"[HTTP {status}] {url}")
                            if status in (403, 404, 410):
                                permanent_error = True
                                break

                            # 429/503：解析 Retry-After，并让所有线程一起冷却 + 降并发
                            if status in (429, 503) and d.limiter is not None:
                                retry_after = parse_retry_after(
                                    response.headers.get('Retry-After')
                                )
                                cooldown = d.limiter.note_throttled(retry_after)
                                logger.warning(
                                    f"服务端限流，全局冷却 {cooldown:.1f}s，"
                                    f"当前并发上限 {d.limiter.limit}"
                                )
                                sleep_for = max(d.delay * (2 ** attempt), min(cooldown, 5.0))
                                if self._interruptible_sleep(sleep_for) == 'stop':
                                    return None
                                continue

                            if self._interruptible_sleep(d.delay * (2 ** attempt)) == 'stop':
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

                        if not data:
                            logger.warning(f"下载数据为空: {url}")
                            if self._interruptible_sleep(d.delay * (2 ** attempt)) == 'stop':
                                return None
                            continue

                        ok, reason = self._validate_tile_data(data)
                        if not ok:
                            logger.warning(f"瓦片内容校验失败({reason}): {url}")
                            # 内容不对通常是源站/错误页问题，重试无益，直接判为永久错误
                            permanent_error = True
                            break
                    finally:
                        try:
                            response.close()
                        except Exception:
                            pass

                    request_seconds = time.time() - request_started
                    if self._persist_tile(x, y, z, data, file_path):
                        if d.performance_monitor:
                            d.performance_monitor.record_download(request_seconds, len(data))
                        if d.limiter is not None:
                            d.limiter.note_success()
                        if not (d.is_mbtiles and d.mbtiles_ack):
                            # 目录模式：文件已原子替换完成，可以立即标记成功；
                            # MBTiles + ack 模式：交给写线程 commit 成功后再标记，
                            # 确保 progress.db 不会领先于实际落盘。
                            d._add_bytes(len(data))
                            d._mark_tile_processed(x, y, z, 'success')
                            d._update_progress()
                        logger.debug(f"下载成功: z={z} x={x} y={y} ({len(data)} 字节)")
                        return 'success'

                    logger.warning(f"保存失败: z={z} x={x} y={y}")
                finally:
                    # 无论成功、失败、continue 还是 return，槽位恰好归还一次
                    if d.limiter is not None:
                        d.limiter.release()

            # 重试结束
            if not permanent_error and not d.stop_event.is_set():
                logger.error(f"下载失败(已重试 {d.retries} 次): {url}")
            d._mark_tile_processed(x, y, z, 'failed')
            d._update_progress()
            return 'failed'
        finally:
            # 每个从队列取出的任务恰好调用一次 task_done；
            # 同时把它移出去重集合（处理期间重复入队也会被拦下）
            d.forget_pending(task)
            d.task_queue.task_done()

    # ------------------------------------------------------------------ #
    # 辅助方法
    # ------------------------------------------------------------------ #
    def _wait_until_resumed(self) -> bool:
        """
        暂停时原地等待恢复。

        Returns:
            True 可以继续处理；False 表示已请求停止，应当放弃当前任务。
        """
        d = self.downloader
        if d.pause_event.is_set():
            return not d.stop_event.is_set()
        logger.debug(f"{threading.current_thread().name} 暂停，等待恢复")
        while not d.pause_event.is_set():
            if d.stop_event.is_set():
                return False
            d.pause_event.wait(0.2)
        return not d.stop_event.is_set()

    def _read_body(self, response):
        """分块读取响应体，过程中可响应停止。

        暂停不在这里中断：一个瓦片会完整读完并落盘，暂停在下一个瓦片边界生效。
        """
        d = self.downloader
        chunks = []
        for chunk in response.iter_content(chunk_size=8192):
            if d.stop_event.is_set():
                return 'stop', None
            if chunk:
                chunks.append(chunk)
        return 'ok', b''.join(chunks)

    def _interruptible_sleep(self, seconds: float) -> str:
        """退避等待，期间响应暂停与停止，返回 'ok' | 'stop'。

        暂停期间不计入退避时间。
        """
        d = self.downloader
        if not self._wait_until_resumed():
            return 'stop'
        deadline = time.time() + min(seconds, 5)
        while time.time() < deadline:
            if d.stop_event.is_set():
                return 'stop'
            time.sleep(0.1)
        return 'ok'

    def _validate_tile_data(self, data: bytes):
        """
        校验瓦片内容。

        Returns:
            (ok, reason)。``ok=False`` 时 reason 说明原因。
        """
        d = self.downloader
        if d.validate_image and not looks_like_image(data):
            return False, '不是有效的图片数据（可能是错误页/占位内容）'
        if d.reject_blank_tiles and is_blank_tile(data):
            return False, '纯色空白瓦片'
        return True, ''

    def _cleanup_stale_parts(self, directory, prefix: str):
        """
        清理同一瓦片遗留的临时文件。

        强杀/断电时 ``os.replace`` 没来得及执行，目录里会留下
        ``.<瓦片名>.<随机>.part``。这些文件既不是目标瓦片也不会被续传读取，
        只是白占磁盘。这里在重新写该瓦片前顺手清理（按 mtime 判龄，防止
        误删另一个并发实例正在写的临时文件）。
        """
        import glob as glob_module

        d = self.downloader
        if not d.cleanup_stale_parts:
            return
        cutoff = time.time() - d.stale_part_age_seconds
        try:
            candidates = glob_module.glob(
                os.path.join(str(directory), f"{glob_module.escape(prefix)}*.part")
            )
        except OSError:
            return
        for candidate in candidates:
            try:
                if os.path.getmtime(candidate) <= cutoff:
                    os.unlink(candidate)
                    logger.debug(f"清理残留临时文件: {candidate}")
            except OSError:
                continue

    def _persist_tile(self, x: int, y: int, z: int, data: bytes, file_path) -> bool:
        """把下载到的瓦片落盘（文件系统或 MBTiles 写入队列）。"""
        d = self.downloader
        if d.is_mbtiles:
            # MBTiles 的 tile_row 按规范自底部计数（TMS）。
            # 任务坐标始终是 XYZ，所以这里总是翻转；is_tms 只影响请求 URL。
            mbtiles_row = (2 ** z) - 1 - y
            # 带上任务坐标 y，写线程 commit 成功后用它回写进度
            return d._enqueue_mbtiles((z, x, mbtiles_row, data, y))

        return self._write_file_atomically(file_path, data)

    def _write_file_atomically(self, file_path, data: bytes) -> bool:
        """
        原子写入瓦片文件。

        直接 ``open(path, "wb")`` 写目标文件时，若进程在写盘中途被杀或磁盘写满，
        会留下半截文件；而断点续传只检查"文件是否存在"，于是这个损坏的瓦片会被
        永久跳过。这里改为先写同目录临时文件，再 ``os.replace()`` 原子替换：
        目标文件要么是完整的旧内容，要么是完整的新内容，不会出现半截。
        """
        d = self.downloader
        file_path = Path(file_path)
        directory = file_path.parent

        try:
            ensure_directory(directory)
        except OSError as e:
            logger.error(f"创建瓦片目录失败 {directory}: {e}")
            return False

        if not d.atomic_write:
            try:
                with open(file_path, "wb") as f:
                    f.write(data)
                return True
            except OSError as e:
                logger.error(f"文件写入错误 {file_path}: {e}")
                return False

        tmp_path = None
        try:
            # 只清理"这一个瓦片"的残留临时文件（前缀精确匹配），
            # 且必须超过 stale_part_age_hours，避免误删并发实例正在写的临时文件
            self._cleanup_stale_parts(directory, f".{file_path.name}.")
            fd, tmp_name = tempfile.mkstemp(
                dir=str(directory), prefix=f".{file_path.name}.", suffix=".part"
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                if d.atomic_fsync:
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(tmp_path, file_path)
            return True
        except OSError as e:
            logger.error(f"文件写入错误 {file_path}: {e}")
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            return False

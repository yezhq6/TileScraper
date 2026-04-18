# src/downloader/worker.py

import time
import threading
from queue import Queue, Empty
from typing import Tuple
from loguru import logger
import requests


class WorkerManager:
    """
    工作线程管理器：负责创建和管理下载工作线程
    """

    def __init__(self, downloader):
        """
        初始化工作线程管理器
        
        Args:
            downloader: TileDownloader实例
        """
        self.downloader = downloader
        self.worker_threads = []

    def start_workers(self):
        """
        启动工作线程
        """
        # 计算实际需要的线程数
        # 基础线程数：不超过配置的最大线程数、不超过系统CPU核心数*8
        # 对于大批量下载，适当增加线程数以充分利用网络带宽
        import os
        cpu_cores = os.cpu_count() or 4
        base_threads = min(
            self.downloader.max_threads, 
            cpu_cores * 8,  # 对于网络IO密集型任务，使用更多线程
            128  # 增加最大线程数到128，提高大批量下载的并发能力
        )
        # 确保至少有1个线程
        actual_threads = max(1, base_threads)
        
        logger.info(f"实际使用线程数: {actual_threads}, CPU核心数: {cpu_cores}")
        
        # 预创建线程池，提高线程启动效率
        self.worker_threads = []
        
        # 创建线程
        for i in range(actual_threads):
            t = threading.Thread(target=self._worker, name=f"Downloader-{i+1}", daemon=True)
            self.worker_threads.append(t)
            t.start()
            # 移除线程启动延迟，使用批量启动方式

        # 批量启动完成后记录
        logger.info(f"已启动 {actual_threads} 个下载线程，将持续处理任务")

    def wait_for_completion(self):
        """
        等待所有线程完成
        """
        for t in self.worker_threads:
            t.join()

    def _worker(self):
        """
        工作线程，处理下载任务，增强异常处理和错误恢复
        """
        thread_name = threading.current_thread().name
        logger.info(f"{thread_name} 启动")
        
        processed_in_batch = 0  # 用于定期保存进度的计数器
        session_manager = None  # 复用requests会话，减少连接开销
        
        # 批量处理相关
        batch_size = 50  # 增加批量处理的任务数量
        batch_tasks = []
        batch_tile_data = []
        
        # 线程状态追踪
        thread_start_time = time.time()
        processed_tasks = 0
        failed_tasks = 0
        
        # 线程本地连接管理
        thread_id = threading.get_ident()
        
        try:
            # 创建请求会话管理器
            from .request import RequestSessionManager
            session_manager = RequestSessionManager()
            session = session_manager.get_session()
            
            while not self.downloader.stop_event.is_set():
                # 先检查是否暂停
                if not self.downloader.pause_event.is_set():
                    logger.info(f"{thread_name} - 任务已暂停，等待恢复")
                    self.downloader.pause_event.wait()  # 无限等待直到恢复
                    if self.downloader.stop_event.is_set():
                        break
                    continue
                
                # 批量获取任务
                batch_tasks = []
                batch_tile_data = []
                
                # 尝试获取多个任务
                for _ in range(batch_size):
                    try:
                        # 获取任务，设置超时
                        task = self.downloader.task_queue.get(timeout=0.1)
                        if task is None:
                            try:
                                self.downloader._update_progress()
                                # 不要调用 task_done()，因为任务为None
                            except Exception as e:
                                # 避免异常
                                logger.warning(f"更新进度失败: {e}")
                            continue
                        batch_tasks.append(task)
                    except Empty:
                        # 无任务，退出循环
                        break
                
                if not batch_tasks:
                    # 任务队列暂时为空，继续等待
                    continue
                
                # 处理批量任务
                for task in batch_tasks:
                    x, y, z = task
                    logger.debug(f"{thread_name} 处理任务: z={z}, x={x}, y={y}")
                
                    ok = False
                    url = None
                    try:
                        # 再次检查是否暂停
                        if not self.downloader.pause_event.is_set():
                            logger.info(f"{thread_name} - 任务已暂停，将任务放回队列")
                            # 将任务重新放回队列，以便在恢复时继续处理
                            self.downloader.task_queue.put((x, y, z))
                            # 不要调用 task_done()，因为任务被放回队列了
                            # 无限等待直到恢复
                            self.downloader.pause_event.wait()
                            if self.downloader.stop_event.is_set():
                                break
                            continue
                        
                        # zoom 范围检查
                        if not (self.downloader.provider.min_zoom <= z <= self.downloader.provider.max_zoom):
                            logger.warning(
                                f"{thread_name} - zoom {z} 超出 [{self.downloader.provider.min_zoom}, {self.downloader.provider.max_zoom}]，跳过"
                            )
                            self.downloader._mark_tile_processed(x, y, z, 'skipped')
                            self.downloader._update_progress()
                            try:
                                self.downloader.task_queue.task_done()
                            except ValueError:
                                # 避免 task_done() 被调用过多
                                logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                            processed_in_batch += 1
                            continue

                        # 只有非MBTiles模式才检查文件是否存在和创建目录
                        file_path = None
                        if not self.downloader.is_mbtiles:
                            # 检查文件是否已存在
                            file_path = self.downloader.provider.get_tile_path(x, y, z, self.downloader.output_dir)
                            if file_path.exists():
                                logger.debug(f"{thread_name} - [跳过] 已存在: {file_path}")
                                self.downloader._mark_tile_processed(x, y, z, 'skipped')
                                self.downloader._update_progress()
                                try:
                                    self.downloader.task_queue.task_done()
                                except ValueError:
                                    # 避免 task_done() 被调用过多
                                    logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                                processed_in_batch += 1
                                continue

                            # 创建父目录
                            from .utils import ensure_directory
                            ensure_directory(file_path.parent)
                        
                        # 获取下载URL
                        url = self.downloader.provider.get_tile_url(x, y, z)
                        
                        for attempt in range(self.downloader.retries):
                            # 检查是否需要停止
                            if self.downloader.stop_event.is_set():
                                logger.info(f"{thread_name} - 收到停止信号，取消当前任务")
                                break
                            
                            # 检查是否暂停
                            if not self.downloader.pause_event.is_set():
                                logger.info(f"{thread_name} - 任务已暂停，等待恢复")
                                self.downloader.pause_event.wait(timeout=1)  # 等待直到恢复或超时
                                
                                # 如果超时后仍然暂停，跳过当前任务
                                if not self.downloader.pause_event.is_set():
                                    logger.info(f"{thread_name} - 任务仍然暂停，跳过当前任务")
                                    self.downloader._mark_tile_processed(x, y, z, 'skipped')
                                    self.downloader._update_progress()
                                    # 不要调用 task_done()，因为任务被放回队列了
                                    processed_in_batch += 1
                                    return
                            
                            try:
                                logger.debug(f"{thread_name} - 下载: {url} (尝试 {attempt+1}/{self.downloader.retries})")
                                
                                # 使用已创建的会话对象
                                response = None
                                try:
                                    # 分块下载，以便在下载过程中检查暂停/停止信号
                                    response = session.get(url, stream=True, timeout=5, allow_redirects=True)
                                    
                                    # 检查响应状态
                                    if response.status_code != 200:
                                        logger.warning(f"{thread_name} - [HTTP {response.status_code}] {url}")
                                        if response.status_code in [403, 404]:
                                            # 403禁止访问或404不存在，直接跳过
                                            logger.warning(f"{thread_name} - 永久错误，停止重试: {url}")
                                            break
                                        continue
                                    
                                    # 验证响应内容
                                    content_type = response.headers.get('Content-Type', '')
                                    if not ('image' in content_type or 'jpeg' in content_type or 'png' in content_type):
                                        logger.warning(f"{thread_name} - 非图片响应: {url}, Content-Type: {content_type}")
                                        continue
                                    
                                    # 记录下载开始时间
                                    download_start_time = time.time()
                                    
                                    # 分块读取响应内容，增大chunk size提高下载速度
                                    tile_data = b''
                                    for chunk in response.iter_content(chunk_size=8192):  # 增大chunk size到8KB
                                        # 检查是否需要停止
                                        if self.downloader.stop_event.is_set():
                                            logger.info(f"{thread_name} - 收到停止信号，取消当前下载")
                                            ok = False
                                            break
                                        
                                        # 检查是否暂停
                                        if not self.downloader.pause_event.is_set():
                                            logger.info(f"{thread_name} - 任务已暂停，将任务放回队列")
                                            # 将任务重新放回队列，以便在恢复时继续处理
                                            self.downloader.task_queue.put((x, y, z))
                                            # 不要调用 task_done()，因为任务被放回队列了
                                            # 无限等待直到恢复
                                            self.downloader.pause_event.wait()
                                            if self.downloader.stop_event.is_set():
                                                return
                                            ok = False
                                            break
                                        
                                        if chunk:
                                            tile_data += chunk
                                    
                                    # 如果在分块读取过程中被停止，退出循环
                                    if self.downloader.stop_event.is_set():
                                        break
                                    
                                    # 计算下载时间
                                    download_duration = time.time() - download_start_time
                                    
                                    # 验证下载的数据
                                    if len(tile_data) == 0:
                                        logger.error(f"{thread_name} - 下载数据为空: {url}")
                                        continue
                                    
                                    total_bytes = len(tile_data)
                                    
                                    if self.downloader.is_mbtiles:
                                        # 保存到MBTiles数据库
                                        try:
                                            # MBTiles中的tile_row是从顶部开始计数的，需要转换
                                            mbtiles_row = (2 ** z) - 1 - y
                                            
                                            # 将写入任务放入队列
                                            self.downloader.mbtiles_write_queue.put((z, x, mbtiles_row, tile_data))
                                            
                                            logger.info(f"{thread_name} - 下载成功: MBTiles [{z}/{x}/{y}] ({total_bytes} 字节) - 耗时: {download_duration:.3f}秒")
                                        except Exception as e:
                                            logger.error(f"{thread_name} - MBTiles保存失败: {e}")
                                            continue
                                    else:
                                        # 保存到文件系统
                                        try:
                                            with open(file_path, "wb") as f:
                                                f.write(tile_data)
                                            
                                            logger.info(f"{thread_name} - 下载成功: {file_path} ({total_bytes} 字节) - 耗时: {download_duration:.3f}秒")
                                        except IOError as e:
                                            logger.error(f"{thread_name} - 文件写入错误 {file_path} - {e}")
                                            # 尝试创建目录
                                            try:
                                                ensure_directory(file_path.parent)
                                                # 再次尝试写入
                                                with open(file_path, "wb") as f:
                                                    f.write(tile_data)
                                                logger.info(f"{thread_name} - 重试文件写入成功: {file_path}")
                                            except Exception as retry_error:
                                                logger.error(f"{thread_name} - 重试文件写入失败: {retry_error}")
                                                continue
                                    
                                    self.downloader.total_bytes += total_bytes  # 更新总字节数
                                    self.downloader._mark_tile_processed(x, y, z, 'success')
                                    processed_tasks += 1
                                    
                                    # 记录性能数据
                                    if self.downloader.performance_monitor:
                                        self.downloader.performance_monitor.record_download(download_duration, total_bytes)
                                    
                                    # 事务管理：每1000个瓦片提交一次事务
                                    if self.downloader.is_mbtiles:
                                        self.downloader.transaction_counter += 1
                                        
                                        # 达到批量大小，提交事务
                                        if self.downloader.transaction_counter % self.downloader.transaction_batch_size == 0:
                                            if self.downloader.enable_sharding:
                                                mbtiles_conn = self.downloader.mbtiles_manager.get_mbtiles_connection(z)
                                                # 每个线程使用独立连接，不需要锁
                                                mbtiles_conn.commit()
                                                logger.debug(f"批量提交事务: {self.downloader.transaction_batch_size} 个瓦片")
                                            else:
                                                # 使用线程专属连接提交事务
                                                if hasattr(self.downloader.thread_local, 'mbtiles_conn'):
                                                    self.downloader.thread_local.mbtiles_conn.commit()
                                                    logger.debug(f"批量提交事务: {self.downloader.transaction_batch_size} 个瓦片")
                                    
                                    ok = True
                                    break
                                finally:
                                    if response:
                                        response.close()
                            except requests.exceptions.ConnectionError as e:
                                logger.error(f"{thread_name} - 连接错误 {url} - {e}")
                                # 尝试重建会话
                                try:
                                    session = session_manager.create_session()
                                    logger.info(f"{thread_name} - 会话已重建")
                                except Exception as session_error:
                                    logger.error(f"{thread_name} - 会话重建失败: {session_error}")
                            except requests.exceptions.Timeout as e:
                                logger.error(f"{thread_name} - 超时错误 {url} - {e}")
                            except requests.exceptions.RequestException as e:
                                logger.error(f"{thread_name} - 请求错误 {url} - {e}")
                            except IOError as e:
                                logger.error(f"{thread_name} - 文件写入错误 {file_path} - {e}")
                            except Exception as e:
                                logger.error(f"{thread_name} - 未知错误 {url} - {e}")
                                import traceback
                                traceback.print_exc()
                            
                            # 检查是否需要停止
                            if self.downloader.stop_event.is_set():
                                logger.info(f"{thread_name} - 收到停止信号，停止重试")
                                break
                            
                            # 指数退避重试，增加最大延迟限制
                            retry_delay = min(
                                self.downloader.delay * (2 ** attempt) * (0.5 + 0.5 * (hash(url) % 2)),
                                5  # 进一步减少最大延迟到5秒，以便更快响应暂停/停止信号
                            )
                            
                            # 在重试延迟期间定期检查暂停/停止信号
                            start_time = time.time()
                            while time.time() - start_time < retry_delay:
                                if self.downloader.stop_event.is_set():
                                    logger.info(f"{thread_name} - 收到停止信号，停止重试")
                                    break
                                if not self.downloader.pause_event.is_set():
                                    logger.info(f"{thread_name} - 任务已暂停，将任务放回队列")
                                    # 将任务重新放回队列，以便在恢复时继续处理
                                    self.downloader.task_queue.put((x, y, z))
                                    # 不要调用 task_done()，因为任务被放回队列了
                                    # 无限等待直到恢复
                                    self.downloader.pause_event.wait()
                                    if self.downloader.stop_event.is_set():
                                        return
                                    return
                                time.sleep(0.1)

                    except Exception as e:
                        logger.error(f"{thread_name} - 任务处理错误: z={z}, x={x}, y={y} - {e}")
                        import traceback
                        traceback.print_exc()
                        self.downloader._mark_tile_processed(x, y, z, 'failed')
                        failed_tasks += 1
                        self.downloader._update_progress()
                        try:
                            self.downloader.task_queue.task_done()
                        except ValueError:
                            # 避免 task_done() 被调用过多
                            logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                        processed_in_batch += 1
                    else:
                        if not ok and url:
                            logger.error(f"{thread_name} - 下载失败: {url}")
                            self.downloader._mark_tile_processed(x, y, z, 'failed')
                            failed_tasks += 1
                        
                        # 标记任务完成 - 只调用一次
                        try:
                            self.downloader.task_queue.task_done()
                        except ValueError:
                            # 避免 task_done() 被调用过多
                            logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                        processed_in_batch += 1
                        
                        # 增加进度更新频率，每2个任务更新一次
                        # if processed_in_batch % 2 == 0:
                        #     self.downloader._update_progress()
                        self.downloader._update_progress()
                
                # 每处理1000个瓦片保存一次进度，减少IO操作
                if self.downloader.enable_resume and processed_in_batch >= 1000:
                    self.downloader._save_progress()
                    processed_in_batch = 0
                    
        except Exception as e:
            logger.error(f"{thread_name} - 线程内部异常: {e}")
            import traceback
            traceback.print_exc()
            # 确保线程不会因异常退出
            time.sleep(0.5)
        
        # 线程结束前保存进度
        if self.downloader.enable_resume:
            try:
                self.downloader._save_progress()
                logger.info(f"{thread_name} - 线程结束前保存进度")
            except Exception as save_error:
                logger.error(f"{thread_name} - 线程结束前保存进度失败: {save_error}")
        
        # 关闭会话，释放资源
        if session_manager:
            session_manager.close()
        
        # 关闭线程专属的MBTiles连接
        if hasattr(self.downloader.thread_local, 'mbtiles_conn'):
            try:
                self.downloader.thread_local.mbtiles_conn.commit()
                self.downloader.thread_local.mbtiles_conn.close()
                logger.debug(f"{thread_name} - 关闭线程专属MBTiles连接")
            except Exception as e:
                logger.error(f"{thread_name} - 关闭MBTiles连接失败: {e}")
        
        # 注意：连接池会在 close_connections 方法中自动处理所有连接的关闭
        # 这里不需要额外的清理操作
        
        # 记录线程统计信息
        thread_duration = time.time() - thread_start_time
        logger.info(f"{thread_name} 结束 - 运行时间: {thread_duration:.2f} 秒, 处理任务: {processed_tasks}, 失败任务: {failed_tasks}")

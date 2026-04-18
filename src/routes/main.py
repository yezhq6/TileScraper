# src/routes/main.py

from flask import Blueprint, jsonify, request, render_template
from queue import Empty
from loguru import logger
from ..providers import ProviderManager
from ..downloader import TileDownloader
from ..config import config_manager

main_bp = Blueprint('main', __name__)

# 全局变量
current_downloader = None
current_downloader_lock = None
current_download_params = None
progress_queue = None

# 下载速度和时间跟踪
last_downloaded = 0
last_time = 0
last_bytes = 0


def init_globals(downloader_lock, queue):
    """
    初始化全局变量
    """
    global current_downloader_lock, progress_queue
    current_downloader_lock = downloader_lock
    progress_queue = queue


def update_progress(downloaded, total, total_bytes=0, completed=False, stats=None):
    """
    更新下载进度
    """
    import json
    import time
    
    global last_downloaded, last_time, last_bytes
    
    # 计算下载速度
    current_time = time.time()
    time_diff = current_time - last_time
    speed = 0
    
    if time_diff > 0 and downloaded > last_downloaded:
        # 计算速度（KB/s）
        bytes_diff = total_bytes - last_bytes
        speed = bytes_diff / (1024 * time_diff)
        
        # 更新跟踪变量
        last_downloaded = downloaded
        last_time = current_time
        last_bytes = total_bytes
    
    # 计算预计剩余时间
    eta = "-"
    if speed > 0 and total > 0:
        remaining_bytes = (total - downloaded) * (total_bytes / downloaded if downloaded > 0 else 1)
        remaining_time = remaining_bytes / (speed * 1024)
        
        # 格式化剩余时间
        if remaining_time < 60:
            eta = f"{int(remaining_time)}秒"
        elif remaining_time < 3600:
            minutes = int(remaining_time / 60)
            seconds = int(remaining_time % 60)
            eta = f"{minutes}分{seconds}秒"
        else:
            hours = int(remaining_time / 3600)
            minutes = int((remaining_time % 3600) / 60)
            eta = f"{hours}小时{minutes}分"
    
    progress_data = {
        'downloaded': downloaded,
        'total': total,
        'total_bytes': total_bytes,
        'percentage': int((downloaded / total * 100) if total > 0 else 0),
        'speed': round(speed, 2),
        'eta': eta,
        'completed': completed,
        'stats': stats
    }
    progress_queue.put(progress_data)


@main_bp.route('/')
def index():
    """
    首页
    """
    return render_template('index.html')


@main_bp.route('/api/download', methods=['POST'])
def api_download():
    """
    处理下载请求
    """
    import threading
    import time
    from queue import Empty
    
    global current_downloader, current_download_params
    
    try:
        # 解析请求数据
        data = request.get_json()
        provider_url = data.get('provider_url')
        provider_name = data.get('provider_name', 'custom')
        north = data.get('north')
        south = data.get('south')
        west = data.get('west')
        east = data.get('east')
        # 确保转换为整数类型
        min_zoom = int(data.get('min_zoom', 1))
        max_zoom = int(data.get('max_zoom', 10))
        output_dir = data.get('output_dir', 'tiles')
        threads = int(data.get('threads', 4))
        is_tms = data.get('tms', False)
        subdomains = data.get('subdomains', [])
        tile_format = data.get('tile_format')
        save_format = data.get('save_format', 'directory')
        
        # 验证参数
        if not all([north, south, west, east, min_zoom, max_zoom]):
            return jsonify({'error': 'Missing required parameters'}), 400
        
        # 处理子域名
        if isinstance(subdomains, str):
            subdomains = [s.strip() for s in subdomains.split(',') if s.strip()]
        
        # 处理提供商
        if provider_url:
            # 创建自定义提供商
            try:
                ProviderManager.create_custom_provider(
                    name=provider_name,
                    url_template=provider_url,
                    subdomains=subdomains,
                    min_zoom=int(min_zoom),
                    max_zoom=int(max_zoom)
                )
                logger.info(f"创建自定义提供商: {provider_name}, URL: {provider_url}")
            except Exception as e:
                logger.error(f"创建自定义提供商失败: {e}")
                return jsonify({'error': f'Failed to create custom provider: {e}'}), 500
        
        logger.info(f"开始下载任务: provider={provider_name}, bbox=[{west},{south},{east},{north}], "
                   f"zoom={min_zoom}-{max_zoom}, threads={threads}, is_tms={is_tms}, output_dir={output_dir}")
        
        try:
            # 定义进度回调函数
            def progress_callback(downloaded, total, total_bytes=0):
                """下载进度回调"""
                update_progress(downloaded, total, total_bytes)
            
            # 确定scheme参数
            scheme = 'tms' if is_tms else 'xyz'
            
            # 直接使用TileDownloader而不是BatchDownloader，以便保存实例
            global current_downloader
            with current_downloader_lock:
                # 检查是否已经有正在进行的下载任务，如果有，先取消它
                if current_downloader:
                    logger.info("发现正在进行的下载任务，正在取消...")
                    # 保存当前进度
                    if hasattr(current_downloader, '_save_progress'):
                        try:
                            current_downloader._save_progress()
                            logger.info("取消旧任务时保存进度")
                        except Exception as e:
                            logger.error(f"保存进度失败: {e}")
                    # 清空任务队列
                    while True:
                        try:
                            current_downloader.task_queue.get_nowait()
                            try:
                                current_downloader.task_queue.task_done()
                            except ValueError:
                                # 避免 task_done() 被调用过多
                                logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                                break
                        except Empty:
                            break
                    # 触发停止事件
                    current_downloader.stop_event.set()
                    # 重置暂停事件
                    current_downloader.pause_event.set()
                    # 等待一小段时间，让线程有机会处理停止事件
                    time.sleep(0.1)  # 缩短等待时间，提高响应速度
                    # 清空全局变量
                    current_downloader = None
                    logger.info("旧下载任务已取消")
                
                # 保存当前下载参数
                global current_download_params
                current_download_params = {
                    'provider_url': provider_url,
                    'north': north,
                    'south': south,
                    'west': west,
                    'east': east,
                    'min_zoom': min_zoom,
                    'max_zoom': max_zoom,
                    'output_dir': output_dir,
                    'threads': threads,
                    'tms': is_tms,
                    'subdomains': subdomains,
                    'tile_format': tile_format,
                    'save_format': save_format
                }
                logger.info(f"保存当前下载参数: {current_download_params}")
                
                # 创建新的下载器实例
                current_downloader = TileDownloader(
                    provider_name,
                    output_dir,
                    max_threads=threads,
                    is_tms=is_tms,
                    progress_callback=progress_callback,
                    tile_format=tile_format,
                    save_format=save_format,
                    scheme=scheme
                )
            
            # 发送初始进度更新
            update_progress(0, 0)
            
            # 任务添加完成标志
            tasks_added = False
            
            # 启动下载线程
            def start_download():
                """启动下载线程"""
                try:
                    global current_downloader
                    with current_downloader_lock:
                        downloader = current_downloader
                    
                    if downloader:
                        # 启动下载线程
                        downloader.start()
                except Exception as e:
                    logger.error(f"下载线程异常: {e}")
            
            # 添加任务的线程函数
            def add_tasks():
                """添加任务的线程函数"""
                nonlocal tasks_added
                try:
                    global current_downloader
                    with current_downloader_lock:
                        downloader = current_downloader
                    
                    if downloader:
                        # 添加下载任务（流式处理）
                        downloader.add_tasks_for_bbox(
                            west, south, east, north, min_zoom, max_zoom
                        )
                        total_tasks = downloader.total_tasks
                        
                        # 更新总任务数
                        update_progress(0, total_tasks)
                except Exception as e:
                    logger.error(f"添加任务失败: {e}")
                finally:
                    # 标记任务添加完成
                    tasks_added = True
                    logger.info("任务添加完成")
            
            # 下载完成后发送最终进度更新
            def send_final_update():
                """发送最终进度更新"""
                try:
                    global current_downloader
                    with current_downloader_lock:
                        downloader = current_downloader
                    
                    if downloader and downloader.total_tasks > 0:
                        stats = {
                            'downloaded': downloader.downloaded_count,
                            'failed': downloader.failed_count,
                            'skipped': downloader.skipped_count,
                            'total': downloader.total_tasks
                        }
                        # 只有当总任务数大于0时才发送完成信号
                        update_progress(downloader.downloaded_count, downloader.total_tasks, completed=True, stats=stats)
                        logger.info(f"发送最终进度更新: 已下载={downloader.downloaded_count}, 失败={downloader.failed_count}, 跳过={downloader.skipped_count}, 总计={downloader.total_tasks}")
                    else:
                        logger.warning("跳过发送最终进度更新: 没有下载器实例或总任务数为0")
                except Exception as e:
                    logger.error(f"发送最终进度更新失败: {e}")
            
            # 启动一个线程来监控下载完成
            def monitor_download():
                """监控下载完成"""
                try:
                    global current_downloader
                    check_interval = 0.3  # 缩短检查间隔，提高响应速度
                    while True:
                        time.sleep(check_interval)
                        with current_downloader_lock:
                            if not current_downloader:
                                break
                        
                        # 检查下载是否完成
                        if current_downloader:
                            # 计算已处理的任务数
                            processed_count = current_downloader.downloaded_count + current_downloader.failed_count + current_downloader.skipped_count
                            # 检查任务添加是否完成、任务队列是否为空、已处理数是否等于总任务数且总任务数大于0
                            if tasks_added and current_downloader.task_queue.empty() and processed_count >= current_downloader.total_tasks and current_downloader.total_tasks > 0:
                                # 等待一小段时间确保所有线程都已完成
                                # 根据总任务数动态调整等待时间
                                # 增加等待时间，确保所有工作线程都有足够的时间完成它们正在执行的任务
                                wait_time = min(3.0, max(1.0, current_downloader.total_tasks / 5000))
                                time.sleep(wait_time)
                                # 再次检查已处理数是否仍然等于总任务数
                                processed_count = current_downloader.downloaded_count + current_downloader.failed_count + current_downloader.skipped_count
                                if processed_count >= current_downloader.total_tasks:
                                    # 记录性能统计信息
                                    if hasattr(current_downloader, 'log_performance_statistics'):
                                        try:
                                            current_downloader.log_performance_statistics()
                                            logger.info("已记录性能统计信息")
                                        except Exception as perf_error:
                                            logger.error(f"记录性能统计信息失败: {perf_error}")
                                    # 完成下载，确保所有MBTiles事务都已提交
                                    if hasattr(current_downloader, 'mbtiles_manager'):
                                        try:
                                            current_downloader.mbtiles_manager.finalize_download()
                                            logger.info("已完成下载，提交所有MBTiles事务")
                                        except Exception as finalize_error:
                                            logger.error(f"完成下载时出错: {finalize_error}")
                                    # 发送最终进度更新
                                    send_final_update()
                                    break
                except Exception as e:
                    logger.error(f"监控下载失败: {e}")
            
            # 先在单独线程中添加任务
            add_tasks_thread = threading.Thread(target=add_tasks, daemon=True)
            add_tasks_thread.start()
            
            # 等待任务添加完成后再启动下载线程
            add_tasks_thread.join()
            
            # 启动下载线程
            threading.Thread(target=start_download, daemon=True).start()
            
            # 启动监控线程
            threading.Thread(target=monitor_download, daemon=True).start()
            
            return jsonify({'success': True, 'message': '下载任务已开始'})
        except Exception as e:
            logger.error(f"下载请求处理失败: {e}")
            return jsonify({'error': f'Failed to start download: {e}'}), 500
    except Exception as e:
        logger.error(f"API请求处理失败: {e}")
        return jsonify({'error': f'Request processing failed: {e}'}), 500


@main_bp.route('/api/providers')
def api_providers():
    """
    获取支持的瓦片提供商及其信息
    """
    providers = ProviderManager.get_all_providers_info()
    return jsonify(providers)


@main_bp.route('/api/progress')
def api_progress():
    """
    SSE端点，用于推送下载进度
    """
    def event_stream():
        try:
            import json
            # 持续从队列中获取进度事件并发送
            while True:
                try:
                    # 非阻塞获取队列中的进度数据
                    progress_data = progress_queue.get(timeout=0.1)
                    # 格式化SSE事件
                    event = f"data: {json.dumps(progress_data)}\n\n"
                    yield event
                except Empty:
                    # 如果队列为空，继续等待
                    continue
        except GeneratorExit:
            # 客户端断开连接
            pass
    return event_stream(), {'Content-Type': 'text/event-stream'}


@main_bp.route('/api/pause-download', methods=['POST'])
def api_pause_download():
    """
    暂停下载
    """
    global current_downloader
    with current_downloader_lock:
        if current_downloader:
            try:
                current_downloader.pause()
                return jsonify({'success': True, 'message': '下载已暂停'})
            except Exception as e:
                logger.error(f"暂停下载失败: {e}")
                return jsonify({'success': False, 'message': f'暂停失败: {e}'})
        else:
            return jsonify({'success': False, 'message': '没有正在进行的下载任务'})


@main_bp.route('/api/resume-download', methods=['POST'])
def api_resume_download():
    """
    恢复下载
    """
    global current_downloader
    with current_downloader_lock:
        if current_downloader:
            try:
                current_downloader.resume()
                return jsonify({'success': True, 'message': '下载已恢复'})
            except Exception as e:
                logger.error(f"恢复下载失败: {e}")
                return jsonify({'success': False, 'message': f'恢复失败: {e}'})
        else:
            return jsonify({'success': False, 'message': '没有正在进行的下载任务'})


@main_bp.route('/api/cancel-download', methods=['POST'])
def api_cancel_download():
    """
    取消下载
    """
    global current_downloader
    with current_downloader_lock:
        if current_downloader:
            try:
                stats = current_downloader.cancel()
                current_downloader = None
                return jsonify({'success': True, 'message': '下载已取消', 'stats': stats})
            except Exception as e:
                logger.error(f"取消下载失败: {e}")
                return jsonify({'success': False, 'message': f'取消失败: {e}'})
        else:
            return jsonify({'success': False, 'message': '没有正在进行的下载任务'})


@main_bp.route('/api/download-status')
def api_download_status():
    """
    获取当前下载状态
    """
    global current_downloader
    with current_downloader_lock:
        if current_downloader:
            stats = current_downloader.get_statistics()
            status = {
                'is_downloading': True,
                'is_paused': current_downloader.is_paused(),
                'stats': stats
            }
            return jsonify(status)
        else:
            return jsonify({'is_downloading': False})


@main_bp.route('/api/config/list')
def api_config_list():
    """
    列出所有配置
    """
    try:
        configs = config_manager.list_configs()
        return jsonify({'success': True, 'configs': configs})
    except Exception as e:
        logger.error(f"列出配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)})


@main_bp.route('/api/config/save', methods=['POST'])
def api_config_save():
    """
    保存配置
    """
    try:
        data = request.get_json()
        config_name = data.get('config_name')
        config_data = data.get('config_data')
        
        if not config_name or not config_data:
            return jsonify({'success': False, 'error': '缺少配置名称或配置数据'})
        
        success = config_manager.save_config(config_name, config_data)
        if success:
            return jsonify({'success': True, 'message': '配置保存成功'})
        else:
            return jsonify({'success': False, 'error': '配置保存失败'})
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)})


@main_bp.route('/api/config/load/<config_name>')
def api_config_load(config_name):
    """
    加载配置
    """
    try:
        config_data = config_manager.load_config(config_name)
        if config_data:
            # 检查配置数据的结构，处理嵌套的data字段
            actual_data = config_data
            # 处理双重嵌套的情况
            if 'data' in actual_data and isinstance(actual_data['data'], dict):
                actual_data = actual_data['data']
                # 再次检查是否还有嵌套的data字段
                if 'data' in actual_data and isinstance(actual_data['data'], dict):
                    actual_data = actual_data['data']
            
            # 返回前端期望的结构
            return jsonify({'success': True, 'config': {'name': config_name, 'data': actual_data}})
        else:
            return jsonify({'success': False, 'error': '配置不存在'})
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)})


@main_bp.route('/api/download-params')
def api_download_params():
    """
    获取当前下载参数
    """
    global current_download_params
    if current_download_params:
        return jsonify({'success': True, 'params': current_download_params})
    else:
        return jsonify({'success': False, 'error': '没有当前下载参数'})

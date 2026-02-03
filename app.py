from flask import Flask, render_template, request, jsonify, Response
import os
import logging
import threading
import time
import json
import queue
from queue import Empty
from pathlib import Path

# 添加src到Python路径
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.downloader import BatchDownloader, TileDownloader
from src.providers import ProviderManager

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 配置
app.config['UPLOAD_FOLDER'] = 'tiles_datasets'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['MAX_THREADS'] = 32  # 限制最大线程数

# 确保瓦片目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
logger.info(f"应用启动，瓦片存储目录: {app.config['UPLOAD_FOLDER']}")

# 确保配置目录存在
app.config['CONFIG_FOLDER'] = 'configs'
os.makedirs(app.config['CONFIG_FOLDER'], exist_ok=True)
logger.info(f"应用启动，配置存储目录: {app.config['CONFIG_FOLDER']}")

# 全局下载控制
current_downloader = None
current_downloader_lock = threading.Lock()

# 进度更新事件管理器
progress_queue = queue.Queue()

def update_progress(downloaded, total, total_bytes=0, completed=False, stats=None):
    """更新进度"""
    # 确保下载数量和总任务数是有效的数字
    downloaded = int(downloaded) if downloaded is not None else 0
    total = int(total) if total is not None else 0
    
    # 计算百分比，避免除以零
    percentage = int((downloaded / total) * 100) if total > 0 else 0
    
    progress_data = {
        'downloaded': downloaded,
        'total': total,
        'total_bytes': total_bytes,
        'percentage': percentage,
        'completed': completed and total > 0,  # 只有当总任务数大于0时才标记为完成
        'stats': stats
    }
    
    # 将进度数据放入队列
    progress_queue.put(progress_data)
    logger.debug(f"发送进度更新: 已下载={downloaded}, 总计={total}, 百分比={percentage}%")


def clear_downloader_tasks():
    """清空下载器任务队列"""
    global current_downloader
    tasks_cleared = 0
    try:
        while True:
            try:
                current_downloader.task_queue.get_nowait()
                try:
                    current_downloader.task_queue.task_done()
                    tasks_cleared += 1
                except ValueError:
                    # 避免 task_done() 被调用过多
                    logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                    break
            except Empty:
                break
        logger.info(f"已清空任务队列，共清除 {tasks_cleared} 个任务")
    except Exception as e:
        logger.error(f"清空任务队列失败: {e}")


def get_downloader_stats():
    """获取下载器统计信息"""
    global current_downloader
    if current_downloader:
        processed = current_downloader.downloaded_count + current_downloader.failed_count + current_downloader.skipped_count
        remaining = max(0, current_downloader.total_tasks - processed)
        return {
            'downloaded': current_downloader.downloaded_count,
            'failed': current_downloader.failed_count,
            'skipped': current_downloader.skipped_count,
            'total': current_downloader.total_tasks,
            'remaining': remaining
        }
    return {
        'downloaded': 0,
        'failed': 0,
        'skipped': 0,
        'total': 0,
        'remaining': 0
    }

@app.route('/')
def index():
    # 对于自定义提供商，我们不再需要传递提供商列表到模板
    return render_template('index.html')

@app.route('/api/download', methods=['POST'])
def api_download():
    """
    处理下载请求
    """
    try:
        # 获取请求参数
        if not request.is_json:
            return jsonify({'error': 'Request must be JSON format'}), 400
        
        data = request.get_json()
        
        # 验证必填参数
        required_fields = ['provider_url', 'north', 'south', 'west', 'east', 'min_zoom', 'max_zoom', 'output_dir']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # 验证并转换参数类型
        try:
            provider_url = data['provider_url']
            north = float(data['north'])
            south = float(data['south'])
            west = float(data['west'])
            east = float(data['east'])
            min_zoom = int(data['min_zoom'])
            max_zoom = int(data['max_zoom'])
            output_dir = data['output_dir']
            threads = int(data.get('threads', 4))
            is_tms = bool(data.get('tms', False))
            subdomains = data.get('subdomains', [])
            tile_format = data.get('tile_format', 'png')
            save_format = data.get('save_format', 'directory')
        except ValueError as e:
            return jsonify({'error': f'Invalid parameter format: {e}'}), 400
        
        # 验证参数范围
        if north <= south:
            return jsonify({'error': 'North must be greater than south'}), 400
        if east <= west:
            return jsonify({'error': 'East must be greater than west'}), 400
        if min_zoom < 0 or max_zoom < 0:
            return jsonify({'error': 'Zoom level must be non-negative'}), 400
        if min_zoom > max_zoom:
            return jsonify({'error': 'Min zoom must be less than or equal to max zoom'}), 400
        
        # 限制线程数
        threads = max(1, min(threads, app.config['MAX_THREADS']))
        
        # 创建自定义瓦片提供商
        provider_name = 'custom'
        try:
            # 清理旧的custom提供商（如果存在）
            if provider_name.lower() in ProviderManager._providers:
                del ProviderManager._providers[provider_name.lower()]
            
            # 创建新的自定义提供商
            ProviderManager.create_custom_provider(
                name=provider_name,
                url_template=provider_url,
                subdomains=subdomains,
                min_zoom=min_zoom,
                max_zoom=max_zoom
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
                            # 检查任务添加是否完成、任务队列是否为空且总任务数大于0
                            if tasks_added and current_downloader.task_queue.empty() and current_downloader.total_tasks > 0:
                                # 等待一小段时间确保所有线程都已完成
                                # 根据总任务数动态调整等待时间
                                wait_time = min(1.0, max(0.3, current_downloader.total_tasks / 10000))
                                time.sleep(wait_time)
                                # 再次检查任务队列是否仍然为空
                                if current_downloader.task_queue.empty():
                                    # 记录性能统计信息
                                    if hasattr(current_downloader, 'log_performance_statistics'):
                                        try:
                                            current_downloader.log_performance_statistics()
                                            logger.info("已记录性能统计信息")
                                        except Exception as perf_error:
                                            logger.error(f"记录性能统计信息失败: {perf_error}")
                                    # 发送最终进度更新
                                    send_final_update()
                                    break
                except Exception as e:
                    logger.error(f"监控下载失败: {e}")
            
            # 启动下载线程
            threading.Thread(target=start_download, daemon=True).start()
            
            # 在单独线程中添加任务（实现真正的并行处理）
            threading.Thread(target=add_tasks, daemon=True).start()
            
            # 启动监控线程
            threading.Thread(target=monitor_download, daemon=True).start()
            
            return jsonify({'success': True, 'message': '下载任务已开始'})
        except Exception as e:
            logger.error(f"下载请求处理失败: {e}")
            return jsonify({'error': f'Failed to start download: {e}'}), 500
    except Exception as e:
        logger.error(f"API请求处理失败: {e}")
        return jsonify({'error': f'Request processing failed: {e}'}), 500

@app.route('/api/providers')
def api_providers():
    # 获取支持的瓦片提供商及其信息
    providers = []
    for name in ProviderManager.list_providers():
        p = ProviderManager.get_provider(name)
        providers.append({
            'name': name,
            'type': p.provider_type.value,
            'min_zoom': p.min_zoom,
            'max_zoom': p.max_zoom
        })
    return jsonify(providers)

@app.route('/api/progress')
def api_progress():
    """SSE端点，用于推送下载进度"""
    def event_stream():
        try:
            # 持续从队列中获取进度事件并发送
            while True:
                try:
                    # 非阻塞获取队列中的进度数据
                    progress_data = progress_queue.get(timeout=0.1)
                    # 格式化SSE事件
                    event = f"data: {json.dumps(progress_data)}\n\n"
                    yield event
                except queue.Empty:
                    # 如果队列为空，继续等待
                    continue
        except GeneratorExit:
            # 客户端断开连接
            pass
    
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/api/cancel-download', methods=['POST'])
def api_cancel_download():
    """取消当前下载任务"""
    global current_downloader
    
    try:
        with current_downloader_lock:
            if current_downloader:
                # 使用新的cancel方法
                if hasattr(current_downloader, 'cancel'):
                    logger.info("使用新的cancel方法取消下载任务")
                    stats = current_downloader.cancel()
                else:
                    # 兼容旧版本
                    logger.info("使用旧方法取消下载任务")
                    # 保存当前进度
                    if hasattr(current_downloader, '_save_progress'):
                        try:
                            current_downloader._save_progress()
                            logger.info("取消下载时保存进度")
                        except Exception as e:
                            logger.error(f"保存进度失败: {e}")
                    
                    # 清空任务队列，立即停止所有下载
                    clear_downloader_tasks()
                    
                    # 触发停止事件
                    current_downloader.stop_event.set()
                    
                    # 重置暂停事件，确保下次下载不受影响
                    current_downloader.pause_event.set()
                    
                    # 等待一小段时间，让线程有机会处理停止事件
                    time.sleep(0.2)  # 缩短等待时间，提高响应速度
                    
                    # 获取当前下载的统计信息
                    stats = get_downloader_stats()
                
                # 清空全局变量
                current_downloader = None
                
                logger.info(f"下载任务已取消: 已下载={stats['downloaded']}, 失败={stats['failed']}, 跳过={stats['skipped']}, 总计={stats['total']}")
                return jsonify({'success': True, 'message': '下载已取消', 'stats': stats})
            return jsonify({'success': False, 'message': '没有正在进行的下载任务'})
    except Exception as e:
        logger.error(f"取消下载时发生异常: {e}")
        return jsonify({'success': False, 'message': f'取消下载时发生异常: {str(e)}'})

@app.route('/api/pause-download', methods=['POST'])
def api_pause_download():
    """暂停当前下载任务"""
    global current_downloader
    
    try:
        with current_downloader_lock:
            if current_downloader:
                current_downloader.pause()
                
                # 暂停时保存进度
                if hasattr(current_downloader, '_save_progress'):
                    try:
                        current_downloader._save_progress()
                        logger.info("暂停下载时保存进度")
                    except Exception as e:
                        logger.error(f"保存进度失败: {e}")
                
                logger.info("下载任务已暂停")
                return jsonify({'success': True, 'message': '下载已暂停'})
            return jsonify({'success': False, 'message': '没有正在进行的下载任务'})
    except Exception as e:
        logger.error(f"暂停下载时发生异常: {e}")
        return jsonify({'success': False, 'message': f'暂停下载时发生异常: {str(e)}'})

@app.route('/api/resume-download', methods=['POST'])
def api_resume_download():
    """继续当前下载任务"""
    global current_downloader
    
    try:
        with current_downloader_lock:
            if current_downloader:
                current_downloader.resume()
                logger.info("下载任务已恢复")
                return jsonify({'success': True, 'message': '下载已恢复'})
            return jsonify({'success': False, 'message': '没有正在进行的下载任务'})
    except Exception as e:
        logger.error(f"恢复下载时发生异常: {e}")
        return jsonify({'success': False, 'message': f'恢复下载时发生异常: {str(e)}'})

@app.route('/api/download-status', methods=['GET'])
def api_download_status():
    """获取当前下载状态"""
    global current_downloader
    
    with current_downloader_lock:
        if current_downloader:
            return jsonify({
                'success': True,
                'is_downloading': True,
                'is_paused': current_downloader.is_paused(),
                'statistics': current_downloader.get_statistics()
            })
        return jsonify({
            'success': True,
            'is_downloading': False,
            'is_paused': False,
            'statistics': {'downloaded': 0, 'failed': 0, 'skipped': 0, 'total': 0}
        })

@app.route('/api/config/save', methods=['POST'])
def api_save_config():
    """
    保存参数配置
    """
    try:
        if not request.is_json:
            return jsonify({'error': 'Request must be JSON format'}), 400
        
        data = request.get_json()
        
        # 验证必填参数
        if 'config_name' not in data:
            return jsonify({'error': 'Missing required field: config_name'}), 400
        
        config_name = data['config_name']
        config_data = data.get('config_data', {})
        
        # 生成配置文件名
        config_filename = f"{config_name}.json"
        config_path = os.path.join(app.config['CONFIG_FOLDER'], config_filename)
        
        # 保存配置数据
        config = {
            'name': config_name,
            'timestamp': time.time(),
            'data': config_data
        }
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        
        logger.info(f"配置已保存: {config_name}")
        return jsonify({'success': True, 'message': '配置保存成功'})
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        return jsonify({'error': f'Failed to save config: {e}'}), 500

@app.route('/api/config/list', methods=['GET'])
def api_list_configs():
    """
    获取配置列表
    """
    try:
        configs = []
        config_folder = app.config['CONFIG_FOLDER']
        
        if os.path.exists(config_folder):
            for filename in os.listdir(config_folder):
                if filename.endswith('.json'):
                    config_path = os.path.join(config_folder, filename)
                    try:
                        with open(config_path, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                        configs.append({
                            'name': config.get('name', filename[:-5]),
                            'timestamp': config.get('timestamp', 0),
                            'filename': filename
                        })
                    except Exception as e:
                        logger.error(f"读取配置文件失败: {filename}, {e}")
        
        # 按时间戳排序，最新的在前
        configs.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({'success': True, 'configs': configs})
    except Exception as e:
        logger.error(f"获取配置列表失败: {e}")
        return jsonify({'error': f'Failed to get config list: {e}'}), 500

@app.route('/api/config/load/<config_name>', methods=['GET'])
def api_load_config(config_name):
    """
    加载指定配置
    """
    try:
        config_filename = f"{config_name}.json"
        config_path = os.path.join(app.config['CONFIG_FOLDER'], config_filename)
        
        if not os.path.exists(config_path):
            return jsonify({'error': 'Config not found'}), 404
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        logger.info(f"配置已加载: {config_name}")
        return jsonify({'success': True, 'config': config})
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        return jsonify({'error': f'Failed to load config: {e}'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
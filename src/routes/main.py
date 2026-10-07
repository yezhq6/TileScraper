# src/routes/main.py

"""Flask 路由层。

路由只做 HTTP 层的解析与响应，下载状态由 :class:`DownloadController` 统一管理。
"""

from flask import Blueprint, Response, jsonify, render_template, request
from loguru import logger

from ..config import config_manager
from ..downloader.controller import DownloadController
from ..providers import ProviderManager

main_bp = Blueprint('main', __name__)

# 全局唯一的下载控制器（单进程单任务模型）
controller = DownloadController()


@main_bp.route('/')
def index():
    """首页。"""
    return render_template('index.html')


@main_bp.route('/api/health')
def api_health():
    """健康检查端点，供生产环境/负载均衡探活使用。"""
    return jsonify({'status': 'ok', 'downloading': controller.status()['is_downloading']})


@main_bp.route('/api/download', methods=['POST'])
def api_download():
    """启动下载任务。"""
    data = request.get_json(silent=True) or {}
    try:
        result = controller.start(data)
        return jsonify(result)
    except ValueError as e:
        logger.warning(f"下载参数校验失败: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:  # noqa: BLE001
        logger.error(f"下载请求处理失败: {e}")
        return jsonify({'error': f'Failed to start download: {e}'}), 500


@main_bp.route('/api/providers')
def api_providers():
    """获取支持的瓦片提供商及其信息。"""
    return jsonify({
        'success': True,
        'providers': ProviderManager.get_all_providers_info(),
        'names': ProviderManager.list_providers(),
    })


@main_bp.route('/api/progress')
def api_progress():
    """SSE 端点，用于推送下载进度。"""
    return Response(
        controller.progress_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


@main_bp.route('/api/pause-download', methods=['POST'])
def api_pause_download():
    """暂停下载。"""
    return jsonify(controller.pause())


@main_bp.route('/api/resume-download', methods=['POST'])
def api_resume_download():
    """恢复下载。"""
    return jsonify(controller.resume())


@main_bp.route('/api/cancel-download', methods=['POST'])
def api_cancel_download():
    """取消下载。"""
    return jsonify(controller.cancel())


@main_bp.route('/api/download-status')
def api_download_status():
    """获取当前下载状态。"""
    return jsonify(controller.status())


@main_bp.route('/api/download-params')
def api_download_params():
    """获取当前下载参数。"""
    return jsonify(controller.params())


# ---------------------------------------------------------------------- #
# 配置管理
# ---------------------------------------------------------------------- #
@main_bp.route('/api/config/list')
def api_config_list():
    """列出所有配置。"""
    try:
        return jsonify({'success': True, 'configs': config_manager.list_configs()})
    except Exception as e:  # noqa: BLE001
        logger.error(f"列出配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)})


@main_bp.route('/api/config/save', methods=['POST'])
def api_config_save():
    """保存配置。"""
    try:
        data = request.get_json(silent=True) or {}
        config_name = data.get('config_name')
        config_data = data.get('config_data')
        if not config_name or not config_data:
            return jsonify({'success': False, 'error': '缺少配置名称或配置数据'})
        if config_manager.save_config(config_name, config_data):
            return jsonify({'success': True, 'message': '配置保存成功'})
        return jsonify({'success': False, 'error': '配置保存失败'})
    except Exception as e:  # noqa: BLE001
        logger.error(f"保存配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)})


@main_bp.route('/api/config/load/<config_name>')
def api_config_load(config_name):
    """加载配置。"""
    try:
        config_data = config_manager.load_config(config_name)
        if not config_data:
            return jsonify({'success': False, 'error': '配置不存在'})

        # 兼容历史数据中可能存在的双层 data 嵌套
        actual = config_data
        for _ in range(2):
            if isinstance(actual, dict) and isinstance(actual.get('data'), dict):
                actual = actual['data']
            else:
                break
        return jsonify({'success': True, 'config': {'name': config_name, 'data': actual}})
    except Exception as e:  # noqa: BLE001
        logger.error(f"加载配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)})


@main_bp.route('/api/config/delete/<config_name>', methods=['POST', 'DELETE'])
def api_config_delete(config_name):
    """删除指定配置。"""
    try:
        if config_manager.delete_config(config_name):
            return jsonify({'success': True, 'message': '配置已删除'})
        return jsonify({'success': False, 'error': '配置不存在或名称非法'})
    except Exception as e:  # noqa: BLE001
        logger.error(f"删除配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)})

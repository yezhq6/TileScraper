# src/routes/main.py

"""Flask 路由层。

路由只做 HTTP 层的解析与响应，下载状态由 :class:`DownloadController` 统一管理。
"""

import functools
import hmac
from urllib.parse import urlsplit

from flask import Blueprint, Response, jsonify, render_template, request
from loguru import logger

from ..config import config_manager
from ..downloader.controller import DownloadController
from ..exceptions import OutputLockedError
from ..providers import ProviderManager

main_bp = Blueprint('main', __name__)

# 全局唯一的下载控制器（单进程单任务模型）
controller = DownloadController()

# 回给客户端的通用错误文案：详细异常（可能含路径/SQL 细节）只写服务端日志
_GENERIC_ERROR = '服务器内部错误，请查看服务端日志（tilescraper.log）'

# 免鉴权路径：健康检查给探活用；首页只是静态外壳，不含数据
_TOKEN_EXEMPT_PATHS = {'/api/health'}


def _configured_token() -> str:
    """读取配置里的访问令牌；空字符串表示不启用鉴权（默认）。"""
    return str(config_manager.get('server.api_token', '') or '').strip()


@main_bp.before_request
def _require_api_token():
    """
    可选的访问令牌鉴权（默认关闭，``server.api_token: ""``）。

    打开的动机：本服务默认监听 0.0.0.0 且无任何认证，任何能访问端口的人都能
    发起下载、把数据写到任意可写路径。设置 token 后所有 ``/api/*`` 都要求
    ``X-API-Token: <token>`` 或 ``Authorization: Bearer <token>``。

    注意：浏览器原生 ``EventSource`` 无法自定义请求头，因此启用 token 时前端
    会自动改用轮询（见 ``core.js.startProgressListener``），而不是把 token
    放进 URL（那样会出现在访问日志里）。
    """
    token = _configured_token()
    if not token or not request.path.startswith('/api/'):
        return None
    if request.path in _TOKEN_EXEMPT_PATHS:
        return None

    supplied = request.headers.get('X-API-Token', '')
    if not supplied:
        auth = request.headers.get('Authorization', '')
        if auth.lower().startswith('bearer '):
            supplied = auth[7:].strip()
    if supplied and hmac.compare_digest(supplied, token):
        return None

    logger.warning(f"拒绝未授权访问 {request.method} {request.path}")
    return jsonify({
        'success': False,
        'error': '未授权：请提供 X-API-Token 或 Authorization: Bearer <token>',
    }), 401


def _reject_cross_site():
    """
    跨站请求保护（不依赖 token 的轻量方案）。

    判定顺序：

    1. ``Sec-Fetch-Site: cross-site`` → 拒绝（现代浏览器都会带这个头，
       且页面无法伪造它）；
    2. ``Sec-Fetch-Site`` 是 same-origin / same-site / none → 放行
       （反向代理改了 Host 也不会误伤）；
    3. 没有该头时退回比较 ``Origin`` 的主机名与请求 Host，
       命中 ``server.trusted_origins`` 也放行；
    4. 两个头都没有（curl / 脚本 / 服务间调用）→ 放行，保证 API 仍可程序化调用。

    Returns:
        None 放行；否则返回 ``(response, status)``。
    """
    site = (request.headers.get('Sec-Fetch-Site') or '').strip().lower()
    if site == 'cross-site':
        logger.warning(f"拒绝跨站 {request.method} {request.path}: Sec-Fetch-Site=cross-site")
        return jsonify({'success': False, 'error': '拒绝跨站请求'}), 403
    if site in ('same-origin', 'same-site', 'none'):
        return None

    origin = (request.headers.get('Origin') or '').strip()
    if origin and origin.lower() != 'null':
        try:
            origin_host = (urlsplit(origin).hostname or '').lower()
        except ValueError:
            origin_host = ''
        try:
            request_host = (urlsplit(f'//{request.host or ""}').hostname or '').lower()
        except ValueError:
            request_host = ''
        trusted = {
            str(item).strip().lower()
            for item in (config_manager.get('server.trusted_origins', []) or [])
        }
        if origin_host and origin_host != request_host and origin_host not in trusted:
            logger.warning(f"拒绝跨站 {request.method} {request.path}: Origin={origin}")
            return jsonify({'success': False, 'error': '拒绝跨站请求'}), 403
    return None


def protect_state_change(view):
    """
    装饰"会改变服务端状态"的端点。

    * 先做同源校验（见 :func:`_reject_cross_site`）；
    * POST/PUT/PATCH 还要求 ``Content-Type: application/json``：
      浏览器里跨站表单只能发 urlencoded / text-plain / multipart，
      发不出 JSON，因此这条能挡住"无 body 简单请求"型 CSRF
      （例如用一张图片或表单静默取消下载）。
      DELETE 无法由表单发起，只做同源校验，方便 `curl -X DELETE` 继续可用。
    """
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        rejection = _reject_cross_site()
        if rejection is not None:
            return rejection
        if request.method in ('POST', 'PUT', 'PATCH') and not request.is_json:
            return jsonify({
                'success': False,
                'error': '该接口要求 Content-Type: application/json',
            }), 415
        return view(*args, **kwargs)

    return wrapper


@main_bp.route('/')
def index():
    """首页。"""
    # 代理是环境级配置（config.yaml 的 download.proxy / 环境变量），
    # 页面上完全不出现，改配置后重启生效（启动日志会打印当前模式）。
    # 输出路径默认值同样来自配置（paths.default_output_dir）
    return render_template(
        'index.html',
        default_output_dir=config_manager.get('paths.default_output_dir', 'tiles_datasets'),
        api_auth_required=bool(_configured_token()),
    )


@main_bp.route('/api/health')
def api_health():
    """健康检查端点，供生产环境/负载均衡探活使用。"""
    return jsonify({'status': 'ok', 'downloading': controller.status()['is_downloading']})


def _internal_error(context: str, exc: Exception):
    """
    记录完整异常（含堆栈）到服务端日志，但**只回通用文案**给客户端。

    异常文本里常带本地路径、SQLite 报错、甚至配置内容，直接回显等于信息泄漏。
    """
    logger.opt(exception=True).error(f"{context}: {exc}")
    return jsonify({'success': False, 'error': _GENERIC_ERROR}), 500


@main_bp.route('/api/download', methods=['POST'])
@protect_state_change
def api_download():
    """启动下载任务。"""
    data = request.get_json(silent=True) or {}
    try:
        result = controller.start(data)
        return jsonify(result)
    except ValueError as e:
        logger.warning(f"下载参数校验失败: {e}")
        return jsonify({'error': str(e)}), 400
    except OutputLockedError as e:
        logger.warning(f"输出路径被占用: {e}")
        return jsonify({'error': str(e)}), 409
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).error(f"下载请求处理失败: {e}")
        return jsonify({'error': _GENERIC_ERROR}), 500


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
@protect_state_change
def api_pause_download():
    """暂停下载。"""
    return jsonify(controller.pause())


@main_bp.route('/api/resume-download', methods=['POST'])
@protect_state_change
def api_resume_download():
    """恢复下载。"""
    return jsonify(controller.resume())


@main_bp.route('/api/cancel-download', methods=['POST'])
@protect_state_change
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


@main_bp.route('/api/failed-tiles')
def api_failed_tiles():
    """列出当前任务输出目录里的失败瓦片（便于只补这些瓦片）。"""
    try:
        limit = int(request.args.get('limit', 1000))
    except (TypeError, ValueError):
        limit = 1000
    limit = max(1, min(limit, 10000))
    try:
        return jsonify(controller.failed_tiles(limit=limit))
    except Exception as e:  # noqa: BLE001
        return _internal_error('读取失败瓦片失败', e)


# ---------------------------------------------------------------------- #
# 配置管理
# ---------------------------------------------------------------------- #
@main_bp.route('/api/config/list')
def api_config_list():
    """列出所有配置。"""
    try:
        return jsonify({'success': True, 'configs': config_manager.list_configs()})
    except Exception as e:  # noqa: BLE001
        return _internal_error('列出配置失败', e)


@main_bp.route('/api/config/save', methods=['POST'])
@protect_state_change
def api_config_save():
    """保存配置。"""
    try:
        data = request.get_json(silent=True) or {}
        config_name = data.get('config_name')
        config_data = data.get('config_data')
        if not config_name or not config_data:
            return jsonify({'success': False, 'error': '缺少配置名称或配置数据'})
        if not config_manager.is_valid_config_name(config_name):
            logger.warning(f"拒绝非法配置名称: {config_name!r}")
            return jsonify({'success': False, 'error': '配置名称非法'}), 400
        if not isinstance(config_data, dict):
            return jsonify({'success': False, 'error': '配置数据必须是对象'}), 400
        if config_manager.save_config(config_name, config_data):
            return jsonify({'success': True, 'message': '配置保存成功'})
        return jsonify({'success': False, 'error': '配置保存失败'})
    except Exception as e:  # noqa: BLE001
        return _internal_error('保存配置失败', e)


@main_bp.route('/api/config/load/<config_name>')
def api_config_load(config_name):
    """加载配置。"""
    try:
        if not config_manager.is_valid_config_name(config_name):
            return jsonify({'success': False, 'error': '配置名称非法'}), 400
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
        return _internal_error('加载配置失败', e)


@main_bp.route('/api/config/delete/<config_name>', methods=['POST', 'DELETE'])
@protect_state_change
def api_config_delete(config_name):
    """删除指定配置。"""
    try:
        if config_manager.delete_config(config_name):
            return jsonify({'success': True, 'message': '配置已删除'})
        return jsonify({'success': False, 'error': '配置不存在或名称非法'})
    except Exception as e:  # noqa: BLE001
        return _internal_error('删除配置失败', e)

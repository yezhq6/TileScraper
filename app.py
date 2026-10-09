# app.py

import os
import signal
import threading

from flask import Flask
from loguru import logger

from src.config import config_manager
from src.routes.main import main_bp

def _configure_logging():
    """按 config.yaml 的 logging 段配置文件日志（以前 format/rotation 是硬编码的）。"""
    logging_config = config_manager.get_logging_config()
    options = {
        "rotation": logging_config.get("rotation", "500 MB"),
        "level": logging_config.get("level", "INFO"),
    }
    fmt = logging_config.get("format") or ""
    if fmt:
        if "%(" in fmt:
            # 旧版写的是 stdlib logging 风格，loguru 不认识，忽略以免整行变成字面量
            logger.warning(
                "logging.format 是 stdlib 风格（含 %(...)s），loguru 不支持，已忽略"
            )
        else:
            options["format"] = fmt
    logger.add(logging_config.get("file", "tilescraper.log"), **options)


# 配置日志
_configure_logging()

# 创建 Flask 应用
app = Flask(__name__)
app.register_blueprint(main_bp)


def _install_graceful_shutdown():
    """
    在主线程注册 SIGINT / SIGTERM：先优雅停止下载，再退出进程。

    Web 场景下下载器是在 ``DownloadSession`` 线程里构造的，``signal.signal``
    无法在非主线程注册（见 ``SignalHandler``），因此必须在这里补上；
    否则 Ctrl-C 会直接杀掉进程，丢掉尚未保存的进度与 MBTiles 事务。
    """
    if threading.current_thread() is not threading.main_thread():
        return

    def handler(signum, frame):
        logger.info(f"收到信号 {signum}，正在优雅停止服务与下载任务...")
        try:
            from src.routes.main import controller

            controller.stop(timeout=30)
        except Exception as e:  # noqa: BLE001
            logger.error(f"优雅停止下载失败: {e}")
        raise SystemExit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError) as e:  # noqa: BLE001
            logger.warning(f"注册信号 {sig} 处理失败: {e}")


def get_production_server():
    """
    返回 waitress 的 ``serve`` 函数；未安装 waitress 时返回 None。

    这样可以在不强制安装依赖的前提下，按需切换生产服务器。
    """
    try:
        from waitress import serve
        return serve
    except ImportError:
        return None


def run(host=None, port=None, debug=None, production=False):
    """启动 Web 服务（默认开发服务器，production=True 时优先使用 waitress）。"""
    server_config = config_manager.get_server_config()
    host = host or server_config.get("host", "0.0.0.0")
    port = port or server_config.get("port", 5000)
    if debug is None:
        debug = server_config.get("debug", False)

    _install_graceful_shutdown()

    # 代理只在 config.yaml / 环境变量里配置，页面上不再展示；
    # 启动时把当前生效模式打进日志，避免"配了但不知道生没生效"。
    from src.downloader.request import describe_proxy

    logger.info(
        "下载代理模式: "
        f"{describe_proxy(config_manager.get('download.proxy', ''))}"
        "（修改 config.yaml 的 download.proxy 或环境变量 "
        "TILESCRAPER_DOWNLOAD_PROXY 后需重启生效）"
    )

    if not str(config_manager.get("server.api_token", "") or "").strip():
        if host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                f"未设置 server.api_token，且监听 {host}（非本机地址）："
                "任何能访问该端口的人都能发起下载、把数据写到任意可写路径。"
                "建议设置 api_token，或把 server.host 改回 127.0.0.1。"
            )

    if production:
        serve = get_production_server()
        if serve is not None:
            logger.info(f"以生产模式(waitress)启动服务: {host}:{port}")
            serve(app, host=host, port=port, threads=8)
            return
        logger.warning("未安装 waitress，回退到 Flask 开发服务器；建议执行 pip install waitress")

    logger.info(f"启动 Web 服务器(开发模式): {host}:{port}")
    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    production_mode = os.environ.get("TILESCRAPER_PROD", "").lower() in ("1", "true", "yes")
    run(production=production_mode)

# app.py

import os

from flask import Flask
from loguru import logger

from src.config import config_manager
from src.routes.main import main_bp

# 配置日志
logging_config = config_manager.get_logging_config()
logger.add(
    logging_config.get("file", "tilescraper.log"),
    rotation="500 MB",
    level=logging_config.get("level", "INFO"),
)

# 创建 Flask 应用
app = Flask(__name__)
app.register_blueprint(main_bp)


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

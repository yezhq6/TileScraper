# app.py

import threading
import queue
from flask import Flask
from loguru import logger
from src.routes.main import main_bp, init_globals
from src.config import config_manager

# 配置日志
logging_config = config_manager.get_logging_config()
logger.add(logging_config.get("file", "tileharvester.log"), rotation="500 MB", level=logging_config.get("level", "INFO"))

# 创建Flask应用
app = Flask(__name__)

# 初始化全局变量
downloader_lock = threading.Lock()
progress_queue = queue.Queue()
init_globals(downloader_lock, progress_queue)

# 注册蓝图
app.register_blueprint(main_bp)

if __name__ == '__main__':
    # 从配置文件获取服务器配置
    server_config = config_manager.get_server_config()
    host = server_config.get("host", "0.0.0.0")
    port = server_config.get("port", 5000)
    debug = server_config.get("debug", False)
    
    logger.info(f"启动TileHarvester Web服务器: {host}:{port}")
    app.run(host=host, port=port, debug=debug)

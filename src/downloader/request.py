# src/downloader/request.py

import requests
from loguru import logger


class RequestSessionManager:
    """
    请求会话管理器：负责创建和管理HTTP请求会话
    """

    def __init__(self):
        """
        初始化请求会话管理器
        """
        self.session = None

    def create_session(self):
        """
        创建并配置请求会话，增强错误处理和网络性能
        
        Returns:
            requests.Session: 配置好的请求会话
        """
        session = requests.Session()
        
        # 优化请求头
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'image/*',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })
        
        # 禁用代理，提高连接速度
        session.proxies = {'http': None, 'https': None}
        
        # 禁用重定向跟随，减少不必要的网络请求
        session.max_redirects = 3
        
        # 配置连接超时
        session.timeout = 5  # 减少超时时间，提高响应速度
        
        # 启用连接池，优化参数
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1000,  # 进一步增加连接池大小
            pool_maxsize=1000,      # 进一步增加最大连接数
            pool_block=False,      # 非阻塞模式，避免连接池满时阻塞
            max_retries=requests.adapters.Retry(
                total=2,            # 减少重试次数，提高响应速度
                backoff_factor=0.3,  # 减少退避因子
                status_forcelist=[429, 500, 502, 503, 504],  # 需要重试的状态码
                allowed_methods=["GET"]  # 只对GET请求重试
            )
        )
        
        # 挂载适配器
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        logger.debug("创建新的请求会话，配置连接池和重试机制")
        self.session = session
        return session

    def get_session(self):
        """
        获取请求会话，如果不存在则创建
        
        Returns:
            requests.Session: 请求会话
        """
        if not self.session:
            return self.create_session()
        return self.session

    def close(self):
        """
        关闭请求会话
        """
        if self.session:
            try:
                self.session.close()
                logger.debug("关闭请求会话")
            except Exception as e:
                logger.error(f"关闭请求会话失败: {e}")
            self.session = None

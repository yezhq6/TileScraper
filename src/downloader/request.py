# src/downloader/request.py

from urllib.parse import urlsplit

import requests
from loguru import logger

from ..config import config_manager


def describe_proxy(value) -> str:
    """
    把 ``download.proxy`` 的取值描述成一句人话，用于日志与页面提示。

    只暴露"模式"和主机名，**不回显用户名/密码**（代理 URL 里可能带认证信息）。
    """
    text = (value or "").strip()
    if not text:
        return "直连（忽略 HTTP_PROXY/HTTPS_PROXY）"
    if text.lower() == "env":
        return "跟随环境变量 HTTP_PROXY/HTTPS_PROXY"
    try:
        parts = urlsplit(text)
    except ValueError:
        return "自定义代理（已配置）"
    if not parts.hostname:
        return "自定义代理（已配置）"
    port = f":{parts.port}" if parts.port else ""
    auth = "，含认证信息" if (parts.username or parts.password) else ""
    return f"自定义代理 {parts.scheme}://{parts.hostname}{port}{auth}"


class RequestSessionManager:
    """
    请求会话管理器：负责创建和管理 HTTP 请求会话。

    代理策略由配置 ``download.proxy`` 决定：

    * 留空（默认）——直连，**忽略** HTTP_PROXY / HTTPS_PROXY 等环境变量；
    * ``env``——沿用系统环境变量里的代理；
    * 其它值——作为代理地址使用，例如 ``http://127.0.0.1:7890``。

    历史坑：旧代码写 ``session.proxies = {'http': None, 'https': None}``
    想"禁用代理"，但 requests 会在合并时把值为 ``None`` 的键直接删掉，
    随后仍会读取环境变量里的代理，因此**代理其实没有被禁用**。
    真正关闭环境变量代理要靠 ``session.trust_env = False``。
    """

    def __init__(self, proxy=None):
        """
        初始化请求会话管理器

        Args:
            proxy: 覆盖配置的代理设置；None 表示使用 ``download.proxy``
        """
        self.session = None
        self.proxy = proxy

    def _effective_proxy(self) -> str:
        """解析当前生效的代理配置，空字符串表示直连。"""
        value = self.proxy
        if value is None:
            value = config_manager.get("download.proxy", "")
        return (value or "").strip()

    def create_session(self):
        """
        创建并配置请求会话，增强错误处理和网络性能。

        Returns:
            requests.Session: 配置好的请求会话
        """
        # 重建会话时先关掉旧连接，避免句柄泄漏
        self.close()
        proxy = self._effective_proxy()

        session = requests.Session()

        # 优化请求头
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'image/*',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })

        # 代理设置：是否走代理完全由配置决定，不再隐式使用环境变量
        if not proxy:
            session.trust_env = False
            session.proxies = {}
        elif proxy.lower() == "env":
            session.trust_env = True
        else:
            session.trust_env = False
            session.proxies = {"http": proxy, "https": proxy}

        # 限制重定向跳转次数（不是"禁用重定向"）
        session.max_redirects = 3

        # 启用连接池，优化参数
        adapter = requests.adapters.HTTPAdapter(
            # 每个工作线程持有自己的 session，单线程串行复用少量连接即可
            pool_connections=8,
            pool_maxsize=8,
            pool_block=True,
            # 重试统一交给 worker 的重试循环（带指数退避 + 永久错误判定），
            # 这里不再叠加 adapter 级重试，避免一次失败被放大成成倍的请求。
            max_retries=requests.adapters.Retry(total=0, connect=0, read=0),
        )

        # 挂载适配器
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        logger.debug(f"创建新的请求会话（proxy={proxy or '直连'}）")
        self.session = session
        return session

    def recreate(self):
        """请求失败后重建会话（会先关闭旧连接）。"""
        try:
            return self.create_session()
        except Exception as e:  # noqa: BLE001
            logger.error(f"重建请求会话失败: {e}")
            return self.session

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

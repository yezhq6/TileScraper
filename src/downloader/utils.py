# src/downloader/utils.py

import os
from pathlib import Path
from loguru import logger

from ..config import config_manager


def resolve_thread_count(requested=None) -> int:
    """
    把"请求的线程数"解析成实际使用的线程数。

    * ``requested`` 为 None 时取配置 ``download.threads``；
    * 取到的值若为 "" / "auto" / 0，则自动选择：
      ``clamp(CPU核数 * 4, threads_auto_min, threads_auto_max)``；
    * 其余情况按给定数字使用；
    * 最终统一被 ``download.max_threads_hard_limit`` 截断。
    """
    hard_limit = int(config_manager.get("download.max_threads_hard_limit", 128) or 128)
    cpu_cores = os.cpu_count() or 4

    if requested is None:
        requested = config_manager.get("download.threads", None)

    is_auto = requested is None or (
        isinstance(requested, str) and requested.strip().lower() in ("", "auto")
    ) or (
        isinstance(requested, (int, float)) and not isinstance(requested, bool)
        and int(requested) <= 0
    )

    if is_auto:
        low = int(config_manager.get("download.threads_auto_min", 8) or 8)
        high = int(config_manager.get("download.threads_auto_max", 32) or 32)
        if low > high:
            low, high = high, low
        value = min(high, max(low, cpu_cores * 4))
    else:
        try:
            value = int(requested)
        except (TypeError, ValueError):
            logger.warning(f"无法解析线程数 {requested!r}，回退到默认值")
            value = int(config_manager.get("download.threads", 16) or 16)

    return max(1, min(value, hard_limit))


def is_auto_thread_count(requested=None) -> bool:
    """判断给定值是否表示"自动选择线程数"。"""
    if requested is None:
        return True
    if isinstance(requested, str):
        return requested.strip().lower() in ("", "auto")
    if isinstance(requested, bool):
        return False
    return isinstance(requested, (int, float)) and int(requested) <= 0


# 路径转换函数：处理Windows路径和Linux路径
def convert_path(output_dir: str) -> Path:
    """
    转换路径，支持Windows路径和Linux路径，在WSL2环境中自动转换
    
    Args:
        output_dir: 输入的路径，可以是Windows路径（如D:/codes）或Linux路径（如/mnt/d/codes）
        
    Returns:
        Path: 转换后的Path对象
    """
    # 处理命令行中可能的空格和换行问题
    output_dir = output_dir.strip()
    
    # 1. 检查是否为WSL2路径（以/mnt/开头）
    if output_dir.startswith('/mnt/'):
        return Path(output_dir)
    
    # 2. 检查是否为Windows路径（包含盘符）
    elif len(output_dir) > 1 and output_dir[1] == ':':
        # 转换Windows路径到WSL2路径
        # 将盘符转换为/mnt/[小写盘符]
        drive_letter = output_dir[0].lower()
        
        # 处理路径中的反斜杠
        # 替换反斜杠为正斜杠
        wsl_path = output_dir[2:].replace('\\', '/')
        
        # 构建完整的WSL路径
        full_path = f"/mnt/{drive_letter}/{wsl_path.lstrip('/')}"
        logger.info(f"转换Windows路径到WSL2路径: {output_dir} -> {full_path}")
        return Path(full_path)
    
    # 3. 其他情况，直接返回
    return Path(output_dir)


def ensure_directory(directory: Path):
    """
    确保目录存在，不存在则创建
    
    Args:
        directory: 目录路径
    """
    directory.mkdir(parents=True, exist_ok=True)


def get_file_size(file_path: Path) -> int:
    """
    获取文件大小
    
    Args:
        file_path: 文件路径
    
    Returns:
        int: 文件大小（字节）
    """
    if file_path.exists():
        return file_path.stat().st_size
    return 0

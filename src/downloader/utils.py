# src/downloader/utils.py

import os
from pathlib import Path
from loguru import logger


# 路径转换函数：处理Windows路径和Linux路径
def convert_path(output_dir: str) -> Path:
    """
    转换路径，支持Windows路径和Linux路径，在WSL2环境中自动转换
    
    Args:
        output_dir: 输入的路径，可以是Windows路径（如D:/codes）或Linux路径（如/mnt/d/codes）
        
    Returns:
        Path: 转换后的Path对象
    """
    # 检查是否为Windows路径（包含盘符和反斜杠）
    if len(output_dir) > 1 and output_dir[1] == ':' and ('\\' in output_dir or '/' in output_dir):
        # 转换Windows路径到WSL2路径
        # 将盘符转换为/mnt/[小写盘符]
        drive_letter = output_dir[0].lower()
        # 替换反斜杠为正斜杠
        wsl_path = output_dir[2:].replace('\\', '/')
        # 构建完整的WSL路径
        full_path = f"/mnt/{drive_letter}/{wsl_path.lstrip('/')}"
        logger.info(f"转换Windows路径到WSL2路径: {output_dir} -> {full_path}")
        return Path(full_path)
    else:
        # 直接返回Linux路径
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

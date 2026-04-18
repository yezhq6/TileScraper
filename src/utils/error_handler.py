# src/utils/error_handler.py

import traceback
from loguru import logger
from typing import Callable, Optional, Type, Any
from ..exceptions import TileHarvesterError

def handle_error(
    error_types: Optional[Type[Exception] | tuple[Type[Exception], ...]] = None,
    default_return: Any = None
) -> Callable:
    """
    错误处理装饰器
    
    Args:
        error_types: 要捕获的错误类型
        default_return: 发生错误时的默认返回值
    
    Returns:
        Callable: 装饰后的函数
    """
    if error_types is None:
        error_types = Exception
    
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except error_types as e:
                # 记录错误信息
                logger.error(f"{func.__name__} 执行失败: {e}")
                logger.debug(traceback.format_exc())
                # 返回默认值
                return default_return
        return wrapper
    return decorator

def handle_tileharvester_error(func: Callable) -> Callable:
    """
    处理 TileHarvester 特定错误的装饰器
    
    Args:
        func: 要装饰的函数
    
    Returns:
        Callable: 装饰后的函数
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except TileHarvesterError as e:
            logger.error(f"TileHarvester 错误: {e}")
            logger.debug(traceback.format_exc())
            return None
        except Exception as e:
            logger.error(f"未预期的错误: {e}")
            logger.debug(traceback.format_exc())
            return None
    return wrapper

def safe_execute(
    func: Callable,
    *args,
    **kwargs
) -> tuple[bool, Any]:
    """
    安全执行函数，返回执行结果和是否成功
    
    Args:
        func: 要执行的函数
        *args: 函数参数
        **kwargs: 函数关键字参数
    
    Returns:
        tuple[bool, Any]: (是否成功, 执行结果)
    """
    try:
        result = func(*args, **kwargs)
        return True, result
    except Exception as e:
        logger.error(f"执行函数 {func.__name__} 失败: {e}")
        logger.debug(traceback.format_exc())
        return False, None

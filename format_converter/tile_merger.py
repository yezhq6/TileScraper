#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
瓦片数据合并工具
支持多线程合并两个路径的瓦片数据
将源路径的瓦片合并到目标路径
支持覆盖或跳过已有的图片
"""

import os
import argparse
import shutil
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import sys
from typing import Generator, Tuple

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

# 确保日志目录存在
log_dir = 'logs'
os.makedirs(log_dir, exist_ok=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'tile_merger.log'), encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def merge_tile(source_path: str, target_path: str, overwrite: bool = False, debug: bool = False) -> Tuple[bool, str, str]:
    """
    合并单个瓦片文件
    
    Args:
        source_path: 源瓦片文件路径
        target_path: 目标瓦片文件路径
        overwrite: 是否覆盖已存在的文件
        debug: 是否打印详细信息
    
    Returns:
        Tuple[bool, str, str]: (是否成功, 操作类型, 错误信息)
    """
    try:
        # 检查目标文件是否存在
        target_exists = os.path.exists(target_path)
        if target_exists and not overwrite:
            # 跳过已存在的文件，这是最快的操作
            if debug:
                print(f"~ 跳过已存在: {target_path}")
            return False, "skip", ""
        
        # 确保目标目录存在，只在需要时创建
        # 预先检查目录是否存在，避免不必要的目录创建开销
        target_dir = os.path.dirname(target_path)
        if not os.path.exists(target_dir):
            try:
                Path(target_dir).mkdir(parents=True, exist_ok=True)
            except Exception as dir_error:
                error_msg = f"创建目录失败: {dir_error}"
                if debug:
                    print(f"✗ {error_msg}")
                return False, "error", error_msg
        
        # 检查源文件是否存在
        if not os.path.exists(source_path):
            error_msg = f"源文件不存在: {source_path}"
            if debug:
                print(f"✗ {error_msg}")
            return False, "error", error_msg
        
        # 检查源文件是否为文件
        if not os.path.isfile(source_path):
            error_msg = f"源路径不是文件: {source_path}"
            if debug:
                print(f"✗ {error_msg}")
            return False, "error", error_msg
        
        # 使用更高效的文件复制方法
        # shutil.copy2会复制所有元数据，使用copyfile更快
        try:
            shutil.copyfile(source_path, target_path)
        except Exception as copy_error:
            error_msg = f"文件复制失败: {copy_error}"
            if debug:
                print(f"✗ {error_msg}")
            return False, "error", error_msg
        
        if debug:
            if target_exists:
                print(f"✓ 覆盖成功: {source_path} -> {target_path}")
            else:
                print(f"✓ 复制成功: {source_path} -> {target_path}")
        
        return True, "overwrite" if target_exists else "copy", ""
    except Exception as e:
        error_msg = f"未知错误: {str(e)}"
        if debug:
            print(f"✗ 合并失败: {source_path} -> {target_path}")
            print(f"   错误信息: {error_msg}")
        return False, "error", error_msg


def process_task(task_with_debug):
    """
    处理单个合并任务的辅助函数，用于进程池
    
    Args:
        task_with_debug: 合并任务和debug标志 (source_path, target_path, overwrite, debug)
    
    Returns:
        Tuple[bool, str, str]: 合并结果
    """
    return merge_tile(*task_with_debug)


def generate_merge_tasks(source_dir: str, target_dir: str, overwrite: bool = False) -> Generator[Tuple[str, str, bool], None, int]:
    """
    生成合并任务的生成器，避免一次性加载所有任务到内存
    
    Args:
        source_dir: 源瓦片目录
        target_dir: 目标瓦片目录
        overwrite: 是否覆盖已存在的文件
    
    Yields:
        Tuple[str, str, bool]: 合并任务 (source_path, target_path, overwrite)
    
    Returns:
        int: 总任务数
    """
    # 支持的瓦片格式
    supported_formats = {'.jpg', '.jpeg', '.png', '.webp'}
    total = 0
    
    # 遍历源目录，生成任务
    for root, dirs, files in os.walk(source_dir):
        # 计算相对路径，保持目录结构
        rel_path = os.path.relpath(root, source_dir)
        current_target_dir = os.path.join(target_dir, rel_path)
        
        # 处理当前目录的文件
        for file in files:
            # 检查文件格式是否支持
            file_ext = os.path.splitext(file)[1].lower()
            if file_ext in supported_formats:
                source_path = os.path.join(root, file)
                target_path = os.path.join(current_target_dir, file)
                total += 1
                yield source_path, target_path, overwrite
    
    return total


def get_optimal_threads() -> int:
    """
    获取最优线程数，对于IO密集型任务，使用更多线程
    
    Returns:
        int: 最优线程数
    """
    import multiprocessing
    cpu_cores = multiprocessing.cpu_count()
    # 对于IO密集型任务，使用CPU核心数的4-8倍
    return min(cpu_cores * 8, 128)  # 限制最大线程数为128


def batch_merge(source_dir: str, target_dir: str, overwrite: bool = False, max_workers: int = None, debug: bool = False):
    """
    批量合并瓦片数据
    
    Args:
        source_dir: 源瓦片目录
        target_dir: 目标瓦片目录
        overwrite: 是否覆盖已存在的文件
        max_workers: 最大线程数，默认使用最优线程数
        debug: 是否打印详细的合并信息
    """
    import psutil
    
    # 转换路径
    source_dir = str(convert_path(source_dir))
    target_dir = str(convert_path(target_dir))
    
    # 验证源目录是否存在
    if not os.path.exists(source_dir):
        print(f"✗ 源目录不存在: {source_dir}")
        return
    
    # 验证源目录是否为目录
    if not os.path.isdir(source_dir):
        print(f"✗ 源路径不是目录: {source_dir}")
        return
    
    # 创建目标目录
    Path(target_dir).mkdir(parents=True, exist_ok=True)
    
    # 统计总任务数
    print("正在统计需要合并的瓦片文件数量...")
    start_task_gen = time.time()
    total_tasks = sum(1 for _ in generate_merge_tasks(source_dir, target_dir, overwrite))
    task_gen_time = time.time() - start_task_gen
    
    # 设置默认线程数，对于IO密集型任务，调整线程数
    if max_workers is None:
        # 对于大量文件，减少线程数，避免过多线程导致的调度开销
        cpu_cores = psutil.cpu_count()
        if total_tasks > 500000:
            # 数据量特别大，使用CPU核心数的2-4倍
            max_workers = min(cpu_cores * 4, 64)
        else:
            max_workers = get_optimal_threads()
    
    if total_tasks == 0:
        print("✗ 没有找到需要合并的瓦片文件")
        return
    
    print(f"✓ 总计 {total_tasks:,} 个文件，生成任务耗时: {task_gen_time:.2f}s")
    print(f"✓ 使用线程数: {max_workers}")
    print(f"✓ CPU核心数: {psutil.cpu_count()}")
    print(f"✓ 初始内存使用: {psutil.Process().memory_info().rss / 1024 / 1024:.1f} MB")
    
    start_time = time.time()
    
    # 使用线程池进行并行合并
    success_count = 0
    skipped_count = 0
    failed_count = 0
    processed_count = 0
    
    # 进度显示配置
    # 根据数据量调整进度显示频率，减少IO开销
    if total_tasks > 500000:
        # 超大数据量，每处理0.05%的任务或至少每2000个文件显示一次
        progress_interval = max(2000, total_tasks // 2000)
    elif total_tasks > 100000:
        # 大量数据，每处理0.1%的任务或至少每1000个文件显示一次
        progress_interval = max(1000, total_tasks // 1000)
    elif total_tasks > 10000:
        # 中等数据量，每处理0.5%的任务或至少每500个文件显示一次
        progress_interval = max(500, total_tasks // 200)
    else:
        # 少量数据，每处理1%的任务或至少每200个文件显示一次
        progress_interval = max(200, total_tasks // 100)
    
    # 内存监控间隔
    memory_monitor_interval = 5000
    
    # 强制刷新输出，确保用户能看到实时进度
    sys.stdout.reconfigure(line_buffering=True)
    
    print(f"\n开始处理任务...")
    print("=" * 60)
    
    # 使用线程池处理任务
    ExecutorClass = ThreadPoolExecutor
    print(f"✓ 使用线程池处理任务，线程数: {max_workers}")
    
    # 生成任务的生成器，避免一次性加载所有任务到内存
    tasks_generator = generate_merge_tasks(source_dir, target_dir, overwrite)
    
    # 批处理配置
    batch_size = 10000  # 每批处理10000个任务，更频繁地释放内存
    
    with ExecutorClass(max_workers=max_workers) as executor:
        while True:
            # 生成当前批次的任务
            batch_tasks = []
            for _ in range(batch_size):
                try:
                    task = next(tasks_generator)
                    # 添加debug标志到任务
                    batch_tasks.append((task[0], task[1], task[2], debug))
                except StopIteration:
                    break
            
            if not batch_tasks:
                break
            
            batch_size_actual = len(batch_tasks)
            
            # 显示批次信息
            print(f"\n处理批次: {processed_count + 1:,}-{processed_count + batch_size_actual:,}/{total_tasks:,} ({batch_size_actual:,}个文件)")
            
            # 使用map处理当前批次
            batch_start_time = time.time()
            
            try:
                for i, result in enumerate(executor.map(process_task, batch_tasks)):
                    success, op_type, error_msg = result
                    processed_count += 1
                    
                    if success:
                        success_count += 1
                    elif op_type == "skip":
                        skipped_count += 1
                    else:
                        failed_count += 1
                        # 只打印错误信息，不打印成功信息
                        print(f"✗ 合并失败: {batch_tasks[i][0]} -> {batch_tasks[i][1]}")
                        print(f"   错误信息: {error_msg}")
                    
                    # 每处理一定数量的任务显示一次进度
                    if processed_count % progress_interval == 0 or processed_count == total_tasks:
                        elapsed = time.time() - start_time
                        speed = processed_count / elapsed if elapsed > 0 else 0
                        remaining = (total_tasks - processed_count) / speed if speed > 0 else 0
                        progress = (processed_count / total_tasks) * 100
                        
                        # 内存监控
                        memory_usage = psutil.Process().memory_info().rss / 1024 / 1024
                        cpu_usage = psutil.cpu_percent(interval=0.1)
                        
                        print(f"\r" + " " * 120, end="")  # 清空当前行
                        print(f"\r✓ 进度: {processed_count:,}/{total_tasks:,} ({progress:.1f}%) "
                              f"耗时: {elapsed:.1f}s 剩余: {remaining:.1f}s "
                              f"速度: {speed:.0f}个/秒 "
                              f"成功: {success_count:,} 跳过: {skipped_count:,} 失败: {failed_count:,} "
                              f"内存: {memory_usage:.0f}MB CPU: {cpu_usage:.1f}%", end="", flush=True)
                    
                    # 定期显示内存使用情况
                    if processed_count % memory_monitor_interval == 0:
                        memory_usage = psutil.Process().memory_info().rss / 1024 / 1024
                        cpu_usage = psutil.cpu_percent(interval=0.1)
                        print(f"\n   系统状态: 内存使用 {memory_usage:.0f}MB, CPU使用率 {cpu_usage:.1f}%")
            except Exception as e:
                print(f"✗ 处理批次失败: {e}")
                failed_count += len(batch_tasks)
            finally:
                # 释放当前批次的内存
                del batch_tasks
                import gc
                gc.collect()
                
                # 显示批次处理时间
                batch_elapsed = time.time() - batch_start_time
                print(f"\n批次处理完成，耗时: {batch_elapsed:.2f}s")
    
    end_time = time.time()
    elapsed_time = end_time - start_time
    
    logger.info("\n✓ 合并完成！")
    logger.info(f"   总文件数: {total_tasks:,}")
    logger.info(f"   成功合并: {success_count:,}")
    logger.info(f"   跳过: {skipped_count:,}")
    logger.info(f"   失败: {failed_count:,}")
    logger.info(f"   耗时: {elapsed_time:.2f} 秒")
    logger.info(f"   平均速度: {total_tasks/elapsed_time:.2f} 个/秒")
    logger.info(f"   使用线程数: {max_workers}")


def main():
    """
    主函数，处理命令行参数
    """
    parser = argparse.ArgumentParser(description='瓦片数据合并工具，支持多线程合并两个路径的瓦片数据')
    
    # 必须参数
    parser.add_argument('-s', '--source', required=True, help='源瓦片目录路径')
    parser.add_argument('-t', '--target', required=True, help='目标瓦片目录路径')
    
    # 可选参数
    parser.add_argument('-o', '--overwrite', action='store_true', help='覆盖已存在的文件，默认跳过')
    parser.add_argument('-w', '--workers', type=int, default=None, help='最大线程数，默认使用CPU核心数的8倍（最大128）')
    parser.add_argument('-v', '--verbose', action='store_true', help='显示详细的合并信息，每100个文件输出一次详细日志')
    parser.add_argument('--debug', action='store_true', help='显示调试信息，每个文件输出一条日志（适合测试少量文件）')
    
    args = parser.parse_args()
    
    # 调整日志级别
    if args.debug:
        logger.setLevel(logging.DEBUG)
    elif args.verbose:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.INFO)
    
    # 执行批量合并
    batch_merge(args.source, args.target, args.overwrite, args.workers, args.debug)


if __name__ == "__main__":
    main()
# src/downloader/performance.py

import time
import statistics
import threading
from typing import Dict


class PerformanceMonitor:
    """
    性能监控器，用于跟踪下载性能指标
    """
    
    def __init__(self):
        self.start_time = time.time()
        self.download_times = []
        self.task_processing_times = []
        self.bytes_downloaded = 0
        self.tasks_completed = 0
        self.lock = threading.Lock()
    
    def record_download(self, duration: float, bytes_count: int):
        """
        记录下载性能
        """
        with self.lock:
            self.download_times.append(duration)
            self.bytes_downloaded += bytes_count
            self.tasks_completed += 1
    
    def record_task_processing(self, duration: float):
        """
        记录任务处理性能
        """
        with self.lock:
            self.task_processing_times.append(duration)
    
    def get_statistics(self) -> Dict:
        """
        获取性能统计信息
        """
        with self.lock:
            total_time = time.time() - self.start_time
            
            # 计算下载速度
            if total_time > 0:
                download_speed = self.bytes_downloaded / total_time / 1024 / 1024  # MB/s
            else:
                download_speed = 0
            
            # 计算任务处理速度
            if total_time > 0:
                task_speed = self.tasks_completed / total_time  # tasks/s
            else:
                task_speed = 0
            
            # 计算平均下载时间
            if self.download_times:
                avg_download_time = statistics.mean(self.download_times)
                min_download_time = min(self.download_times)
                max_download_time = max(self.download_times)
                if len(self.download_times) > 1:
                    std_download_time = statistics.stdev(self.download_times)
                else:
                    std_download_time = 0
            else:
                avg_download_time = 0
                min_download_time = 0
                max_download_time = 0
                std_download_time = 0
            
            # 计算平均任务处理时间
            if self.task_processing_times:
                avg_task_time = statistics.mean(self.task_processing_times)
                min_task_time = min(self.task_processing_times)
                max_task_time = max(self.task_processing_times)
                if len(self.task_processing_times) > 1:
                    std_task_time = statistics.stdev(self.task_processing_times)
                else:
                    std_task_time = 0
            else:
                avg_task_time = 0
                min_task_time = 0
                max_task_time = 0
                std_task_time = 0
            
            return {
                'total_time': total_time,
                'tasks_completed': self.tasks_completed,
                'bytes_downloaded': self.bytes_downloaded,
                'download_speed': download_speed,
                'task_speed': task_speed,
                'download_times': {
                    'average': avg_download_time,
                    'minimum': min_download_time,
                    'maximum': max_download_time,
                    'std_dev': std_download_time,
                    'count': len(self.download_times)
                },
                'task_processing_times': {
                    'average': avg_task_time,
                    'minimum': min_task_time,
                    'maximum': max_task_time,
                    'std_dev': std_task_time,
                    'count': len(self.task_processing_times)
                }
            }
    
    def log_statistics(self):
        """
        记录性能统计信息
        """
        from loguru import logger
        
        stats = self.get_statistics()
        logger.info(f"性能统计: 总时间={stats['total_time']:.2f}秒, "
                   f"完成任务={stats['tasks_completed']}, "
                   f"下载速度={stats['download_speed']:.2f}MB/s, "
                   f"任务速度={stats['task_speed']:.2f}tasks/s")
        logger.info(f"下载时间: 平均={stats['download_times']['average']:.3f}秒, "
                   f"最小={stats['download_times']['minimum']:.3f}秒, "
                   f"最大={stats['download_times']['maximum']:.3f}秒")
        logger.info(f"任务处理时间: 平均={stats['task_processing_times']['average']:.3f}秒, "
                   f"最小={stats['task_processing_times']['minimum']:.3f}秒, "
                   f"最大={stats['task_processing_times']['maximum']:.3f}秒")

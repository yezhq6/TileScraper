# src/config.py

import os
import yaml
from pathlib import Path
from typing import Dict, Any
from loguru import logger
import time

class ConfigManager:
    """
    配置管理器
    """
    
    def __init__(self, config_file: str = "config.yaml"):
        """
        初始化配置管理器
        
        Args:
            config_file: 配置文件路径
        """
        self.config_file = Path(config_file)
        self.config: Dict[str, Any] = {}
        self.last_modified_time = 0
        self.load_main_config()
        self._load_environment_variables()

    def _load_environment_variables(self):
        """
        从环境变量加载配置
        环境变量格式: TILEHARVESTER_<SECTION>_<KEY> = value
        例如: TILEHARVESTER_SERVER_PORT = 8080
        """
        try:
            prefix = "TILEHARVESTER_"
            for key, value in os.environ.items():
                if key.startswith(prefix):
                    # 移除前缀
                    env_key = key[len(prefix):]
                    # 转换为小写并分割
                    parts = env_key.lower().split('_')
                    if len(parts) < 2:
                        continue
                    
                    section = parts[0]
                    config_key = '_'.join(parts[1:])
                    
                    # 转换值类型
                    if value.lower() in ['true', 'false']:
                        value = value.lower() == 'true'
                    elif value.isdigit():
                        value = int(value)
                    elif '.' in value and all(part.isdigit() for part in value.split('.') if part):
                        try:
                            value = float(value)
                        except ValueError:
                            pass
                    
                    # 设置配置
                    if section not in self.config:
                        self.config[section] = {}
                    self.config[section][config_key] = value
                    logger.debug(f"从环境变量加载配置: {section}.{config_key} = {value}")
        except Exception as e:
            logger.error(f"加载环境变量失败: {e}")

    def check_config_file_change(self):
        """
        检查配置文件是否被修改
        
        Returns:
            bool: 配置文件是否被修改
        """
        try:
            if self.config_file.exists():
                current_mtime = self.config_file.stat().st_mtime
                if current_mtime > self.last_modified_time:
                    self.last_modified_time = current_mtime
                    return True
            return False
        except Exception as e:
            logger.error(f"检查配置文件变化失败: {e}")
            return False

    def reload_config(self):
        """
        重新加载配置文件
        """
        try:
            logger.info("重新加载配置文件...")
            self.load_main_config()
            self._load_environment_variables()
            logger.info("配置文件重新加载成功")
            return True
        except Exception as e:
            logger.error(f"重新加载配置文件失败: {e}")
            return False
    
    def load_main_config(self):
        """
        加载主配置文件
        """
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config = yaml.safe_load(f)
                # 更新最后修改时间
                self.last_modified_time = self.config_file.stat().st_mtime
                logger.info(f"成功加载配置文件: {self.config_file}")
            else:
                logger.warning(f"配置文件不存在，使用默认配置: {self.config_file}")
                self._load_default_config()
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            self._load_default_config()
    
    def _load_default_config(self):
        """
        加载默认配置
        """
        self.config = {
            "server": {
                "host": "0.0.0.0",
                "port": 5000,
                "debug": False,
                "secret_key": "default-secret-key"
            },
            "download": {
                "max_threads": 128,
                "default_threads": 8,
                "max_retries": 3,
                "timeout": 30,
                "batch_size": 1000,
                "mbtiles_batch_size": 100,
                "progress_save_interval": 5
            },
            "memory": {
                "max_tiles_in_memory": 100000,
                "memory_threshold": 0.8
            },
            "database": {
                "journal_mode": "WAL",
                "cache_size": 1000000,
                "synchronous": "NORMAL",
                "busy_timeout": 30000,
                "mmap_size": 268435456
            },
            "logging": {
                "level": "INFO",
                "format": "%(asctime)s | %(levelname)s | %(name)s:%(lineno)d - %(message)s",
                "file": "tileharvester.log"
            },
            "paths": {
                "config_dir": "configs",
                "default_output_dir": "tiles_datasets",
                "progress_db_dir": ".progress"
            }
        }
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值
        
        Args:
            key: 配置键，支持点分隔的路径，如 "server.port"
            default: 默认值
        
        Returns:
            配置值
        """
        try:
            keys = key.split('.')
            value = self.config
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, key: str, value: Any):
        """
        设置配置值
        
        Args:
            key: 配置键，支持点分隔的路径，如 "server.port"
            value: 配置值
        """
        try:
            keys = key.split('.')
            config = self.config
            for k in keys[:-1]:
                if k not in config:
                    config[k] = {}
                config = config[k]
            config[keys[-1]] = value
        except Exception as e:
            logger.error(f"设置配置值失败: {e}")
    
    def save_main_config(self):
        """
        保存主配置到文件
        """
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)
            logger.info(f"成功保存配置文件: {self.config_file}")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
    
    def get_server_config(self) -> Dict[str, Any]:
        """
        获取服务器配置
        
        Returns:
            服务器配置
        """
        return self.config.get("server", {})
    
    def get_download_config(self) -> Dict[str, Any]:
        """
        获取下载配置
        
        Returns:
            下载配置
        """
        return self.config.get("download", {})
    
    def get_memory_config(self) -> Dict[str, Any]:
        """
        获取内存配置
        
        Returns:
            内存配置
        """
        return self.config.get("memory", {})
    
    def get_database_config(self) -> Dict[str, Any]:
        """
        获取数据库配置
        
        Returns:
            数据库配置
        """
        return self.config.get("database", {})
    
    def get_logging_config(self) -> Dict[str, Any]:
        """
        获取日志配置
        
        Returns:
            日志配置
        """
        return self.config.get("logging", {})
    
    def get_paths_config(self) -> Dict[str, Any]:
        """
        获取路径配置
        
        Returns:
            路径配置
        """
        return self.config.get("paths", {})
    
    def list_configs(self):
        """
        列出所有配置文件
        
        Returns:
            配置文件列表
        """
        try:
            config_dir = self.get("paths.config_dir", "configs")
            config_path = Path(config_dir)
            
            # 添加调试日志
            logger.info(f"配置目录: {config_path.absolute()}")
            logger.info(f"配置目录是否存在: {config_path.exists()}")
            
            if not config_path.exists():
                config_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"创建配置目录: {config_path.absolute()}")
                return []
            
            # 列出目录中的所有文件
            files = list(config_path.iterdir())
            logger.info(f"目录中的文件: {[f.name for f in files]}")
            
            configs = []
            # 同时支持 .yaml 和 .json 文件
            for ext in ["*.yaml", "*.json"]:
                for file in config_path.glob(ext):
                    logger.info(f"找到配置文件: {file.name}")
                    configs.append(file.stem)  # 只返回文件名（不含扩展名）
            
            # 去重，避免同一配置同时存在 .yaml 和 .json 版本
            configs = list(set(configs))
            
            logger.info(f"找到 {len(configs)} 个配置文件")
            return configs
        except Exception as e:
            logger.error(f"列出配置失败: {e}")
            return []
    
    def save_config(self, config_name: str, config_data: Dict[str, Any]):
        """
        保存配置到文件
        
        Args:
            config_name: 配置名称
            config_data: 配置数据
            
        Returns:
            bool: 是否保存成功
        """
        try:
            config_dir = self.get("paths.config_dir", "configs")
            config_path = Path(config_dir)
            
            if not config_path.exists():
                config_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"创建配置目录: {config_path}")
            
            config_file = config_path / f"{config_name}.yaml"
            
            with open(config_file, 'w', encoding='utf-8') as f:
                yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
            
            logger.info(f"配置保存成功: {config_file}")
            return True
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False
    
    def load_config(self, config_name: str) -> Dict[str, Any]:
        """
        从文件加载配置
        
        Args:
            config_name: 配置名称
            
        Returns:
            配置数据
        """
        try:
            config_dir = self.get("paths.config_dir", "configs")
            config_path = Path(config_dir)
            
            # 尝试加载 .yaml 文件
            yaml_file = config_path / f"{config_name}.yaml"
            if yaml_file.exists():
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    config_data = yaml.safe_load(f)
                logger.info(f"配置加载成功: {config_name} (yaml)")
                return config_data
            
            # 尝试加载 .json 文件
            json_file = config_path / f"{config_name}.json"
            if json_file.exists():
                import json
                with open(json_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                logger.info(f"配置加载成功: {config_name} (json)")
                return config_data
            
            logger.warning(f"配置文件不存在: {config_name}")
            return {}
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return {}

# 全局配置管理器实例
config_manager = ConfigManager()

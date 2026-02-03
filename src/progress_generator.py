#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进度文件生成器

用于生成进度文件，以便在将 MBTiles 转换为目录结构后，或者在复制 MBTiles 文件到其他地方时，能够继续使用断点续传功能。
支持生成 JSON 格式和 SQLite 格式的进度文件。
"""

import os
import json
import sqlite3
import time
from pathlib import Path
from typing import List, Tuple, Set


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
    
    # 2. 检查是否为标准Windows路径（包含盘符和斜杠）
    elif len(output_dir) > 1 and output_dir[1] == ':' and ('/' in output_dir or '\\' in output_dir):
        # 转换Windows路径到WSL2路径
        # 将盘符转换为/mnt/[小写盘符]
        drive_letter = output_dir[0].lower()
        # 替换反斜杠为正斜杠
        wsl_path = output_dir[2:].replace('\\', '/')
        # 构建完整的WSL路径
        full_path = f"/mnt/{drive_letter}/{wsl_path.lstrip('/')}"
        print(f"  转换Windows路径到WSL2路径: {output_dir} -> {full_path}")
        return Path(full_path)
    
    # 3. 特殊情况处理：路径被分割的情况
    # 例如："E:\qqg\mbtiles \\taiwan.mbtiles"（中间有空格）
    elif len(output_dir) > 1 and output_dir[1] == ':':
        # 处理可能的路径分割问题
        # 尝试修复路径，去除中间的空格
        fixed_path = output_dir.replace(' ', '')
        # 替换反斜杠为正斜杠
        fixed_path = fixed_path.replace('\\', '/')
        drive_letter = fixed_path[0].lower()
        full_path = f"/mnt/{drive_letter}/{fixed_path[2:].lstrip('/')}"
        print(f"  修复并转换Windows路径到WSL2路径: {output_dir} -> {full_path}")
        return Path(full_path)
    
    # 4. 其他情况，直接返回
    return Path(output_dir)


def generate_progress_file(input_path: str, provider_name: str = "custom", progress_format: str = "sqlite") -> bool:
    """
    生成进度文件
    
    Args:
        input_path: 输入路径，可以是目录或 MBTiles 文件
        provider_name: 提供商名称，默认为 "custom"
        progress_format: 进度文件格式，可选值: "json" 或 "sqlite"，默认为 "sqlite"
        
    Returns:
        bool: 是否生成成功
    """
    try:
        # 转换输入路径
        input_path = convert_path(input_path)
        
        # 确定数据文件夹和进度文件路径
        if input_path.exists() and input_path.is_file() and input_path.suffix == '.mbtiles':
            # 输入是 MBTiles 文件
            data_dir = input_path.parent / "aux"
            if progress_format == "sqlite":
                progress_file = data_dir / f"{provider_name}_progress.db"
            else:
                progress_file = data_dir / f"{provider_name}_progress.json"
            print(f"✓ 输入是 MBTiles 文件: {input_path.name}")
            print(f"  数据文件夹: {data_dir}")
            
            # 从 MBTiles 文件中提取瓦片信息
            processed_tiles = _extract_tiles_from_mbtiles(input_path)
        elif input_path.exists() and input_path.is_dir():
            # 输入是目录
            data_dir = input_path / "aux"
            if progress_format == "sqlite":
                progress_file = data_dir / f"{provider_name}_progress.db"
            else:
                progress_file = data_dir / f"{provider_name}_progress.json"
            print(f"✓ 输入是目录: {input_path.name}")
            print(f"  数据文件夹: {data_dir}")
            
            # 从目录中提取瓦片信息
            processed_tiles = _extract_tiles_from_directory(input_path)
        else:
            # 输入路径不存在
            print(f"✗ 输入路径不存在或无效: {input_path}")
            return False
        
        # 检查处理的瓦片数量，避免生成过大的进度文件
        tile_count = len(processed_tiles)
        if tile_count > 1000000:
            print(f"⚠ 警告: 瓦片数量 ({tile_count}) 非常大，可能会生成大型进度文件")
            print("  这可能会导致内存使用过高和加载速度变慢")
        
        # 根据格式生成进度文件
        if progress_format == "sqlite":
            # 生成SQLite格式的进度文件
            success = _generate_sqlite_progress_file(progress_file, processed_tiles, provider_name, input_path)
        else:
            # 生成JSON格式的进度文件
            success = _generate_json_progress_file(progress_file, processed_tiles, provider_name, input_path)
        
        return success
        
    except Exception as e:
        print(f"✗ 生成进度文件失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def _extract_tiles_from_mbtiles(mbtiles_path: Path) -> Set[Tuple[int, int, int]]:
    """
    从 MBTiles 文件中提取瓦片信息
    
    Args:
        mbtiles_path: MBTiles 文件路径
        
    Returns:
        Set[Tuple[int, int, int]]: 已处理的瓦片集合，格式为 (x, y, z)
    """
    processed_tiles = set()
    
    try:
        # 连接 MBTiles 数据库
        conn = sqlite3.connect(mbtiles_path)
        cursor = conn.cursor()
        
        # 获取所有瓦片
        cursor.execute("SELECT zoom_level, tile_column, tile_row FROM tiles")
        tiles = cursor.fetchall()
        
        # 转换瓦片坐标
        for z, x, y in tiles:
            # MBTiles 中的 tile_row 是 TMS 格式，需要转换为 XYZ 格式
            y_xyz = (2 ** z - 1) - y
            processed_tiles.add((x, y_xyz, z))
        
        conn.close()
        print(f"  从 MBTiles 文件中提取了 {len(processed_tiles)} 个瓦片")
        
    except Exception as e:
        print(f"  从 MBTiles 文件中提取瓦片失败: {e}")
    
    return processed_tiles


def _extract_tiles_from_directory(directory: Path) -> Set[Tuple[int, int, int]]:
    """
    从目录中提取瓦片信息
    
    Args:
        directory: 目录路径
        
    Returns:
        Set[Tuple[int, int, int]]: 已处理的瓦片集合，格式为 (x, y, z)
    """
    processed_tiles = set()
    
    try:
        # 遍历目录结构
        for zoom_dir in directory.iterdir():
            if not zoom_dir.is_dir():
                continue
            
            # 尝试将目录名转换为缩放级别
            try:
                z = int(zoom_dir.name)
            except ValueError:
                continue
            
            # 遍历 x 目录
            for x_dir in zoom_dir.iterdir():
                if not x_dir.is_dir():
                    continue
                
                # 尝试将目录名转换为 x 坐标
                try:
                    x = int(x_dir.name)
                except ValueError:
                    continue
                
                # 遍历瓦片文件
                for tile_file in x_dir.iterdir():
                    if not tile_file.is_file():
                        continue
                    
                    # 尝试从文件名中提取 y 坐标
                    try:
                        y_str = tile_file.stem
                        y = int(y_str)
                        processed_tiles.add((x, y, z))
                    except ValueError:
                        continue
        
        print(f"  从目录中提取了 {len(processed_tiles)} 个瓦片")
        
    except Exception as e:
        print(f"  从目录中提取瓦片失败: {e}")
    
    return processed_tiles


def _generate_sqlite_progress_file(progress_file: Path, processed_tiles: Set[Tuple[int, int, int]], provider_name: str, input_path: Path) -> bool:
    """
    生成SQLite格式的进度文件
    
    Args:
        progress_file: 进度文件路径
        processed_tiles: 已处理的瓦片集合
        provider_name: 提供商名称
        input_path: 输入路径
        
    Returns:
        bool: 是否生成成功
    """
    try:
        # 确保输出目录存在
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 连接或创建SQLite数据库
        conn = sqlite3.connect(progress_file)
        cursor = conn.cursor()
        
        # 优化SQLite配置
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA cache_size=1000000;')  # 增加缓存大小到约1GB
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA temp_store=MEMORY;')  # 使用内存存储临时表
        conn.execute('PRAGMA mmap_size=1073741824;')  # 启用1GB内存映射
        conn.execute('PRAGMA locking_mode=EXCLUSIVE;')  # 使用独占锁，减少锁竞争
        
        # 创建元数据表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # 创建瓦片表（不创建索引，后续再创建）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_tiles (
                x INTEGER,
                y INTEGER,
                z INTEGER,
                status TEXT,
                timestamp REAL,
                PRIMARY KEY (x, y, z)
            )
        ''')
        
        # 批量插入瓦片数据
        batch_size = 50000  # 增大批处理大小
        tiles = list(processed_tiles)
        tile_count = len(tiles)
        
        print(f"  开始插入 {tile_count} 个瓦片到SQLite数据库")
        
        # 计算一次时间戳，所有瓦片共用
        current_timestamp = time.time()
        
        # 使用单个事务处理所有插入，只在最后提交
        conn.execute('BEGIN TRANSACTION;')
        
        try:
            for i in range(0, tile_count, batch_size):
                batch = tiles[i:i+batch_size]
                # 直接生成元组列表，避免中间转换
                tile_data = [(x, y, z, 'success', current_timestamp) for x, y, z in batch]
                
                # 使用 executemany 进行批量插入
                cursor.executemany(
                    '''INSERT OR IGNORE INTO processed_tiles 
                    (x, y, z, status, timestamp) 
                    VALUES (?, ?, ?, ?, ?)''',
                    tile_data
                )
                
                # 只打印进度，不提交事务
                if (i + batch_size) % 200000 == 0 or (i + batch_size) >= tile_count:
                    print(f"  已处理 {min(i+batch_size, tile_count)}/{tile_count} 个瓦片")
            
            # 提交所有插入
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise
        
        # 不需要创建额外索引，因为：
        # 1. 下载器只查询 x, y, z 坐标，这些已经通过主键索引覆盖
        # 2. 元数据查询是全表扫描，表很小，不需要索引
        # 3. 创建索引会显著增加处理时间和磁盘空间
        print("  跳过索引创建，使用主键索引足够满足查询需求")
        
        # 插入元数据
        metadata = [
            ('downloaded_count', str(tile_count)),
            ('failed_count', '0'),
            ('skipped_count', '0'),
            ('total_tasks', str(tile_count)),
            ('total_bytes', '0'),
            ('timestamp', str(current_timestamp)),
            ('input_path', str(input_path)),
            ('provider_name', provider_name),
            ('tile_count', str(tile_count)),
            ('save_format', 'mbtiles' if input_path.suffix == '.mbtiles' else 'directory'),
            ('version', '1.2')
        ]
        
        conn.execute('BEGIN TRANSACTION;')
        try:
            cursor.executemany(
                'INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)',
                metadata
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise
        
        conn.close()
        
        print(f"✓ 成功生成SQLite格式进度文件: {progress_file.name}")
        print(f"   包含 {tile_count} 个已处理的瓦片")
        print(f"   保存格式: {'mbtiles' if input_path.suffix == '.mbtiles' else 'directory'}")
        
        return True
    except Exception as e:
        print(f"✗ 生成SQLite进度文件失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def _generate_json_progress_file(progress_file: Path, processed_tiles: Set[Tuple[int, int, int]], provider_name: str, input_path: Path) -> bool:
    """
    生成JSON格式的进度文件
    
    Args:
        progress_file: 进度文件路径
        processed_tiles: 已处理的瓦片集合
        provider_name: 提供商名称
        input_path: 输入路径
        
    Returns:
        bool: 是否生成成功
    """
    try:
        # 确保输出目录存在
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 生成进度文件内容
        tile_count = len(processed_tiles)
        progress_data = {
            'version': '1.1',  # 进度文件版本
            'downloaded_count': tile_count,
            'failed_count': 0,
            'skipped_count': 0,
            'total_tasks': tile_count,
            'total_bytes': 0,
            'processed_tiles': list(processed_tiles),
            'timestamp': time.time(),
            'input_path': str(input_path),
            'provider_name': provider_name,
            'tile_count': tile_count,
            'save_format': 'mbtiles' if input_path.suffix == '.mbtiles' else 'directory',
            'generation_info': {
                'generated_by': 'TileHarvester Progress Generator',
                'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'input_type': 'mbtiles' if input_path.suffix == '.mbtiles' else 'directory'
            }
        }
        
        # 保存进度文件（使用临时文件原子性替换）
        temp_file = progress_file.with_suffix('.tmp')
        
        # 写入临时文件
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
        
        # 原子性替换文件
        temp_file.replace(progress_file)
        
        print(f"✓ 成功生成JSON格式进度文件: {progress_file.name}")
        print(f"   包含 {tile_count} 个已处理的瓦片")
        print(f"   保存格式: {'mbtiles' if input_path.suffix == '.mbtiles' else 'directory'}")
        
        return True
    except Exception as e:
        print(f"✗ 生成JSON进度文件失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="生成进度文件")
    parser.add_argument('-p', '--path', required=True, help="输入路径，可以是目录或 MBTiles 文件")
    parser.add_argument('-n', '--name', default="custom", help="提供商名称，默认为 'custom'")
    parser.add_argument('-f', '--format', default="sqlite", choices=["json", "sqlite"], help="进度文件格式，默认为 'sqlite'")
    
    args = parser.parse_args()
    
    generate_progress_file(args.path, args.name, args.format)

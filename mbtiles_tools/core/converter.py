#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MBTiles转换工具核心模块

包含基础转换功能
"""

import os
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from mbtiles_tools.core.coordinate import CoordinateConverter
from mbtiles_tools.core.utils import ensure_directory


class MBTilesConverter:
    """
    MBTiles转换工具类
    """
    
    def __init__(self):
        """
        初始化MBTilesConverter
        """
        pass
    
    def convert_directory_to_mbtiles(self, input_dir, mbtiles_path, scheme='xyz', max_workers=None, zoom_levels=None):
        """
        将目录结构转换为MBTiles
        
        Args:
            input_dir: 输入目录
            mbtiles_path: 输出MBTiles文件路径
            scheme: 输入目录的坐标系统 ('xyz' 或 'tms')，默认为'xyz'
            max_workers: 最大线程数
            zoom_levels: 要转换的缩放级别列表，None表示转换所有级别
            
        Returns:
            bool: 是否转换成功
            
        转换逻辑:
        - 如果scheme='xyz'：直接转换，不进行坐标转换，scheme字段设为'xyz'
        - 如果scheme='tms'：进行坐标转换 (XYZ to TMS)，scheme字段设为'tms'
        - 默认scheme='xyz'：直接转换，不进行坐标转换，scheme字段设为'xyz'
        """
        # 验证输入目录是否存在
        if not os.path.exists(input_dir):
            print(f"✗ 输入目录不存在: {input_dir}")
            return False
        
        # 验证scheme参数
        scheme = scheme.lower()
        if scheme not in ['xyz', 'tms']:
            print(f"✗ 无效的坐标系统: {scheme}，默认使用 'xyz'")
            scheme = 'xyz'
        
        # 创建输出目录
        ensure_directory(os.path.dirname(mbtiles_path))
        
        try:
            # 获取所有缩放级别目录
            zoom_dirs = [d for d in os.listdir(input_dir) 
                        if os.path.isdir(os.path.join(input_dir, d)) and d.isdigit()]
            
            # 转换为整数并排序
            all_zoom_levels = sorted([int(d) for d in zoom_dirs])
            
            # 过滤要转换的缩放级别
            if zoom_levels:
                convert_zoom_levels = [z for z in all_zoom_levels if z in zoom_levels]
            else:
                convert_zoom_levels = all_zoom_levels
            
            if not convert_zoom_levels:
                print("✗ 没有要转换的缩放级别")
                return False
            
            print(f"✓ 发现缩放级别: {all_zoom_levels}")
            print(f"   要转换的缩放级别: {convert_zoom_levels}")
            print(f"   使用坐标系统: {scheme.upper()}")
            
            # 检查MBTiles文件是否存在
            mbtiles_exists = os.path.exists(mbtiles_path)
            
            # 创建MBTiles数据库，使用WAL模式和优化配置
            conn = sqlite3.connect(mbtiles_path)
            cursor = conn.cursor()
            
            # 优化SQLite配置
            conn.execute('PRAGMA journal_mode=WAL;')  # 启用WAL模式，提高并发性能
            conn.execute('PRAGMA cache_size=1000000;')  # 增加缓存大小，约1GB
            conn.execute('PRAGMA synchronous=NORMAL;')  # 同步模式设为NORMAL，权衡安全性和性能
            conn.execute('PRAGMA wal_autocheckpoint=1000;')  # 定期检查点，合并WAL文件
            conn.execute('PRAGMA busy_timeout=30000;')  # 设置锁定超时为30秒
            
            # 检查数据库是否有效（尝试执行简单查询）
            try:
                cursor.execute('SELECT 1')
                if mbtiles_exists:
                    print(f"✓ MBTiles数据库存在且有效: {mbtiles_path}")
                else:
                    print(f"✓ 创建MBTiles数据库: {mbtiles_path}")
            except sqlite3.Error as e:
                print(f"✗ MBTiles数据库无效: {e}")
                conn.close()
                return False
            
            # 创建表结构
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tiles (
                    zoom_level INTEGER,
                    tile_column INTEGER,
                    tile_row INTEGER,
                    tile_data BLOB,
                    PRIMARY KEY (zoom_level, tile_column, tile_row)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS metadata (
                    name TEXT,
                    value TEXT,
                    PRIMARY KEY (name)
                )
            ''')
            
            # 确定格式（使用第一个缩放级别的第一个文件）
            format_type = 'jpg'  # 默认格式
            found_format = False
            for zoom in convert_zoom_levels:
                zoom_dir = os.path.join(input_dir, str(zoom))
                x_dirs = [d for d in os.listdir(zoom_dir) if os.path.isdir(os.path.join(zoom_dir, d)) and d.isdigit()]
                if x_dirs:
                    x_dir = os.path.join(zoom_dir, x_dirs[0])
                    image_files = [f for f in os.listdir(x_dir) 
                                 if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
                    if image_files:
                        first_ext = os.path.splitext(image_files[0])[1].lower()
                        if first_ext in ['.png']:
                            format_type = 'png'
                        elif first_ext in ['.jpeg', '.jpg']:
                            format_type = 'jpg'
                        found_format = True
                        break
            
            # 插入元数据（仅当数据库不存在时）
            if not mbtiles_exists:
                metadata = [
                    ('name', 'Directory to MBTiles'),
                    ('type', 'baselayer'),
                    ('version', '1.0'),
                    ('description', 'Generated by MBTiles Converter'),
                    ('format', format_type),
                    ('scheme', scheme)  # 添加scheme字段
                ]
                cursor.executemany('INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)', metadata)
                conn.commit()
                print(f"✓ 初始化MBTiles数据库元数据")
                print(f"   设置scheme: {scheme}")
                print(f"   格式: {format_type}")
            else:
                # 读取现有元数据
                cursor.execute("SELECT name, value FROM metadata")
                existing_metadata = dict(cursor.fetchall())
                print(f"✓ 读取现有MBTiles数据库元数据")
                if 'format' in existing_metadata:
                    print(f"   现有格式: {existing_metadata['format']}")
                if 'scheme' in existing_metadata:
                    print(f"   现有scheme: {existing_metadata['scheme']}")
            
            # 批量处理参数
            batch_size = 1000  # 每批处理1000个瓦片
            
            # 总体统计
            total_success = 0
            total_failed = 0
            total_skipped = 0
            total_processed = 0
            total_tiles = 0
            
            # 计算总瓦片数
            print(f"✓ 开始扫描输入目录: {input_dir}")
            for zoom in convert_zoom_levels:
                zoom_dir = os.path.join(input_dir, str(zoom))
                x_dirs = [d for d in os.listdir(zoom_dir) if os.path.isdir(os.path.join(zoom_dir, d)) and d.isdigit()]
                
                zoom_tiles = 0
                for x_str in x_dirs:
                    x_dir = os.path.join(zoom_dir, x_str)
                    image_files = [f for f in os.listdir(x_dir) 
                                 if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
                    zoom_tiles += len(image_files)
                    total_tiles += len(image_files)
                print(f"   缩放级别 {zoom}: {zoom_tiles} 个瓦片")
            
            print(f"✓ 找到 {total_tiles} 个瓦片")
            print(f"✓ 开始转换，共 {total_tiles} 个瓦片...")
            start_time = time.time()
            
            # 增大批处理大小以减少数据库提交频率
            batch_size = 5000  # 每批处理5000个瓦片
            
            # 创建全局线程池，避免频繁创建和销毁
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 按缩放级别处理
                for zoom in convert_zoom_levels:
                    zoom_dir = os.path.join(input_dir, str(zoom))
                    x_dirs = [d for d in os.listdir(zoom_dir) if os.path.isdir(os.path.join(zoom_dir, d)) and d.isdigit()]
                    
                    print(f"\n✓ 处理缩放级别 {zoom}，共 {len(x_dirs)} 个x目录")
                    
                    # 处理所有x目录的瓦片
                    all_tiles_info = []
                    for x_str in x_dirs:
                        x = int(x_str)
                        x_dir = os.path.join(zoom_dir, x_str)
                        
                        # 获取所有图片文件（支持jpg、png、jpeg）
                        image_files = [f for f in os.listdir(x_dir) 
                                     if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
                        
                        if not image_files:
                            continue
                        
                        print(f"   处理x={x}，共 {len(image_files)} 个瓦片")
                        
                        # 收集当前x目录的瓦片信息
                        for image_file in image_files:
                            # 提取y坐标
                            y = int(os.path.splitext(image_file)[0])
                            input_path = os.path.join(x_dir, image_file)
                            
                            all_tiles_info.append({
                                'zoom': zoom,
                                'x': x,
                                'y': y,
                                'input_path': input_path,
                                'scheme': scheme
                            })
                    
                    # 批量处理所有瓦片
                    batch = []
                    future_to_tile = {executor.submit(self._process_tile, tile_info): tile_info for tile_info in all_tiles_info}
                    
                    # 处理完成的任务
                    for future in as_completed(future_to_tile):
                        tile_info = future_to_tile[future]
                        try:
                            result = future.result()
                            if result:
                                batch.append(result)
                                total_success += 1
                                total_processed += 1
                                
                                # 每1000个瓦片显示一次进度
                                if total_processed % 1000 == 0:
                                    progress = (total_processed / total_tiles) * 100
                                    elapsed = time.time() - start_time
                                    speed = total_processed / elapsed if elapsed > 0 else 0
                                    print(f"   总进度: {progress:.1f}% ({total_processed}/{total_tiles}) - 速度: {speed:.2f} 个/秒")
                                
                                # 每batch_size个瓦片批量插入一次
                                if len(batch) >= batch_size:
                                    # 使用executemany进行批量插入，使用INSERT OR IGNORE跳过已存在的瓦片
                                    cursor.executemany(
                                        '''INSERT OR IGNORE INTO tiles 
                                        (zoom_level, tile_column, tile_row, tile_data) 
                                        VALUES (?, ?, ?, ?)''',
                                        batch
                                    )
                                    # 计算跳过的瓦片数
                                    total_skipped += len(batch) - cursor.rowcount
                                    conn.commit()
                                    batch = []
                        except Exception as e:
                            print(f"✗ 处理瓦片失败: {tile_info['input_path']}")
                            print(f"   错误信息: {e}")
                            total_failed += 1
                            total_processed += 1
                    
                    # 插入剩余的瓦片
                    if batch:
                        cursor.executemany(
                            '''INSERT OR IGNORE INTO tiles 
                            (zoom_level, tile_column, tile_row, tile_data) 
                            VALUES (?, ?, ?, ?)''',
                            batch
                        )
                        # 计算跳过的瓦片数
                        total_skipped += len(batch) - cursor.rowcount
                        conn.commit()
                    
                    print(f"   缩放级别 {zoom} 处理完成")
            
            conn.close()
            
            end_time = time.time()
            elapsed_time = end_time - start_time
            
            print(f"\n✓ 转换完成！")
            print(f"   成功: {total_success}")
            print(f"   失败: {total_failed}")
            print(f"   跳过: {total_skipped}")
            print(f"   耗时: {elapsed_time:.2f} 秒")
            print(f"   平均速度: {total_processed/elapsed_time:.2f} 个/秒")
            print(f"✓ 输出MBTiles: {mbtiles_path}")
            
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 转换失败: {e}")
            return False
    
    def convert_mbtiles_to_directory(self, mbtiles_path, output_dir, scheme=None, max_workers=None, zoom_levels=None, overwrite=True):
        """
        将MBTiles转换为目录结构
        
        Args:
            mbtiles_path: MBTiles文件路径
            output_dir: 输出目录
            scheme: 输出目录的坐标系统 ('xyz' 或 'tms')，None表示询问用户
            max_workers: 最大线程数
            zoom_levels: 要提取的缩放级别列表，None表示提取所有级别
            overwrite: 是否覆盖已存在的文件，默认 True（覆盖），False 表示跳过已存在的文件
            
        Returns:
            bool: 是否转换成功
        """
        # 验证MBTiles文件是否存在
        if not os.path.exists(mbtiles_path):
            print(f"✗ MBTiles文件不存在: {mbtiles_path}")
            return False
        
        # 创建输出目录
        ensure_directory(output_dir)
        
        try:
            # 连接MBTiles数据库，使用WAL模式和优化配置
            conn = sqlite3.connect(mbtiles_path)
            cursor = conn.cursor()
            
            # 优化SQLite配置
            conn.execute('PRAGMA journal_mode=WAL;')  # 启用WAL模式，提高并发性能
            conn.execute('PRAGMA cache_size=1000000;')  # 增加缓存大小，约1GB
            conn.execute('PRAGMA synchronous=NORMAL;')  # 同步模式设为NORMAL，权衡安全性和性能
            conn.execute('PRAGMA wal_autocheckpoint=1000;')  # 定期检查点，合并WAL文件
            conn.execute('PRAGMA busy_timeout=30000;')  # 设置锁定超时为30秒
            
            # 获取元数据
            cursor.execute("SELECT name, value FROM metadata")
            metadata = dict(cursor.fetchall())
            print(f"✓ 读取MBTiles元数据: {metadata.get('name', 'Unknown')}")
            print(f"   格式: {metadata.get('format', 'Unknown')}")
            print(f"   类型: {metadata.get('type', 'Unknown')}")
            
            # 获取scheme字段
            mbtiles_scheme = metadata.get('scheme', 'tms').lower()
            print(f"   内部坐标系统: {mbtiles_scheme.upper()}")
            
            # 处理scheme参数
            if scheme:
                scheme = scheme.lower()
                if scheme not in ['xyz', 'tms']:
                    print(f"✗ 无效的坐标系统: {scheme}，默认使用 'xyz'")
                    scheme = 'xyz'
            else:
                # 询问用户
                print(f"\nMBTiles文件使用的坐标系统是: {mbtiles_scheme.upper()}")
                while True:
                    user_input = input("请选择输出目录的坐标系统 (xyz/tms): ").lower()
                    if user_input in ['xyz', 'tms']:
                        scheme = user_input
                        break
                    print("✗ 无效的输入，请输入 'xyz' 或 'tms'")
            
            print(f"   输出坐标系统: {scheme.upper()}")
            
            # 获取缩放级别列表
            cursor.execute("SELECT DISTINCT zoom_level FROM tiles ORDER BY zoom_level")
            all_zoom_levels = [row[0] for row in cursor.fetchall()]
            
            # 过滤要提取的缩放级别
            if zoom_levels:
                extract_zoom_levels = [z for z in all_zoom_levels if z in zoom_levels]
            else:
                extract_zoom_levels = all_zoom_levels
            
            print(f"   可用缩放级别: {all_zoom_levels}")
            print(f"   要提取的缩放级别: {extract_zoom_levels}")
            
            if not extract_zoom_levels:
                print("✗ 没有要提取的缩放级别")
                conn.close()
                return False
            
            # 获取瓦片总数
            if zoom_levels:
                placeholders = ','.join(['?'] * len(zoom_levels))
                cursor.execute(f"SELECT COUNT(*) FROM tiles WHERE zoom_level IN ({placeholders})", zoom_levels)
            else:
                cursor.execute("SELECT COUNT(*) FROM tiles")
            total_tiles = cursor.fetchone()[0]
            
            print(f"✓ 总瓦片数: {total_tiles}")
            
            # 总体统计
            total_success = 0
            total_failed = 0
            total_processed = 0
            
            # 批量处理参数
            batch_size = 1000  # 每批获取1000个瓦片
            
            print(f"✓ 开始转换，共 {total_tiles} 个瓦片...")
            start_time = time.time()
            
            # 增大批处理大小以提高性能
            batch_size = 5000  # 每批处理5000个瓦片
            
            # 创建全局线程池，避免频繁创建和销毁
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 按缩放级别处理
                for zoom in extract_zoom_levels:
                    # 计算当前缩放级别的瓦片数量
                    cursor.execute(
                        "SELECT COUNT(*) FROM tiles WHERE zoom_level = ?",
                        (zoom,)
                    )
                    zoom_tile_count = cursor.fetchone()[0]
                    print(f"\n✓ 处理缩放级别 {zoom}，共 {zoom_tile_count} 个瓦片")
                    
                    # 处理所有瓦片数据
                    offset = 0
                    while offset < zoom_tile_count:
                        # 分批获取瓦片数据
                        cursor.execute(
                            "SELECT tile_column, tile_row, tile_data FROM tiles WHERE zoom_level = ? LIMIT ? OFFSET ?",
                            (zoom, batch_size, offset)
                        )
                        
                        batch_tiles = cursor.fetchall()
                        if not batch_tiles:
                            break
                        
                        # 处理当前批次的瓦片
                        tiles_info = []
                        for tile_column, tile_row, tile_data in batch_tiles:
                            # 计算输出的y坐标
                            # 注意：根据MBTiles规范，内部存储始终使用TMS坐标系统
                            # metadata中的scheme字段只是记录了原始输入的坐标系统
                            if scheme == 'xyz':
                                # 转换为XYZ坐标系统
                                y = CoordinateConverter.tms_to_xyz(zoom, tile_row)
                            else:
                                # 使用TMS坐标系统
                                y = tile_row
                            
                            # 构建输出路径
                            zoom_dir = os.path.join(output_dir, str(zoom))
                            x_dir = os.path.join(zoom_dir, str(tile_column))
                            # 根据格式确定文件扩展名
                            ext = metadata.get('format', 'jpg')
                            output_path = os.path.join(x_dir, f"{y}.{ext}")
                            
                            tiles_info.append({
                                'zoom': zoom,
                                'tile_column': tile_column,
                                'tile_row': tile_row,
                                'tile_data': tile_data,
                                'x_dir': x_dir,
                                'output_path': output_path
                            })
                        
                        # 提交当前批次的任务
                        future_to_tile = {executor.submit(self._save_tile, tile_info, overwrite): tile_info for tile_info in tiles_info}
                        
                        # 处理完成的任务
                        for future in as_completed(future_to_tile):
                            tile_info = future_to_tile[future]
                            try:
                                result = future.result()
                                if result:
                                    total_success += 1
                                else:
                                    total_failed += 1
                                total_processed += 1
                                
                                # 每1000个瓦片显示一次进度
                                if total_processed % 1000 == 0:
                                    progress = (total_processed / total_tiles) * 100
                                    elapsed = time.time() - start_time
                                    speed = total_processed / elapsed if elapsed > 0 else 0
                                    print(f"   总进度: {progress:.1f}% ({total_processed}/{total_tiles}) - 速度: {speed:.2f} 个/秒")
                            except Exception as e:
                                print(f"✗ 保存瓦片失败: {tile_info['output_path']}")
                                print(f"   错误信息: {e}")
                                total_failed += 1
                                total_processed += 1
                        
                        offset += batch_size
                        print(f"   缩放级别 {zoom}: 已处理 {offset}/{zoom_tile_count} 个瓦片")
            
            conn.close()
            
            end_time = time.time()
            elapsed_time = end_time - start_time
            
            print(f"\n✓ 转换完成！")
            print(f"   成功: {total_success}")
            print(f"   失败: {total_failed}")
            print(f"   耗时: {elapsed_time:.2f} 秒")
            print(f"   平均速度: {total_processed/elapsed_time:.2f} 个/秒")
            print(f"✓ 输出目录: {output_dir}")
            
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 转换失败: {e}")
            return False
    
    def _process_tile(self, tile_info):
        """
        处理单个瓦片文件
        
        Args:
            tile_info: 瓦片信息字典
            
        Returns:
            tuple: (zoom_level, tile_column, tile_row, tile_data) 或 None
        """
        try:
            # 读取瓦片文件
            with open(tile_info['input_path'], 'rb') as f:
                tile_data = f.read()
            
            zoom = tile_info['zoom']
            x = tile_info['x']
            y = tile_info['y']
            scheme = tile_info['scheme']
            
            # 转换坐标
            # 注意：MBTiles内部存储始终使用TMS坐标系统
            # 无论输入是什么坐标系，都需要转换为TMS存储
            if scheme == 'xyz':
                # XYZ to TMS 转换
                mbtiles_row = CoordinateConverter.xyz_to_tms(zoom, y)
            else:
                # TMS to TMS (无需转换)
                mbtiles_row = y
            
            return (zoom, x, mbtiles_row, tile_data)
        except Exception as e:
            print(f"✗ 处理瓦片失败: {tile_info['input_path']}")
            print(f"   错误信息: {e}")
            return None
    
    def _save_tile(self, tile_info, overwrite=True):
        """
        保存单个瓦片到文件
        
        Args:
            tile_info: 瓦片信息字典
            overwrite: 是否覆盖已存在的文件，默认 True（覆盖），False 表示跳过已存在的文件
            
        Returns:
            bool: 是否保存成功
        """
        try:
            # 创建目录
            ensure_directory(tile_info['x_dir'])
            
            # 检查文件是否已存在
            if not overwrite and os.path.exists(tile_info['output_path']):
                # 跳过已存在的文件
                return True
            
            # 保存瓦片数据
            with open(tile_info['output_path'], 'wb') as f:
                f.write(tile_info['tile_data'])
            
            return True
        except Exception as e:
            print(f"✗ 保存瓦片失败: {tile_info['output_path']}")
            print(f"   错误信息: {e}")
            return False
    
    def analyze_mbtiles(self, mbtiles_path):
        """
        分析MBTiles文件的元数据、瓦片数据、层级分布和经纬度范围
        
        Args:
            mbtiles_path: MBTiles文件路径
            
        Returns:
            bool: 是否分析成功
        """
        # 验证MBTiles文件是否存在
        if not os.path.exists(mbtiles_path):
            print(f"✗ MBTiles文件不存在: {mbtiles_path}")
            return False
        
        try:
            # 连接MBTiles数据库
            conn = sqlite3.connect(mbtiles_path)
            cursor = conn.cursor()
            
            # 1. 读取元数据
            print("\n=== 元数据信息 ===")
            cursor.execute("SELECT name, value FROM metadata")
            metadata = dict(cursor.fetchall())
            
            for key, value in metadata.items():
                print(f"{key}: {value}")
            
            # 2. 分析瓦片数据
            print("\n=== 瓦片数据统计 ===")
            
            # 获取总瓦片数
            cursor.execute("SELECT COUNT(*) FROM tiles")
            total_tiles = cursor.fetchone()[0]
            print(f"总瓦片数: {total_tiles}")
            
            # 3. 分析层级分布
            print("\n=== 层级分布 ===")
            cursor.execute("SELECT zoom_level, COUNT(*) FROM tiles GROUP BY zoom_level ORDER BY zoom_level")
            zoom_distribution = cursor.fetchall()
            
            for zoom, count in zoom_distribution:
                print(f"缩放级别 {zoom}: {count} 个瓦片")
            
            # 4. 分析经纬度范围（如果有bounding box信息）
            print("\n=== 经纬度范围 ===")
            if 'bounds' in metadata:
                bounds = metadata['bounds']
                print(f"边界框: {bounds}")
                # 解析边界框
                try:
                    min_lon, min_lat, max_lon, max_lat = map(float, bounds.split(','))
                    print(f"最小经度: {min_lon}")
                    print(f"最小纬度: {min_lat}")
                    print(f"最大经度: {max_lon}")
                    print(f"最大纬度: {max_lat}")
                except Exception:
                    print("边界框格式不正确")
            else:
                print("未找到边界框信息")
            
            # 5. 分析文件大小
            print("\n=== 文件信息 ===")
            file_size = os.path.getsize(mbtiles_path)
            print(f"文件大小: {file_size / (1024 * 1024):.2f} MB")
            
            # 6. 分析格式信息
            if 'format' in metadata:
                print(f"瓦片格式: {metadata['format']}")
            else:
                print("未找到瓦片格式信息")
            
            conn.close()
            print("\n✓ 分析完成！")
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 分析失败: {e}")
            return False
    
    def merge_mbtiles(self, input_paths, output_path, max_workers=None):
        """
        合并多个MBTiles文件
        
        Args:
            input_paths: 输入MBTiles文件列表
            output_path: 输出MBTiles文件路径
            max_workers: 最大线程数
            
        Returns:
            bool: 是否合并成功
        """
        # 验证输入文件是否存在
        for path in input_paths:
            if not os.path.exists(path):
                print(f"✗ 输入文件不存在: {path}")
                return False
        
        # 创建输出目录
        ensure_directory(os.path.dirname(output_path))
        
        try:
            # 创建输出MBTiles数据库
            conn_output = sqlite3.connect(output_path)
            cursor_output = conn_output.cursor()
            
            # 优化SQLite配置
            conn_output.execute('PRAGMA journal_mode=WAL;')
            conn_output.execute('PRAGMA cache_size=1000000;')
            conn_output.execute('PRAGMA synchronous=NORMAL;')
            conn_output.execute('PRAGMA wal_autocheckpoint=1000;')
            conn_output.execute('PRAGMA busy_timeout=30000;')
            
            # 创建表结构
            cursor_output.execute('''
                CREATE TABLE IF NOT EXISTS tiles (
                    zoom_level INTEGER,
                    tile_column INTEGER,
                    tile_row INTEGER,
                    tile_data BLOB,
                    PRIMARY KEY (zoom_level, tile_column, tile_row)
                )
            ''')
            
            cursor_output.execute('''
                CREATE TABLE IF NOT EXISTS metadata (
                    name TEXT,
                    value TEXT,
                    PRIMARY KEY (name)
                )
            ''')
            
            # 总体统计
            total_success = 0
            total_skipped = 0
            total_failed = 0
            total_tiles = 0
            
            print(f"✓ 开始合并 {len(input_paths)} 个MBTiles文件...")
            start_time = time.time()
            
            # 增大批处理大小以提高性能
            batch_size = 5000  # 每批处理5000个瓦片
            
            # 处理每个输入文件
            for i, input_path in enumerate(input_paths):
                print(f"\n✓ 处理文件 {i+1}/{len(input_paths)}: {input_path}")
                
                try:
                    # 连接输入MBTiles数据库
                    conn_input = sqlite3.connect(input_path)
                    cursor_input = conn_input.cursor()
                    
                    # 获取输入文件的元数据
                    cursor_input.execute("SELECT name, value FROM metadata")
                    metadata = dict(cursor_input.fetchall())
                    print(f"   元数据: {metadata.get('name', 'Unknown')}")
                    
                    # 获取输入文件的瓦片数量
                    cursor_input.execute("SELECT COUNT(*) FROM tiles")
                    input_tile_count = cursor_input.fetchone()[0]
                    print(f"   瓦片数: {input_tile_count}")
                    total_tiles += input_tile_count
                    
                    # 分批获取瓦片数据
                    offset = 0
                    processed = 0
                    
                    while offset < input_tile_count:
                        cursor_input.execute(
                            "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles LIMIT ? OFFSET ?",
                            (batch_size, offset)
                        )
                        
                        batch_tiles = cursor_input.fetchall()
                        if not batch_tiles:
                            break
                        
                        # 批量插入瓦片
                        # 直接使用batch_tiles作为参数，避免额外的列表构建
                        # 使用INSERT OR IGNORE跳过已存在的瓦片
                        cursor_output.executemany(
                            '''INSERT OR IGNORE INTO tiles 
                            (zoom_level, tile_column, tile_row, tile_data) 
                            VALUES (?, ?, ?, ?)''',
                            batch_tiles
                        )
                        
                        # 计算统计信息
                        success_count = cursor_output.rowcount
                        skipped_count = len(batch_tiles) - success_count
                        total_success += success_count
                        total_skipped += skipped_count
                        processed += len(batch_tiles)
                        
                        # 显示进度
                        progress = (processed / input_tile_count) * 100
                        print(f"   进度: {progress:.1f}% ({processed}/{input_tile_count}) - 成功: {success_count}, 跳过: {skipped_count}")
                        
                        conn_output.commit()
                        offset += batch_size
                    
                    conn_input.close()
                    
                except sqlite3.Error as e:
                    print(f"✗ 处理文件失败: {e}")
                    total_failed += 1
                except Exception as e:
                    print(f"✗ 处理文件失败: {e}")
                    total_failed += 1
            
            # 插入元数据（使用第一个文件的元数据）
            if input_paths:
                try:
                    conn_first = sqlite3.connect(input_paths[0])
                    cursor_first = conn_first.cursor()
                    cursor_first.execute("SELECT name, value FROM metadata")
                    first_metadata = cursor_first.fetchall()
                    cursor_output.executemany('INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)', first_metadata)
                    conn_output.commit()
                    conn_first.close()
                except Exception as e:
                    print(f"✗ 插入元数据失败: {e}")
            
            conn_output.close()
            
            end_time = time.time()
            elapsed_time = end_time - start_time
            
            print(f"\n✓ 合并完成！")
            print(f"   成功: {total_success}")
            print(f"   跳过: {total_skipped}")
            print(f"   失败: {total_failed}")
            print(f"   耗时: {elapsed_time:.2f} 秒")
            print(f"✓ 输出MBTiles: {output_path}")
            
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 合并失败: {e}")
            return False
    
    def split_mbtiles(self, input_path, output_dir, zoom_levels, max_workers=None):
        """
        按zoom级别拆分MBTiles文件
        
        Args:
            input_path: 输入MBTiles文件路径
            output_dir: 输出目录
            zoom_levels: 要拆分的缩放级别列表
            max_workers: 最大线程数
            
        Returns:
            bool: 是否拆分成功
        """
        # 验证输入文件是否存在
        if not os.path.exists(input_path):
            print(f"✗ 输入文件不存在: {input_path}")
            return False
        
        # 创建输出目录
        ensure_directory(output_dir)
        
        try:
            # 连接输入MBTiles数据库
            conn_input = sqlite3.connect(input_path)
            cursor_input = conn_input.cursor()
            
            # 优化SQLite配置
            conn_input.execute('PRAGMA journal_mode=WAL;')
            conn_input.execute('PRAGMA cache_size=1000000;')
            conn_input.execute('PRAGMA synchronous=NORMAL;')
            conn_input.execute('PRAGMA wal_autocheckpoint=1000;')
            conn_input.execute('PRAGMA busy_timeout=30000;')
            
            # 获取元数据
            cursor_input.execute("SELECT name, value FROM metadata")
            metadata = dict(cursor_input.fetchall())
            print(f"✓ 读取MBTiles元数据: {metadata.get('name', 'Unknown')}")
            
            # 验证缩放级别是否存在
            cursor_input.execute("SELECT DISTINCT zoom_level FROM tiles ORDER BY zoom_level")
            available_zoom_levels = [row[0] for row in cursor_input.fetchall()]
            print(f"   可用缩放级别: {available_zoom_levels}")
            
            # 过滤有效的缩放级别
            valid_zoom_levels = [z for z in zoom_levels if z in available_zoom_levels]
            if not valid_zoom_levels:
                print("✗ 没有有效的缩放级别")
                conn_input.close()
                return False
            
            print(f"   要拆分的缩放级别: {valid_zoom_levels}")
            
            # 处理每个缩放级别
            print(f"✓ 开始拆分，共 {len(valid_zoom_levels)} 个缩放级别...")
            start_time = time.time()
            
            for zoom in valid_zoom_levels:
                # 计算当前缩放级别的瓦片数量
                cursor_input.execute(
                    "SELECT COUNT(*) FROM tiles WHERE zoom_level = ?",
                    (zoom,)
                )
                zoom_tile_count = cursor_input.fetchone()[0]
                print(f"\n✓ 处理缩放级别 {zoom}，共 {zoom_tile_count} 个瓦片")
                
                # 创建输出MBTiles文件
                output_file = os.path.join(output_dir, f"tiles_zoom_{zoom}.mbtiles")
                conn_output = sqlite3.connect(output_file)
                cursor_output = conn_output.cursor()
                
                # 优化SQLite配置
                conn_output.execute('PRAGMA journal_mode=WAL;')
                conn_output.execute('PRAGMA cache_size=1000000;')
                conn_output.execute('PRAGMA synchronous=NORMAL;')
                conn_output.execute('PRAGMA wal_autocheckpoint=1000;')
                conn_output.execute('PRAGMA busy_timeout=30000;')
                
                # 创建表结构
                cursor_output.execute('''
                    CREATE TABLE IF NOT EXISTS tiles (
                        zoom_level INTEGER,
                        tile_column INTEGER,
                        tile_row INTEGER,
                        tile_data BLOB,
                        PRIMARY KEY (zoom_level, tile_column, tile_row)
                    )
                ''')
                
                cursor_output.execute('''
                    CREATE TABLE IF NOT EXISTS metadata (
                        name TEXT,
                        value TEXT,
                        PRIMARY KEY (name)
                    )
                ''')
                
                # 插入元数据
                metadata_list = [(k, v) for k, v in metadata.items()]
                cursor_output.executemany('INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)', metadata_list)
                conn_output.commit()
                
                # 增大批处理大小以提高性能
                batch_size = 5000  # 每批处理5000个瓦片
                offset = 0
                processed = 0
                
                while offset < zoom_tile_count:
                    cursor_input.execute(
                        "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles WHERE zoom_level = ? LIMIT ? OFFSET ?",
                        (zoom, batch_size, offset)
                    )
                    
                    batch_tiles = cursor_input.fetchall()
                    if not batch_tiles:
                        break
                    
                    # 批量插入瓦片
                    cursor_output.executemany(
                        '''INSERT OR REPLACE INTO tiles 
                        (zoom_level, tile_column, tile_row, tile_data) 
                        VALUES (?, ?, ?, ?)''',
                        batch_tiles
                    )
                    
                    processed += len(batch_tiles)
                    progress = (processed / zoom_tile_count) * 100
                    print(f"   进度: {progress:.1f}% ({processed}/{zoom_tile_count})")
                    
                    conn_output.commit()
                    offset += batch_size
                
                conn_output.close()
                print(f"   输出文件: {output_file}")
            
            conn_input.close()
            
            end_time = time.time()
            elapsed_time = end_time - start_time
            
            print(f"\n✓ 拆分完成！")
            print(f"   处理缩放级别: {valid_zoom_levels}")
            print(f"   耗时: {elapsed_time:.2f} 秒")
            print(f"✓ 输出目录: {output_dir}")
            
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 拆分失败: {e}")
            return False
    
    def compare_mbtiles(self, file1_path, file2_path):
        """
        比较两个MBTiles文件是否相同
        
        Args:
            file1_path: 第一个MBTiles文件路径
            file2_path: 第二个MBTiles文件路径
            
        Returns:
            bool: 是否比较成功
        """
        # 验证输入文件是否存在
        if not os.path.exists(file1_path):
            print(f"✗ 第一个文件不存在: {file1_path}")
            return False
        if not os.path.exists(file2_path):
            print(f"✗ 第二个文件不存在: {file2_path}")
            return False
        
        try:
            print(f"✓ 开始比较两个MBTiles文件...")
            print(f"   文件1: {file1_path}")
            print(f"   文件2: {file2_path}")
            
            # 连接第一个数据库
            conn1 = sqlite3.connect(file1_path)
            cursor1 = conn1.cursor()
            
            # 连接第二个数据库
            conn2 = sqlite3.connect(file2_path)
            cursor2 = conn2.cursor()
            
            # 比较元数据
            print("\n=== 比较元数据 ===")
            cursor1.execute("SELECT name, value FROM metadata ORDER BY name")
            metadata1 = dict(cursor1.fetchall())
            
            cursor2.execute("SELECT name, value FROM metadata ORDER BY name")
            metadata2 = dict(cursor2.fetchall())
            
            if metadata1 == metadata2:
                print("✓ 元数据相同")
            else:
                print("✗ 元数据不同")
                # 显示差异
                for key in set(metadata1.keys()) | set(metadata2.keys()):
                    if key not in metadata1:
                        print(f"   文件1缺少键: {key}")
                    elif key not in metadata2:
                        print(f"   文件2缺少键: {key}")
                    elif metadata1[key] != metadata2[key]:
                        print(f"   键 '{key}' 不同: 文件1='{metadata1[key]}', 文件2='{metadata2[key]}'")
            
            # 比较瓦片数量
            print("\n=== 比较瓦片数量 ===")
            cursor1.execute("SELECT COUNT(*) FROM tiles")
            count1 = cursor1.fetchone()[0]
            
            cursor2.execute("SELECT COUNT(*) FROM tiles")
            count2 = cursor2.fetchone()[0]
            
            print(f"   文件1瓦片数: {count1}")
            print(f"   文件2瓦片数: {count2}")
            
            if count1 == count2:
                print("✓ 瓦片数量相同")
            else:
                print("✗ 瓦片数量不同")
            
            # 比较缩放级别分布
            print("\n=== 比较缩放级别分布 ===")
            cursor1.execute("SELECT zoom_level, COUNT(*) FROM tiles GROUP BY zoom_level ORDER BY zoom_level")
            zoom_dist1 = dict(cursor1.fetchall())
            
            cursor2.execute("SELECT zoom_level, COUNT(*) FROM tiles GROUP BY zoom_level ORDER BY zoom_level")
            zoom_dist2 = dict(cursor2.fetchall())
            
            if zoom_dist1 == zoom_dist2:
                print("✓ 缩放级别分布相同")
            else:
                print("✗ 缩放级别分布不同")
                # 显示差异
                for zoom in set(zoom_dist1.keys()) | set(zoom_dist2.keys()):
                    if zoom not in zoom_dist1:
                        print(f"   文件1缺少缩放级别: {zoom}")
                    elif zoom not in zoom_dist2:
                        print(f"   文件2缺少缩放级别: {zoom}")
                    elif zoom_dist1[zoom] != zoom_dist2[zoom]:
                        print(f"   缩放级别 {zoom} 瓦片数不同: 文件1={zoom_dist1[zoom]}, 文件2={zoom_dist2[zoom]}")
            
            conn1.close()
            conn2.close()
            
            print("\n✓ 比较完成！")
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 比较失败: {e}")
            return False

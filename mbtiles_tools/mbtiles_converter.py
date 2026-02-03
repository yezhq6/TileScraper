#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MBTiles转换工具核心模块

包含MBTilesConverter类，实现核心转换功能
"""

import os
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from coordinate_converter import CoordinateConverter


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
        Path(os.path.dirname(mbtiles_path)).mkdir(parents=True, exist_ok=True)
        
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
            
            # 收集所有瓦片文件
            tiles_info = []
            for zoom in convert_zoom_levels:
                zoom_dir = os.path.join(input_dir, str(zoom))
                x_dirs = [d for d in os.listdir(zoom_dir) if os.path.isdir(os.path.join(zoom_dir, d)) and d.isdigit()]
                
                for x_str in x_dirs:
                    x = int(x_str)
                    x_dir = os.path.join(zoom_dir, x_str)
                    
                    # 获取所有图片文件（支持jpg、png、jpeg）
                    image_files = [f for f in os.listdir(x_dir) 
                                 if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
                    
                    for image_file in image_files:
                        # 提取y坐标
                        y = int(os.path.splitext(image_file)[0])
                        input_path = os.path.join(x_dir, image_file)
                        
                        tiles_info.append({
                            'zoom': zoom,
                            'x': x,
                            'y': y,
                            'input_path': input_path,
                            'scheme': scheme
                        })
            
            print(f"✓ 找到 {len(tiles_info)} 个瓦片")
            
            # 创建MBTiles数据库，使用WAL模式和优化配置
            conn = sqlite3.connect(mbtiles_path)
            cursor = conn.cursor()
            
            # 优化SQLite配置
            conn.execute('PRAGMA journal_mode=WAL;')  # 启用WAL模式，提高并发性能
            conn.execute('PRAGMA cache_size=1000000;')  # 增加缓存大小，约1GB
            conn.execute('PRAGMA synchronous=NORMAL;')  # 同步模式设为NORMAL，权衡安全性和性能
            conn.execute('PRAGMA wal_autocheckpoint=1000;')  # 定期检查点，合并WAL文件
            conn.execute('PRAGMA busy_timeout=30000;')  # 设置锁定超时为30秒
            
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
            
            # 确定格式
            format_type = 'jpg'  # 默认格式
            if tiles_info:
                # 根据第一个文件的扩展名确定格式
                first_ext = os.path.splitext(tiles_info[0]['input_path'])[1].lower()
                if first_ext in ['.png']:
                    format_type = 'png'
            
            # 插入元数据
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
            
            print(f"✓ 初始化MBTiles数据库: {mbtiles_path}")
            print(f"   设置scheme: {scheme}")
            print(f"   格式: {format_type}")
            
            # 使用线程池进行并行转换
            print(f"✓ 开始转换，共 {len(tiles_info)} 个瓦片...")
            start_time = time.time()
            success_count = 0
            failed_count = 0
            
            # 批量处理参数
            batch_size = 1000  # 每批处理1000个瓦片
            batch = []
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                future_to_tile = {executor.submit(self._process_tile, tile_info): tile_info for tile_info in tiles_info}
                
                # 处理完成的任务
                for future in as_completed(future_to_tile):
                    tile_info = future_to_tile[future]
                    try:
                        result = future.result()
                        if result:
                            batch.append(result)
                            success_count += 1
                            
                            # 每batch_size个瓦片批量插入一次
                            if len(batch) >= batch_size:
                                # 使用executemany进行批量插入
                                cursor.executemany(
                                    '''INSERT OR REPLACE INTO tiles 
                                    (zoom_level, tile_column, tile_row, tile_data) 
                                    VALUES (?, ?, ?, ?)''',
                                    batch
                                )
                                conn.commit()
                                batch = []
                    except Exception as e:
                        print(f"✗ 处理瓦片失败: {tile_info['input_path']}")
                        print(f"   错误信息: {e}")
                        failed_count += 1
            
            # 插入剩余的瓦片
            if batch:
                cursor.executemany(
                    '''INSERT OR REPLACE INTO tiles 
                    (zoom_level, tile_column, tile_row, tile_data) 
                    VALUES (?, ?, ?, ?)''',
                    batch
                )
                conn.commit()
            conn.close()
            
            end_time = time.time()
            elapsed_time = end_time - start_time
            
            print(f"\n✓ 转换完成！")
            print(f"   成功: {success_count}")
            print(f"   失败: {failed_count}")
            print(f"   耗时: {elapsed_time:.2f} 秒")
            print(f"   平均速度: {len(tiles_info)/elapsed_time:.2f} 个/秒")
            print(f"✓ 输出MBTiles: {mbtiles_path}")
            
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 转换失败: {e}")
            return False
    
    def convert_mbtiles_to_directory(self, mbtiles_path, output_dir, scheme=None, max_workers=None, zoom_levels=None):
        """
        将MBTiles转换为目录结构
        
        Args:
            mbtiles_path: MBTiles文件路径
            output_dir: 输出目录
            scheme: 输出目录的坐标系统 ('xyz' 或 'tms')，None表示询问用户
            max_workers: 最大线程数
            zoom_levels: 要提取的缩放级别列表，None表示提取所有级别
            
        Returns:
            bool: 是否转换成功
        """
        # 验证MBTiles文件是否存在
        if not os.path.exists(mbtiles_path):
            print(f"✗ MBTiles文件不存在: {mbtiles_path}")
            return False
        
        # 创建输出目录
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
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
            
            # 收集所有瓦片信息，使用分批获取的方式减少内存使用
            tiles_info = []
            batch_size = 1000  # 每批获取1000个瓦片
            
            for zoom in extract_zoom_levels:
                # 计算当前缩放级别的瓦片数量
                cursor.execute(
                    "SELECT COUNT(*) FROM tiles WHERE zoom_level = ?",
                    (zoom,)
                )
                zoom_tile_count = cursor.fetchone()[0]
                print(f"   缩放级别 {zoom}: {zoom_tile_count} 个瓦片")
                
                # 分批获取瓦片数据
                offset = 0
                while offset < zoom_tile_count:
                    cursor.execute(
                        "SELECT tile_column, tile_row, tile_data FROM tiles WHERE zoom_level = ? LIMIT ? OFFSET ?",
                        (zoom, batch_size, offset)
                    )
                    
                    batch_tiles = cursor.fetchall()
                    if not batch_tiles:
                        break
                    
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
                    
                    offset += batch_size
                    
                    # 每处理一批后清理内存
                    if len(tiles_info) >= 5000:
                        # 立即处理当前批次的瓦片
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            # 提交当前批次的任务
                            future_to_tile = {executor.submit(self._save_tile, tile_info): tile_info for tile_info in tiles_info}
                            
                            # 处理完成的任务
                            for future in as_completed(future_to_tile):
                                try:
                                    future.result()
                                except Exception as e:
                                    pass
                        
                        # 清空瓦片信息列表，释放内存
                        tiles_info.clear()
                        print(f"   已处理 {offset} 个瓦片...")
            
            conn.close()
            
            # 处理剩余的瓦片数据
            success_count = 0
            failed_count = 0
            
            if tiles_info:
                print(f"✓ 处理剩余的 {len(tiles_info)} 个瓦片...")
                start_time = time.time()
                
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # 提交所有任务
                    future_to_tile = {executor.submit(self._save_tile, tile_info): tile_info for tile_info in tiles_info}
                    
                    # 处理完成的任务
                    for future in as_completed(future_to_tile):
                        tile_info = future_to_tile[future]
                        try:
                            result = future.result()
                            if result:
                                success_count += 1
                            else:
                                failed_count += 1
                        except Exception as e:
                            print(f"✗ 保存瓦片失败: {tile_info['output_path']}")
                            print(f"   错误信息: {e}")
                            failed_count += 1
            else:
                start_time = time.time()
                print(f"✓ 所有瓦片已处理完成")
            
            end_time = time.time()
            elapsed_time = end_time - start_time
            
            print(f"\n✓ 转换完成！")
            print(f"   成功: {success_count}")
            print(f"   失败: {failed_count}")
            print(f"   耗时: {elapsed_time:.2f} 秒")
            print(f"   平均速度: {len(tiles_info)/elapsed_time:.2f} 个/秒")
            print(f"✓ 输出目录: {output_dir}")
            
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 转换失败: {e}")
            return False
    
    def merge_mbtiles(self, input_files, output_file, max_workers=None):
        """
        合并多个MBTiles文件
        
        Args:
            input_files: 输入MBTiles文件列表
            output_file: 输出MBTiles文件路径
            max_workers: 最大线程数
            
        Returns:
            bool: 是否合并成功
        """
        # 验证输入文件
        for input_file in input_files:
            if not os.path.exists(input_file):
                print(f"✗ 输入文件不存在: {input_file}")
                return False
        
        # 创建输出目录
        Path(os.path.dirname(output_file)).mkdir(parents=True, exist_ok=True)
        
        try:
            # 创建输出MBTiles数据库，使用WAL模式和优化配置
            output_conn = sqlite3.connect(output_file)
            output_cursor = output_conn.cursor()
            
            # 优化SQLite配置
            output_conn.execute('PRAGMA journal_mode=WAL;')  # 启用WAL模式，提高并发性能
            output_conn.execute('PRAGMA cache_size=1000000;')  # 增加缓存大小，约1GB
            output_conn.execute('PRAGMA synchronous=NORMAL;')  # 同步模式设为NORMAL，权衡安全性和性能
            output_conn.execute('PRAGMA wal_autocheckpoint=1000;')  # 定期检查点，合并WAL文件
            output_conn.execute('PRAGMA busy_timeout=30000;')  # 设置锁定超时为30秒
            
            # 创建表结构
            output_cursor.execute('''
                CREATE TABLE IF NOT EXISTS tiles (
                    zoom_level INTEGER,
                    tile_column INTEGER,
                    tile_row INTEGER,
                    tile_data BLOB,
                    PRIMARY KEY (zoom_level, tile_column, tile_row)
                )
            ''')
            
            output_cursor.execute('''
                CREATE TABLE IF NOT EXISTS metadata (
                    name TEXT,
                    value TEXT,
                    PRIMARY KEY (name)
                )
            ''')
            
            # 读取第一个输入文件的元数据，获取format
            format_type = 'jpg'  # 默认值
            scheme_type = 'xyz'  # 默认值
            
            # 首先读取第一个MBTiles文件的元数据，获取format和scheme
            first_input_conn = sqlite3.connect(input_files[0])
            first_input_cursor = first_input_conn.cursor()
            first_input_cursor.execute("SELECT name, value FROM metadata")
            first_metadata = dict(first_input_cursor.fetchall())
            
            if 'format' in first_metadata:
                format_type = first_metadata['format']
            if 'scheme' in first_metadata:
                scheme_type = first_metadata['scheme']
            
            first_input_conn.close()
            
            # 验证所有输入文件的格式是否一致
            for input_file in input_files[1:]:
                input_conn = sqlite3.connect(input_file)
                input_cursor = input_conn.cursor()
                input_cursor.execute("SELECT name, value FROM metadata")
                metadata = dict(input_cursor.fetchall())
                input_format = metadata.get('format', 'png')
                
                if input_format != format_type:
                    print(f"✗ 警告: 输入文件 {input_file} 的格式 ({input_format}) 与第一个文件的格式 ({format_type}) 不一致")
                
                input_conn.close()
            
            # 插入基础元数据
            metadata = [
                ('name', 'Merged MBTiles'),
                ('type', 'baselayer'),
                ('version', '1.0'),
                ('description', 'Merged by MBTiles Converter'),
                ('format', format_type),
                ('scheme', scheme_type),
            ]
            output_cursor.executemany('INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)', metadata)
            output_conn.commit()
            
            total_tiles = 0
            
            for input_file in input_files:
                print(f"✓ 处理输入文件: {input_file}")
                
                # 连接输入MBTiles
                input_conn = sqlite3.connect(input_file)
                input_cursor = input_conn.cursor()
                
                # 获取输入文件的瓦片数
                input_cursor.execute("SELECT COUNT(*) FROM tiles")
                input_tiles_count = input_cursor.fetchone()[0]
                print(f"   瓦片数: {input_tiles_count}")
                
                # 分批获取和插入瓦片数据，减少内存使用
                batch_size = 1000  # 每批处理1000个瓦片
                offset = 0
                
                # 获取输入文件的瓦片总数
                input_cursor.execute("SELECT COUNT(*) FROM tiles")
                input_tiles_count = input_cursor.fetchone()[0]
                
                while offset < input_tiles_count:
                    # 分批获取瓦片数据
                    input_cursor.execute(
                        "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles LIMIT ? OFFSET ?",
                        (batch_size, offset)
                    )
                    
                    tiles = input_cursor.fetchall()
                    if not tiles:
                        break
                    
                    # 批量插入到输出文件
                    if tiles:
                        output_cursor.executemany(
                            "INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)",
                            tiles
                        )
                        output_conn.commit()
                        total_tiles += len(tiles)
                        
                        # 显示处理进度
                        processed = min(offset + len(tiles), input_tiles_count)
                        print(f"   处理进度: {processed}/{input_tiles_count} 个瓦片")
                    
                    offset += batch_size
                
                input_conn.close()
            
            output_conn.close()
            
            print(f"\n✓ 合并完成！")
            print(f"   合并文件数: {len(input_files)}")
            print(f"   总瓦片数: {total_tiles}")
            print(f"✓ 输出MBTiles: {output_file}")
            
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 合并失败: {e}")
            return False
    
    def split_mbtiles(self, mbtiles_path, output_dir, zoom_levels, max_workers=None):
        """
        按zoom级别拆分MBTiles文件
        
        Args:
            mbtiles_path: 输入MBTiles文件路径
            output_dir: 输出目录
            zoom_levels: 要拆分的缩放级别列表
            max_workers: 最大线程数
            
        Returns:
            bool: 是否拆分成功
        """
        # 验证MBTiles文件是否存在
        if not os.path.exists(mbtiles_path):
            print(f"✗ MBTiles文件不存在: {mbtiles_path}")
            return False
        
        # 创建输出目录
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
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
            
            # 获取所有缩放级别
            cursor.execute("SELECT DISTINCT zoom_level FROM tiles ORDER BY zoom_level")
            all_zoom_levels = [row[0] for row in cursor.fetchall()]
            
            # 验证要拆分的缩放级别
            valid_zoom_levels = [z for z in zoom_levels if z in all_zoom_levels]
            if not valid_zoom_levels:
                print(f"✗ 没有有效的缩放级别，可用级别: {all_zoom_levels}")
                conn.close()
                return False
            
            print(f"✓ 可用缩放级别: {all_zoom_levels}")
            print(f"   要拆分的缩放级别: {valid_zoom_levels}")
            
            # 获取元数据
            cursor.execute("SELECT name, value FROM metadata")
            metadata = dict(cursor.fetchall())
            
            for zoom in valid_zoom_levels:
                print(f"\n✓ 处理缩放级别: {zoom}")
                
                # 创建输出MBTiles文件
                output_mbtiles = os.path.join(output_dir, f"tiles_zoom{zoom}.mbtiles")
                
                # 创建新的MBTiles数据库
                output_conn = sqlite3.connect(output_mbtiles)
                output_cursor = output_conn.cursor()
                
                # 创建表结构
                output_cursor.execute('''
                    CREATE TABLE IF NOT EXISTS tiles (
                        zoom_level INTEGER,
                        tile_column INTEGER,
                        tile_row INTEGER,
                        tile_data BLOB,
                        PRIMARY KEY (zoom_level, tile_column, tile_row)
                    )
                ''')
                
                output_cursor.execute('''
                    CREATE TABLE IF NOT EXISTS metadata (
                        name TEXT,
                        value TEXT,
                        PRIMARY KEY (name)
                    )
                ''')
                
                # 插入元数据
                zoom_metadata = metadata.copy()
                zoom_metadata['name'] = f"Tiles Zoom {zoom}"
                zoom_metadata['description'] = f"Zoom level {zoom} extracted by MBTiles Converter"
                # 确保scheme字段存在
                if 'scheme' not in zoom_metadata:
                    zoom_metadata['scheme'] = 'xyz'
                
                output_cursor.executemany(
                    "INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)",
                    zoom_metadata.items()
                )
                
                # 分批获取和插入当前缩放级别的瓦片数据，减少内存使用
                batch_size = 1000  # 每批处理1000个瓦片
                offset = 0
                
                # 获取当前缩放级别的瓦片总数
                cursor.execute(
                    "SELECT COUNT(*) FROM tiles WHERE zoom_level = ?",
                    (zoom,)
                )
                zoom_tile_count = cursor.fetchone()[0]
                print(f"   瓦片数: {zoom_tile_count}")
                
                while offset < zoom_tile_count:
                    # 分批获取瓦片数据
                    cursor.execute(
                        "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles WHERE zoom_level = ? LIMIT ? OFFSET ?",
                        (zoom, batch_size, offset)
                    )
                    
                    tiles = cursor.fetchall()
                    if not tiles:
                        break
                    
                    # 批量插入瓦片数据
                    if tiles:
                        output_cursor.executemany(
                            "INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)",
                            tiles
                        )
                        output_conn.commit()
                        
                        # 显示处理进度
                        processed = min(offset + len(tiles), zoom_tile_count)
                        if processed % (batch_size * 5) == 0:
                            print(f"   处理进度: {processed}/{zoom_tile_count} 个瓦片")
                    
                    offset += batch_size
                
                output_conn.close()
                print(f"   输出文件: {output_mbtiles}")
            
            conn.close()
            
            print(f"\n✓ 拆分完成！")
            print(f"   拆分缩放级别: {valid_zoom_levels}")
            print(f"   输出目录: {output_dir}")
            
            return True
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return False
        except Exception as e:
            print(f"✗ 拆分失败: {e}")
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
    
    def _save_tile(self, tile_info):
        """
        保存单个瓦片到文件
        
        Args:
            tile_info: 瓦片信息字典
            
        Returns:
            bool: 是否保存成功
        """
        try:
            # 创建目录
            Path(tile_info['x_dir']).mkdir(parents=True, exist_ok=True)
            
            # 保存瓦片数据
            with open(tile_info['output_path'], 'wb') as f:
                f.write(tile_info['tile_data'])
            
            return True
        except Exception as e:
            print(f"✗ 保存瓦片失败: {tile_info['output_path']}")
            print(f"   错误信息: {e}")
            return False
    
    def compare_mbtiles(self, mbtiles_file1, mbtiles_file2):
        """
        比较两个MBTiles文件是否相同
        
        Args:
            mbtiles_file1: 第一个MBTiles文件路径
            mbtiles_file2: 第二个MBTiles文件路径
            
        Returns:
            dict: 比较结果，包含相同瓦片数、不同瓦片数、仅在第一个文件中存在的瓦片数、仅在第二个文件中存在的瓦片数
        """
        # 验证输入文件
        for file_path in [mbtiles_file1, mbtiles_file2]:
            if not os.path.exists(file_path):
                print(f"✗ 文件不存在: {file_path}")
                return None
        
        try:
            # 读取第一个MBTiles文件的瓦片数据
            print(f"✓ 读取第一个MBTiles文件: {mbtiles_file1}")
            conn1 = sqlite3.connect(mbtiles_file1)
            cursor1 = conn1.cursor()
            
            # 获取元数据
            cursor1.execute("SELECT name, value FROM metadata")
            metadata1 = dict(cursor1.fetchall())
            print(f"   名称: {metadata1.get('name', 'Unknown')}")
            print(f"   格式: {metadata1.get('format', 'Unknown')}")
            
            # 获取scheme并验证
            scheme1 = metadata1.get('scheme', 'xyz').lower()
            print(f"   scheme: {scheme1}")
            if scheme1 not in ['xyz', 'tms']:
                print(f"✗ 第一个文件的scheme无效: {scheme1}，必须是 'xyz' 或 'tms'")
                conn1.close()
                return None
            
            # 获取瓦片数据
            cursor1.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles")
            tiles1 = cursor1.fetchall()
            tiles1_dict = {(z, x, y): data for z, x, y, data in tiles1}
            print(f"   瓦片数: {len(tiles1)}")
            
            conn1.close()
            
            # 读取第二个MBTiles文件的瓦片数据
            print(f"\n✓ 读取第二个MBTiles文件: {mbtiles_file2}")
            conn2 = sqlite3.connect(mbtiles_file2)
            cursor2 = conn2.cursor()
            
            # 获取元数据
            cursor2.execute("SELECT name, value FROM metadata")
            metadata2 = dict(cursor2.fetchall())
            print(f"   名称: {metadata2.get('name', 'Unknown')}")
            print(f"   格式: {metadata2.get('format', 'Unknown')}")
            
            # 获取scheme并验证
            scheme2 = metadata2.get('scheme', 'xyz').lower()
            print(f"   scheme: {scheme2}")
            if scheme2 not in ['xyz', 'tms']:
                print(f"✗ 第二个文件的scheme无效: {scheme2}，必须是 'xyz' 或 'tms'")
                conn2.close()
                return None
            
            # 获取瓦片数据
            cursor2.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles")
            tiles2 = cursor2.fetchall()
            tiles2_dict = {(z, x, y): data for z, x, y, data in tiles2}
            print(f"   瓦片数: {len(tiles2)}")
            
            conn2.close()
            
            # 比较瓦片
            print("\n✓ 开始比较瓦片...")
            
            # 计算相同的瓦片
            same_tiles = 0
            different_tiles = 0
            
            # 检查第一个文件中的瓦片
            for key, data1 in tiles1_dict.items():
                if key in tiles2_dict:
                    if data1 == tiles2_dict[key]:
                        same_tiles += 1
                    else:
                        different_tiles += 1
            
            # 计算仅在第一个文件中存在的瓦片
            only_in_file1 = len(tiles1_dict) - same_tiles - different_tiles
            
            # 计算仅在第二个文件中存在的瓦片
            only_in_file2 = len(tiles2_dict) - same_tiles - different_tiles
            
            # 输出比较结果
            print("\n✓ 比较完成！")
            print(f"   相同的瓦片数: {same_tiles}")
            print(f"   不同的瓦片数: {different_tiles}")
            print(f"   仅在第一个文件中存在的瓦片数: {only_in_file1}")
            print(f"   仅在第二个文件中存在的瓦片数: {only_in_file2}")
            
            # 如果有不同的瓦片，输出具体信息
            if different_tiles > 0:
                print("\n✗ 不同的瓦片:")
                count = 0
                max_display = 10  # 最多显示10个不同的瓦片
                for key, data1 in tiles1_dict.items():
                    if key in tiles2_dict and data1 != tiles2_dict[key]:
                        z, x, y = key
                        print(f"   Zoom={z}, X={x}, Y={y}")
                        count += 1
                        if count >= max_display:
                            print(f"   ... 还有 {different_tiles - count} 个不同的瓦片")
                            break
            
            # 如果有仅在一个文件中存在的瓦片，输出具体信息
            if only_in_file1 > 0:
                print("\n✗ 仅在第一个文件中存在的瓦片:")
                count = 0
                max_display = 10
                for key in tiles1_dict:
                    if key not in tiles2_dict:
                        z, x, y = key
                        print(f"   Zoom={z}, X={x}, Y={y}")
                        count += 1
                        if count >= max_display:
                            print(f"   ... 还有 {only_in_file1 - count} 个瓦片")
                            break
            
            if only_in_file2 > 0:
                print("\n✗ 仅在第二个文件中存在的瓦片:")
                count = 0
                max_display = 10
                for key in tiles2_dict:
                    if key not in tiles1_dict:
                        z, x, y = key
                        print(f"   Zoom={z}, X={x}, Y={y}")
                        count += 1
                        if count >= max_display:
                            print(f"   ... 还有 {only_in_file2 - count} 个瓦片")
                            break
            
            # 生成比较结果
            result = {
                'same_tiles': same_tiles,
                'different_tiles': different_tiles,
                'only_in_file1': only_in_file1,
                'only_in_file2': only_in_file2,
                'total_tiles_file1': len(tiles1_dict),
                'total_tiles_file2': len(tiles2_dict)
            }
            
            return result
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return None
        except Exception as e:
            print(f"✗ 比较失败: {e}")
            return None
    
    def analyze_mbtiles(self, mbtiles_path):
        """
        分析MBTiles文件的元数据、瓦片数据、层级分布和经纬度范围
        
        Args:
            mbtiles_path: MBTiles文件路径
            
        Returns:
            dict: 分析结果，包含元数据、瓦片统计、层级分布和经纬度范围
        """
        # 验证输入文件
        if not os.path.exists(mbtiles_path):
            print(f"✗ 文件不存在: {mbtiles_path}")
            return None
        
        try:
            # 连接MBTiles数据库
            print(f"✓ 读取MBTiles文件: {mbtiles_path}")
            conn = sqlite3.connect(mbtiles_path)
            cursor = conn.cursor()
            
            # 获取元数据
            cursor.execute("SELECT name, value FROM metadata")
            metadata = dict(cursor.fetchall())
            print(f"\n✓ 元数据:")
            for key, value in metadata.items():
                print(f"   {key}: {value}")
            
            # 获取scheme
            scheme = metadata.get('scheme', 'xyz').lower()
            print(f"\n✓ 坐标系统: {scheme.upper()}")
            
            # 获取瓦片统计
            cursor.execute("SELECT COUNT(*) FROM tiles")
            total_tiles = cursor.fetchone()[0]
            print(f"✓ 总瓦片数: {total_tiles}")
            
            # 获取层级分布
            cursor.execute("SELECT zoom_level, COUNT(*) FROM tiles GROUP BY zoom_level ORDER BY zoom_level")
            zoom_distribution = cursor.fetchall()
            print(f"\n✓ 层级分布:")
            for zoom, count in zoom_distribution:
                print(f"   Zoom={zoom}: {count} 个瓦片")
            
            # 计算经纬度范围
            print(f"\n✓ 经纬度范围:")
            
            # 分批获取瓦片坐标，减少内存使用
            batch_size = 1000  # 每批处理1000个瓦片
            offset = 0
            total_processed = 0
            
            # 获取总瓦片数
            cursor.execute("SELECT COUNT(*) FROM tiles")
            total_tiles = cursor.fetchone()[0]
            
            # 初始化最小最大坐标
            min_lat, max_lat = 90, -90
            min_lon, max_lon = 180, -180
            
            if total_tiles > 0:
                while offset < total_tiles:
                    # 分批获取瓦片坐标
                    cursor.execute(
                        "SELECT zoom_level, tile_column, tile_row FROM tiles LIMIT ? OFFSET ?",
                        (batch_size, offset)
                    )
                    
                    tiles = cursor.fetchall()
                    if not tiles:
                        break
                    
                    for z, x, y in tiles:
                        # 转换为经纬度
                        lat, lon = self.tile_to_latlon(z, x, y)
                        
                        # 更新最小最大坐标
                        min_lat = min(min_lat, lat)
                        max_lat = max(max_lat, lat)
                        min_lon = min(min_lon, lon)
                        max_lon = max(max_lon, lon)
                    
                    offset += batch_size
                    total_processed = min(offset, total_tiles)
                    
                    # 显示处理进度
                    if total_processed % (batch_size * 5) == 0:
                        print(f"   处理经纬度: {total_processed}/{total_tiles} 个瓦片")
                
                print(f"   最小纬度: {min_lat:.6f}")
                print(f"   最大纬度: {max_lat:.6f}")
                print(f"   最小经度: {min_lon:.6f}")
                print(f"   最大经度: {max_lon:.6f}")
                print(f"   纬度范围: {max_lat - min_lat:.6f}")
                print(f"   经度范围: {max_lon - min_lon:.6f}")
                
                # 生成分析结果
                result = {
                    'metadata': metadata,
                    'total_tiles': total_tiles,
                    'zoom_distribution': dict(zoom_distribution),
                    'bbox': {
                        'min_lat': min_lat,
                        'max_lat': max_lat,
                        'min_lon': min_lon,
                        'max_lon': max_lon
                    }
                }
            else:
                print(f"   无瓦片数据")
                result = {
                    'metadata': metadata,
                    'total_tiles': 0,
                    'zoom_distribution': {},
                    'bbox': None
                }
            
            conn.close()
            
            return result
            
        except sqlite3.Error as e:
            print(f"✗ MBTiles数据库错误: {e}")
            return None
        except Exception as e:
            print(f"✗ 分析失败: {e}")
            return None
    
    def tile_to_latlon(self, zoom, x, y):
        """
        将瓦片坐标转换为经纬度
        
        Args:
            zoom: 缩放级别
            x: 瓦片X坐标
            y: 瓦片Y坐标（TMS格式）
            
        Returns:
            tuple: (纬度, 经度)
        """
        import math
        
        # 转换为XYZ格式的Y坐标
        y_xyz = (2 ** zoom - 1) - y
        
        # 计算经度
        lon = (x / (2 ** zoom)) * 360 - 180
        
        # 计算纬度
        n = math.pi - 2 * math.pi * y_xyz / (2 ** zoom)
        lat = math.degrees(math.atan(math.sinh(n)))
        
        return lat, lon


def png_to_mbtiles(input_dir, mbtiles_path, max_workers=None, zoom_levels=None):
    """
    将PNG目录结构转换为MBTiles
    
    Args:
        input_dir: 输入目录
        mbtiles_path: 输出MBTiles文件路径
        max_workers: 最大线程数
        zoom_levels: 要转换的缩放级别列表，None表示转换所有级别
    """
    # 验证输入目录是否存在
    if not os.path.exists(input_dir):
        print(f"✗ 输入目录不存在: {input_dir}")
        return False
    
    # 创建输出目录
    Path(os.path.dirname(mbtiles_path)).mkdir(parents=True, exist_ok=True)
    
    try:
        # 仅获取 0-23 的数字目录作为缩放级别目录
        zoom_dirs = [d for d in os.listdir(input_dir) 
                     if os.path.isdir(os.path.join(input_dir, d)) and d.isdigit() and 0 <= int(d) <= 23]
        
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
        
        # 创建MBTilesConverter实例
        converter = MBTilesConverter()
        
        # 使用现有的convert_directory_to_mbtiles方法
        return converter.convert_directory_to_mbtiles(
            input_dir=input_dir,
            mbtiles_path=mbtiles_path,
            scheme='xyz',  # PNG目录通常使用xyz坐标系统
            max_workers=max_workers,
            zoom_levels=zoom_levels
        )
    except Exception as e:
        print(f"✗ PNG到MBTiles转换失败: {e}")
        return False

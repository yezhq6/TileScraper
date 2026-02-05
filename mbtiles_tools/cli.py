#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命令行接口模块

处理命令行参数解析和执行相应的功能
"""

import argparse
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbtiles_tools.core import MBTilesConverter, parse_zoom_levels, convert_path


def main():
    """
    主函数，处理命令行参数
    """
    parser = argparse.ArgumentParser(description='MBTiles转换工具')
    
    # 子命令
    subparsers = parser.add_subparsers(dest='command', required=True, help='命令类型')
    
    # 1. MBTiles转目录命令
    mbtiles_to_dir_parser = subparsers.add_parser('mbtiles_to_dir', help='将MBTiles转换为目录结构')
    mbtiles_to_dir_parser.add_argument('-i', '--input', required=True, help='MBTiles文件路径')
    mbtiles_to_dir_parser.add_argument('-o', '--output', required=True, help='输出目录')
    mbtiles_to_dir_parser.add_argument('-s', '--scheme', type=str, default=None, help='输出目录的坐标系统 (xyz/tms)')
    mbtiles_to_dir_parser.add_argument('-w', '--workers', type=int, default=None, help='最大线程数')
    mbtiles_to_dir_parser.add_argument('-z', '--zoom', type=str, nargs='*', help='要提取的缩放级别，支持单个值或范围，如 14 或 8-15')
    mbtiles_to_dir_parser.add_argument('-ow', '--overwrite', type=lambda x: x.lower() == 'true', default=True, help='是否覆盖已存在的文件，默认 True（覆盖），使用 --overwrite false 表示跳过已存在的文件')
    
    # 2. 目录转MBTiles命令
    dir_to_mbtiles_parser = subparsers.add_parser('dir_to_mbtiles', help='将目录结构转换为MBTiles')
    dir_to_mbtiles_parser.add_argument('-i', '--input', required=True, help='输入目录')
    dir_to_mbtiles_parser.add_argument('-o', '--output', required=True, help='输出MBTiles文件路径')
    dir_to_mbtiles_parser.add_argument('-s', '--scheme', type=str, default='xyz', help='输入目录的坐标系统 (xyz/tms)，默认 xyz (不进行坐标转换)')
    dir_to_mbtiles_parser.add_argument('-w', '--workers', type=int, default=None, help='最大线程数')
    dir_to_mbtiles_parser.add_argument('-z', '--zoom', type=str, nargs='*', help='要转换的缩放级别，支持单个值或范围，如 14 或 8-15')
    
    # 3. 合并MBTiles命令
    merge_parser = subparsers.add_parser('merge', help='合并多个MBTiles文件')
    merge_parser.add_argument('-i', '--input', required=True, nargs='+', help='输入MBTiles文件列表')
    merge_parser.add_argument('-o', '--output', required=True, help='输出MBTiles文件路径')
    merge_parser.add_argument('-w', '--workers', type=int, default=None, help='最大线程数')
    
    # 4. 拆分MBTiles命令
    split_parser = subparsers.add_parser('split', help='按zoom级别拆分MBTiles文件')
    split_parser.add_argument('-i', '--input', required=True, help='输入MBTiles文件路径')
    split_parser.add_argument('-o', '--output', required=True, help='输出目录')
    split_parser.add_argument('-z', '--zoom', type=str, nargs='+', required=True, help='要拆分的缩放级别列表，支持单个值或范围，如 14 或 8-15')
    split_parser.add_argument('-w', '--workers', type=int, default=None, help='最大线程数')
    
    # 5. 比较MBTiles命令
    compare_parser = subparsers.add_parser('compare', help='比较两个MBTiles文件是否相同')
    compare_parser.add_argument('-f1', '--file1', required=True, help='第一个MBTiles文件路径')
    compare_parser.add_argument('-f2', '--file2', required=True, help='第二个MBTiles文件路径')
    
    # 6. 分析MBTiles命令
    analyze_parser = subparsers.add_parser('analyze', help='分析MBTiles文件的元数据、瓦片数据、层级分布和经纬度范围')
    analyze_parser.add_argument('-i', '--input', required=True, help='MBTiles文件路径')
    
    args = parser.parse_args()
    
    # 解析缩放级别参数
    zoom_levels = parse_zoom_levels(args.zoom) if hasattr(args, 'zoom') else None
    
    # 创建转换器实例
    converter = MBTilesConverter()
    
    # 执行相应命令
    if args.command == 'mbtiles_to_dir':
        # 转换路径
        input_path = convert_path(args.input)
        output_path = convert_path(args.output)
        # 直接使用 overwrite 参数
        overwrite = args.overwrite
        converter.convert_mbtiles_to_directory(input_path, output_path, args.scheme, args.workers, zoom_levels, overwrite)
    elif args.command == 'dir_to_mbtiles':
        # 转换路径
        input_path = convert_path(args.input)
        output_path = convert_path(args.output)
        converter.convert_directory_to_mbtiles(input_path, output_path, args.scheme, args.workers, zoom_levels)
    elif args.command == 'merge':
        # 转换路径
        input_paths = [convert_path(p) for p in args.input]
        output_path = convert_path(args.output)
        converter.merge_mbtiles(input_paths, output_path, args.workers)
    elif args.command == 'split':
        # 转换路径
        input_path = convert_path(args.input)
        output_path = convert_path(args.output)
        converter.split_mbtiles(input_path, output_path, zoom_levels, args.workers)
    elif args.command == 'compare':
        # 转换路径
        file1_path = convert_path(args.file1)
        file2_path = convert_path(args.file2)
        converter.compare_mbtiles(file1_path, file2_path)
    elif args.command == 'analyze':
        # 转换路径
        input_path = convert_path(args.input)
        converter.analyze_mbtiles(input_path)


if __name__ == "__main__":
    main()

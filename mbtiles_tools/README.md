# MBTiles转换工具

一个功能强大的MBTiles转换工具，支持MBTiles和目录结构的相互转换，以及MBTiles的合并和拆分功能。

## 功能特点

1. **MBTiles转目录**：将MBTiles文件转换为标准的目录结构
2. **目录转MBTiles**：将目录结构转换为MBTiles文件
3. **MBTiles合并**：合并多个MBTiles文件为一个
4. **MBTiles拆分**：按缩放级别拆分MBTiles文件
5. **MBTiles比较**：比较两个MBTiles文件是否相同
6. **MBTiles分析**：分析MBTiles文件的元数据、瓦片数据、层级分布和经纬度范围
7. **多线程支持**：支持并行处理，提高转换效率
8. **选择性转换**：可以选择特定的缩放级别进行转换
9. **详细的转换统计**：显示转换进度、成功/失败数量、耗时等信息
10. **坐标系统支持**：正确处理XYZ和TMS坐标系统之间的转换
11. **多格式支持**：支持jpg、png、jpeg格式的瓦片
12. **跳过/覆盖选项**：支持跳过已存在的文件或覆盖它们

## 安装依赖

所有依赖已合并到项目根目录的`requirements.txt`中，使用以下命令安装：

```bash
pip install -r ../requirements.txt
```

## 使用方法

### 命令格式

```bash
# 设置PYTHONPATH
export PYTHONPATH=$(pwd)

# 运行命令
python mbtiles_tools/cli.py <command> [options]
```

### 命令列表

| 命令 | 描述 |
|------|------|
| `mbtiles_to_dir` | 将MBTiles转换为目录结构 |
| `dir_to_mbtiles` | 将目录结构转换为MBTiles |
| `merge` | 合并多个MBTiles文件 |
| `split` | 按缩放级别拆分MBTiles文件 |
| `compare` | 比较两个MBTiles文件是否相同 |
| `analyze` | 分析MBTiles文件的元数据、瓦片数据、层级分布和经纬度范围 |

## 详细命令说明

### 1. MBTiles转目录

将MBTiles文件转换为标准的目录结构，目录结构为：`zoom/x/y.ext`

```bash
python mbtiles_tools/cli.py mbtiles_to_dir -i <mbtiles_path> -o <output_dir> [-s <scheme>] [-w <workers>] [-z <zoom_levels>] [--no-overwrite]
```

**参数说明**：
- `-i, --input`: MBTiles文件路径（必填）
- `-o, --output`: 输出目录（必填）
- `-s, --scheme`: 输出目录的坐标系统 (xyz/tms)，默认询问用户
- `-w, --workers`: 最大线程数，默认使用CPU核心数
- `-z, --zoom`: 要提取的缩放级别列表，如 `-z 14 15`，默认提取所有级别
- `--no-overwrite`: 跳过已存在的文件，默认覆盖已存在的文件

**示例**：

```bash
# 转换所有缩放级别，使用默认线程数（默认覆盖已存在的文件）
python mbtiles_tools/cli.py mbtiles_to_dir -i tiles.mbtiles -o tiles_dir

# 只转换缩放级别14和15，使用8个线程，输出XYZ格式
python mbtiles_tools/cli.py mbtiles_to_dir -i tiles.mbtiles -o tiles_dir -z 14 15 -w 8 -s xyz

# 转换所有缩放级别，跳过已存在的文件
python mbtiles_tools/cli.py mbtiles_to_dir -i tiles.mbtiles -o tiles_dir --no-overwrite
```

### 2. 目录转MBTiles

将目录结构转换为MBTiles文件

```bash
python mbtiles_tools/cli.py dir_to_mbtiles -i <input_dir> -o <mbtiles_path> [-s <scheme>] [-w <workers>] [-z <zoom_levels>]
```

**参数说明**：
- `-i, --input`: 输入目录，包含瓦片（必填）
- `-o, --output`: 输出MBTiles文件路径（必填）
- `-s, --scheme`: 输入目录的坐标系统 (xyz/tms)，默认 tms (进行坐标转换)
- `-w, --workers`: 最大线程数，默认使用CPU核心数
- `-z, --zoom`: 要转换的缩放级别列表，如 `-z 14 15`，默认转换所有级别

**示例**：

```bash
# 转换所有缩放级别，使用默认线程数（默认进行坐标转换）
python mbtiles_tools/cli.py dir_to_mbtiles -i tiles_dir -o output.mbtiles

# 不进行坐标转换，直接使用XYZ格式
python mbtiles_tools/cli.py dir_to_mbtiles -i tiles_dir -o output.mbtiles -s xyz

# 只转换缩放级别14和15，使用8个线程，进行坐标转换
python mbtiles_tools/cli.py dir_to_mbtiles -i tiles_dir -o output.mbtiles -z 14 15 -w 8 -s tms
```

### 3. MBTiles合并

合并多个MBTiles文件为一个MBTiles文件

```bash
python mbtiles_tools/cli.py merge -i <input_files> -o <output_file> [-w <workers>]
```

**参数说明**：
- `-i, --input`: 输入MBTiles文件列表，如 `-i tiles1.mbtiles tiles2.mbtiles`（必填）
- `-o, --output`: 输出MBTiles文件路径（必填）
- `-w, --workers`: 最大线程数，默认使用CPU核心数

**示例**：

```bash
# 合并两个MBTiles文件
python mbtiles_tools/cli.py merge -i tiles1.mbtiles tiles2.mbtiles -o merged.mbtiles

# 合并多个MBTiles文件，使用4个线程
python mbtiles_tools/cli.py merge -i tiles1.mbtiles tiles2.mbtiles tiles3.mbtiles -o merged.mbtiles -w 4
```

### 4. MBTiles拆分

按缩放级别拆分MBTiles文件，每个缩放级别生成一个独立的MBTiles文件

```bash
python mbtiles_tools/cli.py split -i <mbtiles_path> -o <output_dir> -z <zoom_levels> [-w <workers>]
```

**参数说明**：
- `-i, --input`: 输入MBTiles文件路径（必填）
- `-o, --output`: 输出目录（必填）
- `-z, --zoom`: 要拆分的缩放级别列表，如 `-z 14 15`（必填）
- `-w, --workers`: 最大线程数，默认使用CPU核心数

**示例**：

```bash
# 拆分缩放级别14和15
python mbtiles_tools/cli.py split -i tiles.mbtiles -o split_output -z 14 15

# 拆分缩放级别10到15，使用8个线程
python mbtiles_tools/cli.py split -i tiles.mbtiles -o split_output -z 10 11 12 13 14 15 -w 8
```

### 5. MBTiles比较

比较两个MBTiles文件是否相同，包括元数据和瓦片数据

```bash
python mbtiles_tools/cli.py compare -f1 <file1> -f2 <file2>
```

**参数说明**：
- `-f1, --file1`: 第一个MBTiles文件路径（必填）
- `-f2, --file2`: 第二个MBTiles文件路径（必填）

**示例**：

```bash
# 比较两个MBTiles文件
python mbtiles_tools/cli.py compare -f1 tiles1.mbtiles -f2 tiles2.mbtiles
```

### 6. MBTiles分析

分析MBTiles文件的元数据、瓦片数据、层级分布和经纬度范围

```bash
python mbtiles_tools/cli.py analyze -i <mbtiles_path>
```

**参数说明**：
- `-i, --input`: 输入MBTiles文件路径（必填）

**示例**：

```bash
# 分析MBTiles文件
python mbtiles_tools/cli.py analyze -i tiles.mbtiles
```

## 转换说明

### MBTiles格式

MBTiles是一种基于SQLite的瓦片存储格式，包含以下表：
- `tiles`: 存储瓦片数据，包含`zoom_level`、`tile_column`、`tile_row`和`tile_data`字段
- `metadata`: 存储元数据，如名称、类型、格式、坐标系等

### 坐标系统转换

#### 坐标系统说明
- **XYZ (Slippy Map)**：Y=0 在顶部（北），向下增加
- **TMS**：Y=0 在底部（南），向上增加
- **MBTiles内部**：默认使用TMS坐标系统

#### 转换公式
- **TMS to XYZ**: `y_xyz = (2^zoom - 1) - y_tms`
- **XYZ to TMS**: `y_tms = (2^zoom - 1) - y_xyz`

#### 转换逻辑
- **目录转MBTiles**：
  - 如果scheme='xyz'：直接转换，不进行坐标转换，scheme字段设为'xyz'
  - 如果scheme='tms'：进行坐标转换 (XYZ to TMS)，scheme字段设为'tms'
  - 默认scheme='tms'：进行坐标转换 (XYZ to TMS)，scheme字段设为'tms'

- **MBTiles转目录**：
  - 读取MBTiles文件的scheme字段
  - 根据用户选择的输出坐标系统进行转换

### 性能优化

1. **SQLite数据库优化**：使用WAL模式、优化缓存大小和同步模式，提高数据库操作速度
2. **批量处理**：
   - 目录转MBTiles时，每100个瓦片提交一次事务，减少数据库操作开销
   - MBTiles转目录时，批量获取瓦片数据，减少数据库查询次数
3. **多线程处理**：使用ThreadPoolExecutor并行处理瓦片转换，提高转换速度
4. **最优线程数计算**：根据系统CPU核心数和内存情况，自动计算最优线程数，最大化处理效率
5. **内存管理**：使用生成器和垃圾回收优化，减少内存使用，支持处理大规模数据
6. **实时进度显示**：显示转换进度、详细统计信息和系统状态，方便监控转换状态
7. **系统状态监控**：实时跟踪内存和CPU使用情况，确保系统稳定运行
8. **错误处理**：增强错误处理和日志记录，提高工具稳定性和可靠性

## 示例应用场景

1. **将下载的MBTiles转换为目录**：用于需要标准目录结构的应用
2. **将目录瓦片打包为MBTiles**：方便存储和传输大量瓦片
3. **合并多个MBTiles文件**：将不同区域或缩放级别的MBTiles合并为一个
4. **按缩放级别拆分MBTiles**：生成特定缩放级别的MBTiles，减小文件大小

## 注意事项

1. 转换过程中会创建必要的目录结构
2. 支持的图片格式：jpg、png、jpeg
3. 转换时会覆盖已存在的文件
4. 建议根据系统配置选择合适的线程数
5. 对于大规模转换，建议使用SSD存储以提高性能

## 系统要求

- Python 3.7+
- SQLite 3+

## 许可证

MIT License

# 图片格式转换工具

一个简单易用的图片格式转换工具，支持jpg、jpeg、png格式之间的相互转换，支持单个文件和批量转换。

## 功能特点

- ✅ 支持jpg、jpeg、png格式之间的相互转换
- ✅ 支持单个文件转换
- ✅ 支持批量转换
- ✅ 支持递归处理子目录
- ✅ 保持原始目录结构
- ✅ 自动处理透明通道（转换为jpg时自动转为RGB模式）
- ✅ 支持瓦片数据多线程合并
- ✅ 支持覆盖或跳过已存在的瓦片
- ✅ 显示合并进度和统计信息
- ✅ 最优线程数计算：根据系统CPU核心数和内存情况，自动计算最优线程数
- ✅ 内存管理优化：使用生成器减少内存使用，支持处理大规模数据
- ✅ 实时进度显示：详细的转换进度和统计信息
- ✅ 系统状态监控：实时跟踪内存和CPU使用情况
- ✅ 增强错误处理：完善的错误处理和异常捕获机制
- ✅ 批量处理优化：高效的批量处理算法，提高转换速度

## 安装依赖

所有依赖已合并到项目根目录的`requirements.txt`中，使用以下命令安装：

```bash
pip install -r ../requirements.txt
```

## 使用方法

### 1. 单个文件转换

```bash
python image_converter.py -f <输入文件路径> -t <输出格式> [-o <输出文件路径>]
```

**示例：**

```bash
# 将example.png转换为jpg格式，输出为example.jpg
python image_converter.py -f example.png -t jpg

# 将example.jpg转换为png格式，指定输出文件名为output.png
python image_converter.py -f example.jpg -t png -o output.png
```

### 2. 批量转换

```bash
python image_converter.py -d <输入目录路径> -t <输出格式> [-o <输出目录路径>] [-r]
```

**参数说明：**
- `-d, --directory`: 输入目录路径
- `-t, --type`: 输出格式（必须指定，可选值：jpg, jpeg, png）
- `-o, --output`: 输出目录路径（可选，默认为原目录同级的`[原目录名]_converted`）
- `-r, --recursive`: 递归处理子目录（可选，默认不递归）

**示例：**

```bash
# 将input_dir目录下的所有图片转换为png格式，输出到默认目录
python image_converter.py -d input_dir -t png

# 将input_dir目录及其子目录下的所有图片转换为jpg格式，输出到output_dir
python image_converter.py -d input_dir -t jpg -o output_dir -r
```

## 支持的格式

### 输入格式
- `.jpg`
- `.jpeg`
- `.png`

### 输出格式
- `jpg`
- `jpeg`
- `png`

## 注意事项

1. 转换为jpg/jpeg格式时，透明通道会被自动转换为白色背景
2. 批量转换时会保持原始目录结构
3. 输出目录不存在时会自动创建
4. 相同文件名的文件会被覆盖

## 3. 瓦片合并工具

将一个路径下的瓦片数据合并到另一个路径中，支持多线程处理，可选择覆盖或跳过已存在的瓦片。

```bash
python tile_merger.py -s <源目录路径> -d <目标目录路径> [-w <线程数>] [-o]
```

**参数说明：**
- `-s, --source`: 源瓦片目录路径（必填）
- `-d, --destination`: 目标瓦片目录路径（必填）
- `-w, --workers`: 线程数，默认使用CPU核心数
- `-o, --overwrite`: 是否覆盖已存在的瓦片，默认不覆盖（跳过）

**示例：**

```bash
# 将source_tiles目录的瓦片合并到dest_tiles目录，使用默认线程数，跳过已存在的瓦片
python tile_merger.py -s source_tiles -d dest_tiles

# 将source_tiles目录的瓦片合并到dest_tiles目录，使用32个线程，覆盖已存在的瓦片
python tile_merger.py -s source_tiles -d dest_tiles -w 32 -o
```

## 示例场景

### 场景1：将下载的png瓦片转换为jpg

```bash
python image_converter.py -d /path/to/tiles -t jpg -r
```

### 场景2：将下载的jpg瓦片转换为png

```bash
python image_converter.py -d /path/to/tiles -t png -r
```

### 场景3：将单个瓦片转换为其他格式

```bash
python image_converter.py -f /path/to/tile.png -t jpg
```

### 场景4：合并两个瓦片目录

```bash
python tile_merger.py -s tiles1 -d tiles2 -w 64 -o
```

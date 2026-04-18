# TileHarvester

**TileHarvester** 是一个功能强大的地图瓦片下载工具，支持多种地图提供商，可批量下载地图瓦片并保存到本地。

## 功能特性

- 🗺️ **支持多种地图提供商**：可自定义瓦片服务器URL
- 🚀 **高性能下载**：支持多线程下载，充分利用带宽
- ⏸️ **暂停/继续下载**：灵活控制下载过程
- 📊 **实时进度显示**：直观展示下载进度和统计信息
- 🎯 **精确边界控制**：支持手动输入边界坐标
- 🔧 **灵活配置**：支持自定义输出目录、缩放级别等
- 💡 **友好的Web界面**：基于Flask和Leaflet的交互式地图界面
- 📷 **支持多种图片格式**：支持jpg、jpeg、png格式下载
- 📦 **支持MBTiles格式**：可将瓦片下载为MBTiles文件，便于存储和传输


## 技术栈

- **后端**：Python 3, Flask
- **前端**：HTML, CSS, JavaScript, Leaflet.js, Bootstrap
- **核心库**：requests, threading, queue, sqlite3, Pillow, mercantile

## 安装步骤

1. **克隆仓库**
   ```bash
   git clone https://github.com/yourusername/TileHarvester.git
   cd TileHarvester
   ```

2. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

3. **启动应用**
   ```bash
   python app.py
   ```

4. **访问应用**
   打开浏览器访问 `http://127.0.0.1:5000`

## 使用说明

### 基本使用

1. **设置瓦片URL**：在右侧输入瓦片服务器URL，例如Bing地图URL
2. **设置输出目录**：输入瓦片保存的目录名称
3. **绘制下载区域**：点击"绘制区域"按钮，在地图上点击两个点绘制矩形区域
4. **设置缩放级别**：输入最小和最大缩放级别
5. **开始下载**：点击"开始下载"按钮
6. **控制下载**：可随时暂停、继续或取消下载

### 高级功能

#### 手动输入边界坐标
1. 在"设置边界坐标"区域输入精确的经纬度坐标
2. 点击"应用边界"按钮
3. 系统会自动在地图上绘制边界框

#### 使用TMS瓦片规范
- 勾选"Use TMS tiles convention"选项
- 系统会使用TMS坐标系统下载瓦片

## API文档

### 主要API端点

- `GET /`：返回主页面
- `POST /api/download`：启动下载任务
- `POST /api/pause-download`：暂停当前下载
- `POST /api/resume-download`：继续当前下载
- `POST /api/cancel-download`：取消当前下载
- `GET /api/progress`：获取实时下载进度（SSE）
- `GET /api/download-status`：获取当前下载状态
- `GET /api/config/list`：获取配置文件列表
- `GET /api/config/load/<config_name>`：加载指定配置文件
- `POST /api/config/save`：保存当前配置

## 项目结构

```
TileHarvester/
├── app.py                # Flask应用主文件
├── requirements.txt      # 项目依赖
├── src/
│   ├── __init__.py       # 包初始化文件
│   ├── downloader/       # 下载器模块
│   │   ├── __init__.py   # 下载器包初始化
│   │   ├── base.py       # 核心下载逻辑
│   │   ├── batch.py      # 批处理下载接口
│   │   ├── performance.py # 性能监控
│   │   ├── utils.py      # 工具函数
│   │   ├── worker.py     # 下载工作线程
│   │   ├── mbtiles_handler.py # MBTiles处理
│   │   ├── progress_handler.py # 进度管理
│   │   ├── signal_handler.py # 信号处理
│   │   ├── mbtiles.py    # MBTiles相关功能
│   │   ├── progress.py   # 进度文件管理
│   │   ├── connection_pool.py # 数据库连接池
│   │   ├── transaction.py # 事务管理
│   │   └── request.py    # HTTP请求处理
│   ├── providers/        # 瓦片提供商模块
│   │   ├── __init__.py   # 提供商包初始化
│   │   ├── base.py       # 基础提供商类
│   │   ├── osm.py        # OSM提供商
│   │   ├── bing.py       # Bing提供商
│   │   ├── custom.py     # 自定义提供商
│   │   └── manager.py    # 提供商管理器
│   ├── routes/           # Flask路由模块
│   │   ├── __init__.py   # 路由包初始化
│   │   └── main.py       # 主要路由
│   ├── utils/            # 工具模块
│   │   ├── __init__.py   # 工具包初始化
│   │   └── error_handler.py # 错误处理
│   ├── tile_math.py      # 瓦片坐标计算
│   ├── cli.py            # 命令行接口（预留）
│   ├── progress_generator.py  # 进度文件生成器
│   ├── config.py         # 配置管理
│   └── exceptions.py     # 自定义异常
├── static/               # 静态文件
│   └── js/               # JavaScript文件
│       ├── main.js       # 前端主文件
│       └── modules/      # 前端模块
│           ├── core.js   # 核心模块
│           ├── map.js    # 地图模块
│           ├── config.js # 配置模块
│           └── bing.js   # Bing地图模块
├── templates/
│   └── index.html        # Web界面模板

├── logs/                 # 日志文件目录
├── configs/              # 配置文件目录
└── README.md            # 项目说明文档
```

## 配置说明

### 环境变量

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| `FLASK_ENV` | Flask运行环境 | development |
| `FLASK_DEBUG` | 是否开启调试模式 | True |
| `HOST` | 服务器绑定地址 | 0.0.0.0 |
| `PORT` | 服务器端口 | 5000 |

### 应用配置

在 `app.py` 中可以修改以下配置：

- `MAX_THREADS`：最大下载线程数（默认32）
- `UPLOAD_FOLDER`：默认瓦片存储目录（默认"tiles"）
- `MAX_CONTENT_LENGTH`：最大请求内容长度（默认16MB）

## 工具使用

### 1. 进度文件生成器 (progress_generator.py)

进度文件生成器用于生成 `.custom_progress.json` 文件，以便在将 MBTiles 转换为目录结构后，或者在复制 MBTiles 文件到其他地方时，能够继续使用断点续传功能。

#### 使用方法

```bash
# 生成目录的进度文件
python src/progress_generator.py -p /path/to/tile/directory

# 生成 MBTiles 文件的进度文件
python src/progress_generator.py -p /path/to/file.mbtiles

# 自定义提供商名称
python src/progress_generator.py -p /path/to/tile/directory -n my_provider
```

#### 参数说明

- `-p, --path`：输入路径，可以是目录或 MBTiles 文件
- `-n, --name`：提供商名称，默认为 'custom'

## 最佳实践

1. **选择合适的线程数**：根据网络带宽和服务器限制调整线程数
2. **合理设置缩放级别**：避免一次性下载过多瓦片
3. **使用代理**：如果下载量大，建议使用代理避免被封禁
4. **定期备份**：定期备份下载的瓦片数据
5. **遵守使用条款**：确保遵守各地图提供商的使用条款

## 常见问题

### Q: 下载失败怎么办？
A: 检查瓦片URL是否正确，网络连接是否正常，或尝试减少线程数

### Q: 地图显示不出来？
A: 检查瓦片URL是否支持HTTPS，或尝试更换地图提供商

### Q: 下载速度慢？
A: 尝试增加线程数，或检查网络连接

### Q: 瓦片数量计算不准确？
A: 检查边界坐标是否正确，或尝试调整缩放级别

## 许可证

MIT License

## 贡献指南

欢迎提交Issue和Pull Request！

## 更新日志

### v1.3.0 (2026-04-18)
- **前端代码重构**：
  - 将内联JavaScript代码拆分为模块化的JS文件
  - 优化代码结构，提高可维护性
  - 增强代码可读性和注释
  - 实现ES6模块语法，减少全局变量
  - 优化进度显示，提供下载速度和预计剩余时间
- **地图功能改进**：
  - 修复地图缩放到0级时消失的问题，通过限制最小缩放级别为1
  - 修复地图缩小时显示重复北美区域的问题，通过实现正确的QuadKey生成算法
  - 优化地图初始化和加载过程
- **后端代码优化**：
  - 模块化重构，将大文件拆分为多个小模块
  - 实现数据库连接池，提高多线程环境下的性能
  - 优化事务管理，减少数据库锁定问题
  - 增强错误处理，提供更详细的错误信息
  - 实现配置管理系统，支持YAML配置文件
  - 优化下载速度，提高并发性能
- **配置系统改进**：
  - 支持环境变量覆盖配置
  - 支持配置热重载
  - 提供配置保存和加载功能
  - 优化配置文件管理

### v1.1.0 (2026-01-26)
- 新增 `progress_generator.py` 工具：用于生成进度文件，支持断点续传
- 统一日志目录：将所有日志文件存储到 `logs` 目录
- 优化Windows路径处理：改进命令行参数中的Windows路径解析
- 精简工具输出：优化进度生成器的输出格式，提高可读性

### v1.0.0 (2026-01-17)
- 初始版本发布
- 支持多线程下载
- 支持暂停/继续下载
- 支持自定义瓦片提供商
- 友好的Web界面

## 联系方式

如有问题或建议，请提交Issue或联系开发者。

---

**Enjoy TileHarvesting! 🎉**

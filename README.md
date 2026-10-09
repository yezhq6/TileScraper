# TileScraper

**TileScraper** 是一个地图瓦片下载工具，可批量下载地图瓦片并保存到本地（支持目录与 MBTiles 输出、断点续传与 Web 界面）。

## 功能特性

- 🗺️ **任意瓦片源**：填一个瓦片 URL 模板即可（内置 Bing QuadKey 示例）
- 🚀 **高性能下载**：多线程下载，线程数可固定也可自动选择
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

## 核心架构（v2.0 重构）

本次重构重点解决"大数据量下载"的稳定性与内存问题，并做了模块化拆分：

- **流式任务生产**：`add_tasks_for_bbox()` 不再一次性生成全部瓦片坐标，
  而是按需生成 + 有界队列背压，百万级任务的内存占用保持恒定。
- **单一 MBTiles 写线程**：下载线程只负责入队，由独立写线程批量落库，
  避免多线程写 SQLite 的锁竞争（同时修复了历史版本"瓦片未落库"的缺陷）。
- **可中断的任务调度**：暂停 / 停止通过"任务回队 + 事件通知"实现，
  不会误杀工作线程，续传与取消都更可靠。
- **可扩展的断点续传**：不再把全量"已处理瓦片"读进内存集合，而是让
  任务生成流与进度库按同一顺序（z→x→y）做**双指针归并 + keyset 分页**，
  内存只与"每页大小"有关，与历史规模无关（实测 100 万条记录峰值内存从
  约 148 MB 降到约 7 MB）。
- **下载控制器**：`src/downloader/controller.py` 收敛全部会话状态，
  Flask 路由层保持轻薄、易测试。

### 目录结构

完整目录树（含每个文件的职责）见下方「项目结构」一节，避免两处重复维护导致过期。

## 测试

项目自带基于标准库 `unittest` 的回归测试（使用本地假 HTTP 会话，无需联网）：

```bash
python -m unittest discover -s tests -v
```

覆盖范围：瓦片坐标计算、目录 / MBTiles 下载、断点续传跳过与产物校验、
原子写入、内容校验、限流自适应、信号收尾、`start()` 正常返回（不再死锁）、
取消、暂停不误杀线程，以及 Flask API 端到端。

假网络测不到的"真实 HTTP 行为"（并发、限流、代理、真实写盘）由真实本地
服务器脚本覆盖，需要能绑定本地端口：

```bash
python -u tools/stress/ts_integrity.py   # 数据正确性/内容校验/限流/崩溃恢复/锁（约 25 秒）
python -u tools/stress/ts_final.py       # 全套并发+万级批量（约 3 分钟）
python -u tools/stress/ts_adaptive.py    # 静态 vs 动态并发对比（可选，较慢）
```

## 安装步骤

1. **克隆仓库**
   ```bash
   git clone https://github.com/yezhq6/TileScraper.git
   cd TileScraper
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

1. **设置瓦片URL**：在右侧输入瓦片服务器URL（默认是 Bing 的 QuadKey 模板；
   任意 XYZ 源替换成 `http://host/{z}/{x}/{y}.png` 即可）
2. **设置输出目录**：输入瓦片保存的目录名称
3. **绘制下载区域**：点击"绘制区域"按钮，在地图上点击两个点绘制矩形区域
4. **设置缩放级别**：分别填写"最低层级"和"最高层级"
5. **开始下载**：点击"开始下载"按钮
6. **控制下载**：可随时暂停、继续或取消下载

> 暂停在**瓦片边界**生效：正在下载的瓦片会读完并落盘，然后停止取新任务。
> 这样既不会留下半截文件，也不会重复请求已在途的瓦片。

### 高级功能

#### 手动输入边界坐标
1. 在"设置边界坐标"区域输入精确的经纬度坐标
2. 点击"应用边界"按钮
3. 系统会自动在地图上绘制边界框

#### 使用TMS瓦片规范
- 勾选"Use TMS tiles convention"选项（或 API 传 `"tms": true`）
- 含义：**只影响向服务端请求哪一行**——`{y}` 会被翻转成 TMS 行号
  （`2^z-1-y`）；URL 里的 `{-y}` 无论开关都表示翻转后的行号（Leaflet 写法）
- 任务坐标与本地落盘**始终按 XYZ**（目录布局与开关无关）；
  MBTiles 的 `tile_row` 按规范**始终写 TMS 行号**
- 若服务端是 XYZ 规范（`{y}` 从顶部计数），不要勾选

> 修正记录：v2.2.0 之前该开关完全无效；v2.3.0 一度让落盘路径和 MBTiles 行
> 也跟着翻转，导致"请求的行"与"存下来的行"错位（输出上下镜像）；
> v2.3.2 起统一为"坐标恒 XYZ，只在 URL 上翻转"，并支持 `{-y}`。

## 安全说明

本项目定位是**本机/内网工具**，默认监听 `0.0.0.0:5000`。已经做的收敛：

| 项 | 状态 |
|---|---|
| `/api/config/save` 路径穿越 | 已修：配置名只允许纯文件名，拒绝路径分隔符/`..` |
| 自定义源覆盖内置 `bing` | 已修：内置名拒绝被覆盖（返回 400） |
| 请求级代理覆盖 | 已移除：代理只能改 `config.yaml` / 环境变量，API 传了会被忽略并告警 |
| CSRF（跨站静默暂停/取消/删配置） | 已修：状态变更接口要求**同源** + POST 要求 `Content-Type: application/json`（跨站表单发不出 JSON，返回 415/403）；`server.trusted_origins` 可放行反代域名 |
| 前端第三方库被 CDN 投毒 | 已修：Bootstrap/Leaflet/Leaflet.draw **本地内置**在 `static/vendor/`（离线可用，页面不再引用任何外部 URL） |
| 错误信息泄漏 | 已修：500 只回通用文案，异常堆栈（含路径/SQL 细节）只写服务端日志 |
| 参数校验 | 已修：缩放 0–24、经纬度范围、非有限数字、URL 模板占位符、子域名清洗，统一 400 |
| API 鉴权 | **可选**：`server.api_token`（默认空 = 不鉴权，见下） |

**仍未解决 / 需要你自己决定**：默认没有鉴权，任何能访问端口的人都能发起下载、
把数据写到任意可写路径、填满磁盘。因此：

- 只在本机用 → 把 `server.host` 改成 `127.0.0.1`（最省事、最安全）；
- 要在内网共享 → 设置下面这个 `server.api_token`，并放在反向代理/防火墙后面；
- **不要直接暴露到公网**。

### 访问令牌（可选，默认关闭）

默认不鉴权（`server.api_token: ""`），方便本机/单人使用。要在内网共享时建议打开：

```yaml
server:
  api_token: "换成一串足够长的随机字符串"     # 例如 openssl rand -hex 24
```

- 打开后所有 `/api/*` 都要求 `X-API-Token: <token>` 或
  `Authorization: Bearer <token>`；`/api/health` 例外（方便探活）。
- 页面首次调用接口收到 401 时会弹框让你输入一次，之后记在浏览器
  `localStorage` 里自动携带；令牌本身**不会**出现在页面 HTML 或 URL 里。
- 脚本/命令行：`curl -H "X-API-Token: <token>" ...`。
- 未设置令牌且监听非本机地址时，启动日志会打一条 WARNING 提醒。

**限制（务必了解）**：

- 没有 TLS 时令牌是明文传输，只适合可信内网；真正的安全边界仍是
  反向代理 + HTTPS（Basic Auth / mTLS）。
- 浏览器原生 `EventSource` 不能带自定义请求头，因此启用令牌后页面**自动改用
  轮询**拉进度（SSE 才有的速度/ETA 会退化为显示计数与百分比）。
- 没有登录失败次数限制，令牌请用足够长的随机串。
- 仍然**不要直接暴露到公网**。

## API文档

### 主要API端点

- `GET /`：返回主页面
- `GET /api/health`：健康检查（返回服务状态与当前是否在下载）
- `POST /api/download`：启动下载任务
- `POST /api/pause-download`：暂停当前下载
- `POST /api/resume-download`：继续当前下载
- `POST /api/cancel-download`：取消当前下载
- `GET /api/progress`：获取实时下载进度（SSE）
- `GET /api/download-status`：获取当前下载状态
- `GET /api/failed-tiles?limit=1000`：列出当前任务输出目录里的失败瓦片
- `GET /api/config/list`：获取配置文件列表
- `GET /api/config/load/<config_name>`：加载指定配置文件
- `POST /api/config/save`：保存当前配置
- `POST /api/config/delete/<config_name>`：删除指定配置文件

> `/api/download` 在输出路径已被另一个下载任务占用时返回 **409**；
> 请求体里的 `proxy` / `provider_name="bing"` 等字段会被忽略或拒绝（见下）。

## 项目结构

```
TileScraper/
├── app.py                # Flask 应用主文件（含优雅退出与启动告警）
├── requirements.txt      # 项目依赖
├── config.yaml           # 运行配置
├── src/
│   ├── __init__.py
│   ├── downloader/       # 下载器模块
│   │   ├── __init__.py
│   │   ├── base.py              # 核心下载逻辑（入队/去重/生命周期/统计）
│   │   ├── batch.py             # 批处理下载接口
│   │   ├── worker.py            # 工作线程（校验/原子写/暂停语义）
│   │   ├── controller.py        # Web 会话控制器（参数校验/进度广播）
│   │   ├── request.py           # HTTP 会话与代理策略
│   │   ├── progress_handler.py  # progress.db（断点续传）
│   │   ├── mbtiles_handler.py   # 单写线程 + 收尾安全 + 产物校验
│   │   ├── rate_limiter.py      # 动态并发闸门 + 全局冷却 + Retry-After
│   │   ├── output_lock.py       # 输出目录 advisory lock
│   │   ├── connection_pool.py   # SQLite 连接池（分库模式）
│   │   ├── performance.py       # 性能监控
│   │   ├── signal_handler.py    # 信号 → stop_event
│   │   └── utils.py             # 路径工具 + 线程数解析
│   ├── providers/        # 瓦片源（bing / custom + 管理器）
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── bing.py
│   │   ├── custom.py
│   │   └── manager.py
│   ├── routes/           # Flask 路由
│   │   ├── __init__.py
│   │   └── main.py              # 路由 + CSRF/令牌保护
│   ├── utils/            # 预留工具包（当前为空）
│   │   └── __init__.py
│   ├── tile_math.py      # 瓦片坐标计算（含 O(1) 计数）
│   ├── cli.py            # 命令行（list / single / bbox / retry）
│   ├── progress_generator.py  # 断点续传进度文件生成工具
│   ├── config.py         # 配置管理
│   └── exceptions.py     # 自定义异常
├── static/
│   ├── js/               # 前端模块
│   │   ├── main.js       # 入口 + API 令牌 fetch 拦截器
│   │   └── modules/
│   │       ├── core.js   # 核心模块（表单/SSE/轮询/进度）
│   │       ├── map.js    # 地图与绘制
│   │       ├── config.js # 配置存取
│   │       └── bing.js   # Bing 图层
│   └── vendor/           # 本地内置的 Bootstrap/Leaflet（见其 README）
├── templates/
│   └── index.html        # Web 界面模板
├── tools/
│   ├── stress/           # 真实服务器压力/回归脚本
│   └── clean_parts.py    # 清理残留 .part 临时文件
├── configs/              # 配置文件目录（/api/config/* 使用）
└── README.md
```

## 配置说明

### 环境变量

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| `FLASK_ENV` | Flask运行环境 | development |
| `FLASK_DEBUG` | 是否开启调试模式 | True |
| `HOST` | 服务器绑定地址 | 0.0.0.0 |
| `PORT` | 服务器端口 | 5000 |
| `TILESCRAPER_PROD` | 设为 `1`/`true` 时使用 waitress 生产服务器（未安装则回退开发服务器） | 空 |

> 生产部署建议：安装 `waitress`（已列入 `requirements.txt`）后设置
> `TILESCRAPER_PROD=1`，以多线程 WSGI 服务器运行，替代 Flask 自带开发服务器。
> 本项目为"单进程单下载任务"模型，不要用多 worker 进程部署（各进程会各自维护一份下载状态）。

### 应用配置

下载相关配置都在 `config.yaml` 的 `download` 段：

- `download.threads`：默认下载线程数（默认 16，可填数字或 `auto`）
- `download.max_threads_hard_limit`：线程数硬上限（默认 128）
- `download.timeout` / `download.max_retries`：单请求超时 / 每瓦片重试次数
- `download.proxy`：代理策略（默认 `""` 直连，见下）
- 数据正确性：`atomic_write`、`atomic_fsync`、`verify_artifacts`、
  `validate_image`、`reject_blank_tiles`
- 限流自适应：`adaptive_concurrency`、`rate_limit_min_threads`、
  `rate_limit_cooldown_max`
- MBTiles 收尾：`mbtiles_drain_timeout`、`mbtiles_stall_timeout`、`mbtiles_ack`
- 并发保护：`output_lock`、`output_lock_timeout`
- 临时文件与失败清单：`cleanup_stale_parts`、`stale_part_age_hours`、
  `write_failed_manifest`

其它段（v2.3.2 起都**真正生效**，此前有几段是硬编码/死配置）：

| 段 | 键 | 作用 |
|---|---|---|
| `database` | `journal_mode`、`cache_size`、`synchronous`、`busy_timeout`、`mmap_size` | MBTiles 写连接与进度库连接的 SQLite PRAGMA（非法值自动回退默认） |
| `logging` | `level`、`format`（loguru 语法）、`file`、`rotation` | 文件日志；`format` 写成 stdlib 的 `%(...)s` 会被忽略并告警 |
| `paths` | `config_dir`、`default_output_dir` | 配置目录；页面/接口未指定输出路径时的默认值 |
| `server` | `host`、`port`、`debug`、`api_token`、`trusted_origins` | 监听地址/端口/调试模式；可选访问令牌；反向代理放行域名（见「安全说明」） |

> 已移除的死配置：`memory.*`、`server.secret_key`、`download.batch_size`、
> `download.progress_save_interval`、`paths.progress_db_dir`、`logging.format`
> 的 stdlib 写法。代理只认 `config.yaml` / 环境变量（页面完全不展示，见下）。

### 数据正确性（v2.2.0 新增）

下载器现在把"进度库"当作**线索**而不是唯一权威，避免断点续传变成"断点丢数据"：

| 配置 | 默认 | 作用 |
|---|---|---|
| `atomic_write` | `true` | 先写同目录临时文件再 `os.replace`，进程被杀/磁盘满不会留下半截瓦片 |
| `atomic_fsync` | `false` | 替换前 `fsync`（更抗断电，代价是每瓦片一次同步写） |
| `verify_artifacts` | `true` | 跳过前确认产物存在：目录查文件、MBTiles 查 `tiles` 表；缺失则重新下载并计入 `redownloaded` |
| `validate_image` | `true` | 用文件头校验返回的确实是图片，挡住"HTTP 200 + 错误页" |
| `reject_blank_tiles` | `false` | 过滤纯色空白瓦片（需要 Pillow；默认关，避免误杀合法纯色瓦片） |

另外：`failed` 状态的瓦片不再被当作"已完成"，下次运行会自动重试；MBTiles 收尾
只有在写队列确认排空后才关连接，写失败会记入 `write_failures` 并告警，而不是
静默丢数据。

**崩溃恢复（进程被强杀 / 断电）**：

- 目录模式：目标文件只在数据完整后原子替换，磁盘上不会出现半截瓦片；
  重跑时已存在的文件由 `exists()` 检查直接跳过（不发 HTTP 请求）。
- MBTiles 模式：`tiles` 表就是权威产物记录。重跑时按表跳过已提交的行，
  只补缺失的；即使 `progress.db` 落后于实际写入（强杀时最后一批进度
  还没落盘），也不会把已落库的瓦片重新请求一遍。
- 两种模式的崩溃恢复都由 `tools/stress/ts_integrity.py` 的真实
  "SIGKILL 下载中的子进程 → 重跑" 场景覆盖（3000 瓦片：最终数量完整、
  同一 URL 只请求一次、请求数恰好等于缺失数）。

### 提交确认（v2.3.0 新增）

`download.mbtiles_ack: true`（默认）让 MBTiles 的"标记成功"只发生在写线程
**commit 成功之后**，因此 `progress.db` 永远不会领先于实际产物：

- 强杀后最坏情况是"少记了已提交的瓦片"（下次重下，最多一个写入批次）；
- 不可能出现"进度说成功、磁盘上没有"这种丢数据的情况；
- 即使显式关掉 `verify_artifacts` 也依然不丢（已实测：
  SIGKILL + `verify_artifacts=false` → 3000/3000 行齐全）。

### 并发与多进程保护（v2.3.0 新增）

| 配置 | 默认 | 作用 |
|---|---|---|
| `output_lock` | `true` | 输出路径加 advisory lock（POSIX `flock` / Windows `msvcrt`），同一路径只允许一个下载进程；Web 端冲突返回 409，CLI 打印提示；进程退出自动释放 |
| `output_lock_timeout` | `0` | 取锁等待秒数（0 = 立刻失败） |
| `cleanup_stale_parts` | `true` | 重新下载某个瓦片时清理它遗留的 `.<瓦片名>.<随机>.part` |
| `stale_part_age_hours` | `24` | 只有 mtime 超过该时长的临时文件才会被清理（避免误删并发实例的在途文件） |

批量清理历史遗留可用：

```bash
python tools/clean_parts.py /path/to/tiles --hours 24 --dry-run
```

### 失败瓦片（v2.3.0 新增）

失败瓦片不会被当作"已完成"，下次运行会自动重试；此外还提供：

- 收尾时写出清单 `<输出目录>/failed_tiles.txt`（MBTiles 为 `<同名>.failed.txt`），
  每行 `z x y`；下次全部成功则自动删除陈旧清单
  （`download.write_failed_manifest`，默认开）；
- CLI 只补失败：`python -m src.cli retry --provider bing --output-dir tiles_datasets`
  （内部用 keyset 分页读失败清单，内存有界）；
- API：`GET /api/failed-tiles?limit=1000` 返回当前任务的失败瓦片清单。

### 限流自适应（v2.2.0 新增）

收到 `429` / `503` 时会：

1. 解析 `Retry-After`（秒数或 HTTP 日期），设置一段**全局冷却**，所有线程一起等；
2. 按比例下调并发上限（不低于 `rate_limit_min_threads`，冷却时间不超过
   `rate_limit_cooldown_max`）；
3. 冷却结束且连续成功达到阈值后，逐步把并发恢复回设定值。

`adaptive_concurrency: false` 可以关闭这套逻辑，退回固定并发 + 普通退避。
请求头里的 `Retry-After` 缺失时使用 1 秒的基础冷却。

### 线程数怎么定

线程数不是越多越快：受带宽、瓦片服务器限速和 Python GIL 影响，超过某个点后
吞吐不再增长而 CPU 继续升高。实测（24 核，本地瓦片服务器，服务器独立进程）：

| 服务端延迟 | 8 线程 | 16 线程 | 32 线程 | 64 线程 | 256 线程 |
|-----------|-------:|--------:|--------:|--------:|---------:|
| 5 ms      | 1296/s | 1405/s  | 1303/s  | 1039/s  | 1525/s   |
| 50 ms     | 145/s  | 272/s   | 606/s   | 1188/s  | 1463/s   |

低延迟时 8~16 线程就基本打满，再加只浪费 CPU（客户端 CPU 从 99% 涨到 228%，
吞吐几乎不变）；高延迟时线程越多越接近上限。**默认给 16**，并支持：

- 页面/接口填具体数字——按你的网络实测调优；
- 填 `auto`——按 `clamp(CPU核数*4, threads_auto_min, threads_auto_max)` 自动选择
  （本机 24 核时为 32）；
- `download.max_threads_hard_limit`（默认 128）为硬上限，超过会被截断并打印告警。

> **为什么不内置"吞吐驱动的动态线程"**（2026-10-08 调查结论）：
> 实测客户端每瓦片约 0.76ms Python CPU，**单核上限 ≈1300 瓦片/s**（GIL 串行化
> 每响应的 Python 工作：头解析、对象构造、内容校验、落盘），所以低延迟下
> 16 线程左右就是天花板，加线程不会更快。业界主流下载器
> （aria2 `-j`、curl `--parallel-max`、rclone `--transfers`、Scrapy
> `CONCURRENT_REQUESTS`）也都是**固定可设上限**；唯一的内建自适应
> （aria2 `--optimize-concurrent-downloads`）是按观测带宽的开环映射且默认关闭。
> 实测"只看吞吐"的 AIMD 控制器在服务端限流收紧后会正反馈崩塌（比最差静态配置
> 慢 3.6 倍），而 RTT/延迟梯度类控制器对"尺寸均一的瓦片"是已知会退化的场景。
> 因此本项目保持**静态线程数 + 仅在服务器明确限流（429/503 + `Retry-After`）
> 时向下收缩并发**。复现脚本：`tools/stress/ts_adaptive.py`。

### 代理设置（只通过配置文件，不在页面上改）

瓦片请求默认**直连**，不会读取 `HTTP_PROXY` / `HTTPS_PROXY` 等环境变量
（历史版本想"禁用代理"但写法无效，导致请求被环境代理接管、间歇性失败）。
在 `config.yaml` 里按需配置：

```yaml
download:
  proxy: ""                          # 直连（默认，小白无需关心）
  # proxy: "env"                     # 沿用系统环境变量代理
  # proxy: "http://127.0.0.1:7890"   # 指定代理（可含 user:pass@）
```

也支持用环境变量覆盖（适合容器/服务化部署）：
`TILESCRAPER_DOWNLOAD_PROXY=env`。

> **为什么页面上不放代理输入框**：代理是"环境/部署"级别的设置，不是每次下载的
> 参数。放在页面上既增加小白的心智负担，也意味着**任何能访问端口的人都能让
> 服务端改用他指定的代理**（中间人、凭据泄漏、把服务端当跳板），而本服务默认
> 监听 `0.0.0.0` 且没有认证。现在 `/api/download` 会**忽略**请求里的 `proxy`
> 字段并打一条 WARNING；前端**完全不显示代理相关 UI**（可改但改了不生效的展示
> 反而误导人），当前生效模式改为**启动时打进日志**（例如
> `下载代理模式: 直连（忽略 HTTP_PROXY/HTTPS_PROXY）`），需要时改
> `config.yaml` 后重启即可。程序化调用仍可用 `TileDownloader(proxy=...)` 显式指定。

## 工具使用

### 1. 进度文件生成器 (progress_generator.py)

进度文件生成器用于在**复制/移动数据集之后**重建断点续传所需的 `progress.db`，
避免重新下载已有瓦片。

> v2.2.0 起，生成器写入的路径与下载器读取的路径完全一致：
> 目录模式写 `<目录>/progress.db`，MBTiles 模式写 `<同名>.progress.db`；
> 并且会建立下载器需要的 `(z, x, y)` 索引。旧版本写到 `<输入>/aux/...`，
> 下载器根本不会读取。

#### 使用方法

```bash
# 为目录生成/补齐进度库
python src/progress_generator.py -p /path/to/tile/directory

# 为 MBTiles 生成/补齐进度库
python src/progress_generator.py -p /path/to/file.mbtiles

# 自定义提供商名称（仅影响 JSON 旧格式的输出位置）
python src/progress_generator.py -p /path/to/tile/directory -n my_provider
```

#### 参数说明

- `-p, --path`：输入路径，可以是目录或 MBTiles 文件
- `-n, --name`：提供商名称，默认为 'custom'

## 最佳实践

1. **选择合适的线程数**：低延迟网络 8~16 通常已够，高延迟或跨国链路可加到 32~64
2. **合理设置缩放级别**：避免一次性下载过多瓦片
3. **使用代理**：需要走代理时显式配置 `download.proxy`（默认直连）
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

## 已知限制与后续计划

**已知限制**（安全相关的详见「安全说明」）：

- **单进程单任务**：下载控制器是全局单例，同一时刻只跑一个任务；同一输出路径由
  输出目录锁保护（冲突返回 409）。多任务并行需要按任务 id 管理多个 downloader。
- **默认不鉴权**：要内网共享请设置 `server.api_token`，或放在反向代理后面；
  不要直接暴露公网。
- **强杀后有界重下**：MBTiles 模式被强杀时，最后一批（≤ 未 flush 的进度批次 +
  未提交的写批次）会重下，但**不会丢数据**（进度只在 commit 成功后记录）。
- **线程数不是越大越快**：实测客户端约 0.76ms Python CPU/瓦片（GIL 串行），
  单核上限 ≈1300 瓦片/s；低延迟下 16 线程已到拐点，详见「线程数怎么定」。

**后续计划（尚未实现）**：

1. 多会话隔离：按任务 id 管理多个 downloader，允许不同输出路径并行。
2. 重试退避加 jitter（现在 `delay * 2**attempt` 没有抖动）。
3. 按域名维护独立的限流/冷却（目前是全局冷却）。
4. 失败瓦片自动化闭环：定时/多轮补失败，并把 `failed_tiles.txt` 接进界面。
5. 清理：删除 `progress_generator` 的历史 JSON 格式，收敛 `/api/providers`。

## 许可证

MIT License

## 贡献指南

欢迎提交Issue和Pull Request！

## 更新日志

### v2.3.5 (2026-10-09) — 页面彻底移除代理展示

- **前端不再显示任何代理 UI**：v2.3.2 起页面上的"网络代理"只是只读展示
  （改了也没用，真正的配置在 `config.yaml`），容易被误认为可以在这里设置。
  现在整块移除，模板不再渲染 `proxy_mode`，`/` 路由也不再传该变量。
- 代理的唯一入口仍是 `config.yaml` 的 `download.proxy`（`""`=直连默认、
  `env`=跟随环境变量、其它=代理地址）或环境变量
  `TILESCRAPER_DOWNLOAD_PROXY`；**改完需重启**。
- 为方便排查"到底走的哪种模式"，启动时打一条 INFO 日志（例如
  `下载代理模式: 直连（忽略 HTTP_PROXY/HTTPS_PROXY）`），复用
  `describe_proxy()`（仍自动隐藏 `user:pass`）。

### v2.3.4 (2026-10-08) — 可选访问令牌 + 收尾评审遗留项

- **可选 API 访问令牌**（`server.api_token`，默认空 = 不鉴权）：设置后所有
  `/api/*` 要求 `X-API-Token` 或 `Authorization: Bearer`（`/api/health` 例外）；
  页面 401 时弹框输入一次并记住（令牌不进 HTML/URL）；启用令牌时前端自动改用
  轮询（`EventSource` 无法带自定义头）。未设令牌却监听非本机地址会打 WARNING。
- **不再回显异常原文**：500 统一返回通用文案，堆栈只写服务端日志（此前可能把
  本地路径、SQLite 报错回给客户端）。
- **并发安全**：`ProviderManager` 的读取方法（`provider_exists` /
  `get_provider_info` / `get_all_providers_info`）补上锁，避免与注册并发时
  遍历到一半抛 "dictionary changed size"；`ConnectionPool.get_connection`
  改为持锁返回，并记录/校验每个 key 对应的库文件，避免复用错库或拿到正在关闭的连接。
- **修正 Bing z=0 的 quadkey**：应为 `"0"` 而不是空串（当前预览地图 minZoom=1，
  只有直接调用 API 才会遇到）。
- **删除死代码**：`src/utils/error_handler.py` 与 `TileMath.validate_bbox`
  （全仓无引用）。
- 新增 12 项测试：令牌 401/Bearer/页面不泄漏、异常不回显、连接池换库重建、
  Bing quadkey、ProviderManager 读方法。

### v2.3.3 (2026-10-08) — CSRF 防护 + 前端资源本地内置

- **CSRF 防护**：`/api/download`、`/api/pause-download`、`/api/resume-download`、
  `/api/cancel-download`、`/api/config/save`、`/api/config/delete` 现在要求
  **同源**（优先看浏览器的 `Sec-Fetch-Site`，退回比较 `Origin` 与请求 Host；
  两者都没有的脚本/curl 仍放行），且 POST/PUT/PATCH 必须带
  `Content-Type: application/json`（浏览器跨站表单只能发 urlencoded，
  因此被 415 挡下）。以前任意网页都能静默取消正在跑的下载或删除配置。
  `server.trusted_origins` 用于反向代理/端口转发场景放行域名。
- **前端第三方库全部本地内置**：Bootstrap 5.3.0 / Leaflet 1.9.4 /
  Leaflet.draw 1.0.4（含 CSS 引用的图标图片）放到 `static/vendor/`，
  页面不再引用任何 CDN。好处：① 内网/离线也能打开界面；② CDN 被投毒不再是
  "同源脚本可以直接调本站 API"。版本/许可证/sha384 见
  `static/vendor/README.md`；同时移除了**从未被使用**的
  `bootstrap.bundle.min.js`（页面只用 Bootstrap 的 CSS）。
- 新增回归测试：页面不得引用任何外部资源、模板引用的静态资源必须可访问、
  CSS 里的图标引用必须落到本地文件、CSRF 的 415/403/放行分支。

### v2.3.2 (2026-10-08) — 代理配置化 / 修复 TMS 错位 / 配置项真正生效

- **前端移除"代理"输入框**，改为只读展示当前生效模式（不显示账号密码）。
- **`/api/download` 不再接受 `proxy` 字段**：代理只从 `config.yaml` 的
  `download.proxy` 或环境变量 `TILESCRAPER_DOWNLOAD_PROXY` 读取；请求里带了
  `proxy` 会被忽略并打 WARNING。理由：代理是环境级配置，且无认证的 HTTP 端口
  不应能被用来改写服务端出口代理（中间人/凭据泄漏/跳板风险）。
- 默认仍是**直连**，小白零配置；专业人员改配置或用环境变量。
- 新增 `describe_proxy()`（日志/页面提示统一文案，自动隐藏 `user:pass`）。
- **修复 TMS 坐标错位（上下镜像）**：任务坐标恒为 XYZ，`is_tms` 只翻转 URL 里的
  `{y}`；目录落盘恒为 XYZ 布局，MBTiles `tile_row` 恒按规范写 TMS 行号；
  新增 `{-y}` 占位符支持。v2.3.0 曾让路径/行号也跟着翻转，导致"请求的行"
  与"存下来的行"不一致。
- **修复子域名导致的批量失败**：子域名（字符串或数组）统一 trim/去空/去重——
  以前输入 `t0, t1` 会拼出 `ecn.%20t1...`，约 75% 瓦片直接连接失败；
  URL 模板含 `{s}` 但子域名为空、或模板完全没有占位符时返回 400 并给出提示。
- **消除"死配置"**：`database.*`（SQLite PRAGMA）、`logging.format`/`rotation`、
  `paths.default_output_dir` 以前写了不生效，现已接入（非法 PRAGMA 值自动回退）；
  删除从未被读取的 `memory.*`、`server.secret_key`、`download.batch_size`、
  `download.progress_save_interval`、`paths.progress_db_dir`。

### v2.3.1 (2026-10-08) — 默认线程数 8 → 16

- 默认下载线程数改为 **16**（`download.threads`、页面输入框默认值同步）。
  依据：低延迟（5ms）下 8→16 线程吞吐 1296→1405/s，16 线程已在拐点；
  高延迟下 16 线程也比 8 线程快约一倍。实测见"线程数怎么定"。
- 完成"静态线程 vs 动态线程"调查（`tools/stress/ts_adaptive.py`）：
  **不引入吞吐驱动的动态线程**，维持静态线程数 + 服务器限流时向下收缩并发
  （原因见"线程数怎么定"一节）。

### v2.3.0 (2026-10-08) — 提交确认 / 多进程锁 / 失败补下

- **MBTiles 提交确认（ack）**：`download.mbtiles_ack`（默认开）把"标记成功"
  从 worker 移到写线程，只在 commit 成功后记录。进度库永不领先于产物：
  强杀最坏只是"少记已提交的瓦片"（下次重下），**即使关掉 `verify_artifacts`
  也不会丢数据**；写失败的批次改记 `failed`（旧行为会谎报成功）。
- **输出目录锁**：`download.output_lock`（默认开）用 advisory lock 保证
  同一输出路径只有一个下载进程；Web 冲突返回 **409**，进程退出/被强杀
  自动释放。顺带避免两个写线程抢同一个 `.mbtiles`。
- **残留临时文件清理**：重新下载某个瓦片时清理其超龄 `.part`
  （`cleanup_stale_parts` / `stale_part_age_hours`），并提供
  `tools/clean_parts.py` 批量清理。
- **失败瓦片可见可补**：收尾写 `failed_tiles.txt` 清单（无失败自动清理）、
  新增 `GET /api/failed-tiles`、新增 CLI `retry` 只补失败瓦片
  （keyset 分页读取，内存有界）。
- **入队去重**：同一坐标在"已入队未处理"期间只保留一份，去重集合由
  队列容量封顶（不会随下载总量增长）；`add_tasks` 内部及跨调用重复都会
  被拦下，统计上计入 skipped 以保证进度收敛到 100%。
- **收尾顺序**：输出锁最后释放，确保写线程与数据库连接都已收尾。

### v2.2.0 (2026-10-08) — 数据正确性 / 限流自适应 / 审计修复

- **数据正确性（P1）**：
  - 目录模式改为**原子写入**（临时文件 + `os.replace`），进程被杀/磁盘满不再
    留下半截瓦片（旧行为下断点续传会把它当成"已完成"永久跳过）；
  - 断点续传跳过前**校验产物确实存在**（目录查文件、MBTiles 查 `tiles` 表），
    删掉产物后能重新下载；被重新下载的数量记入 `redownloaded`；
  - `failed` 记录不再被当作"已完成"，下次运行自动重试；
  - 信号处理只置 `stop_event`，不再 `sys.exit()`，进度保存与 MBTiles 提交
    走 `start()` 的正常收尾；Web 侧在主线程补注册优雅退出。
- **抓取质量（P2）**：
  - 新增图片**文件头校验**（挡住 HTTP 200 + 错误页），可选空白瓦片过滤；
  - 新增**限流自适应**：解析 `Retry-After`、全局冷却、动态降低/恢复并发；
  - MBTiles 收尾安全：写队列排空确认、写失败计数告警、写线程停止改用哨兵；
- **稳定性与安全**：
  - MBTiles "database is locked" 改为**有界重试**（旧实现递归重试会
    `RecursionError` 并打死写线程）；
  - 写线程退出/停滞时入队方不再永久阻塞；`wait_for_completion` 不会挂死；
  - `/api/config/save` 修复**路径穿越**（`config_name` 不再能写出 `configs/`）；
  - provider 改为**每下载器浅拷贝**，`tile_format`/`is_tms` 不再污染全局；
    禁止用自定义源覆盖内置 `bing`；并发请求各用各的 provider 实例；
  - `add_tasks()` 的 `total` 现在包含被跳过的瓦片，进度不会再超过 100%；
  - Web 进度改为**最新快照广播**（多标签页不再互相抢事件、不再收到旧任务事件）；
  - API 参数校验补齐：缩放级别范围、经纬度范围、非有限数字统一返回 400；
- **其它**：
  - 删除死代码 `src/downloader/transaction.py`，接上从未被调用的
    `PerformanceMonitor`（`enable_performance_monitor=True` 时生效）；
  - `progress_generator.py` 写入下载器真正读取的路径并建立 `(z, x, y)` 索引；
  - 前端与内置 Bing 默认 URL 改为 `https://`，避免 HTTPS 部署的 mixed content；
  - 配置键收敛：移除语义重叠的 `download.max_threads` / `default_threads`；
  - 新增真实服务器回归脚本 `tools/stress/ts_integrity.py`（含 **SIGKILL 崩溃
    恢复**场景：下载中强杀子进程后重跑，验证不丢瓦片、不重复请求）。

### v2.1.0 (2026-10-08) — 压力测试后的修复
- **修复严重缺陷**：
  - 任务总量超过 `download.task_queue_size` 时，暂停 → 继续会**永久死锁**
    （队列被生产者打满，工作线程又在无超时地 `put` 回队，互相等待）。
    改为"暂停时当前线程原地持有任务、在瓦片边界生效"，并有回归测试覆盖；
  - `add_tasks()` 在 `start()` 之前预置超过队列容量的任务会永久阻塞调用方；
    现在超量时自动切换为流式生产，`add_task()` 单条超限会给出明确报错；
  - **代理设置失效**：`session.proxies = {'http': None}` 无法关闭环境变量代理，
    请求会被 `HTTP_PROXY` 悄悄接管（实测间歇性 502、吞吐降到 1/4）。
    现在代理由 `download.proxy` 显式控制，默认直连。
- **修复中等缺陷**：
  - `download.timeout` / `max_retries` 现在真正生效（此前 worker 硬编码 `timeout=5`）；
  - 去掉 HTTPAdapter 与业务层叠加的重试，避免一次失败被放大成成倍请求；
  - 会话重建时关闭旧连接，不再泄漏。
- **修复轻微缺陷**：
  - 暂停不再重复请求在途瓦片（此前会多请求约 N 个，N=线程数）；
  - 线程数超过硬上限会打印告警，不再静默截断；
  - 工作线程的进度库连接在收尾时统一关闭。
- **界面 / 默认值**：
  - 移除"地图源"下拉（与"瓦片URL"重复），只保留瓦片URL输入；
  - 移除内置 OSM 源，默认只保留 Bing；
  - 缩放级别输入框标注"最低层级 / 最高层级"；
  - 下载线程数默认 4 → 8，并支持填 `auto` 自动选择；
  - 新增"代理"输入框。

### v2.0.0 (2026-10-07) — 重构
- **修复严重缺陷**：
  - MBTiles 写入队列生产者/消费者不一致，导致下载的瓦片从未落库；
  - `start()` 因工作线程永不退出而永久阻塞（CLI / 批量下载会卡死）；
  - 暂停时误用 `return` 结束工作线程，导致并发线程数递减；
  - `progress_handler` 中重复定义的 `__init__`；
  - `add_tasks` 中使用 Python 3.12+ 才支持的嵌套引号 f-string。
- **性能 / 内存**：
  - 任务改为"按需生成 + 有界队列背压"，超大数据量下载内存占用恒定；
  - 断点续传改为"有序流式归并 + keyset 分页"，内存与历史规模解耦
    （100 万条历史：约 148 MB → 约 7 MB）；
  - 新增 `TileMath.count_tiles_in_bbox`，O(1) 计算瓦片数量；
  - Web 请求不再阻塞等待所有任务入队；进度事件队列有界，避免内存泄漏。
- **模块化 / 健壮性**：
  - 新增 `DownloadController`，路由层不再持有全局状态；
  - 统计计数加锁，线程安全；单任务异常不再拖垮工作线程；
  - 移除死代码模块（`progress.py`、`mbtiles.py`）与无用导入；
  - 新增 `tests/` 回归测试套件。
- **可用性**：
  - 新增 `/api/health` 健康检查、`/api/config/delete/<name>` 删除配置；
  - Web 端新增"地图源"下拉（选择内置源自动填充 URL/子域名/格式）与"删除配置"按钮；
  - 支持 waitress 生产服务器（`TILESCRAPER_PROD=1`），未安装时安全回退。

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

**Enjoy TileScraping! 🎉**

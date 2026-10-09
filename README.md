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
  单核上限 ≈1300 瓦片/s；低延迟下 16 线程已到拐点，往上只增加 CPU 不增加吞吐
  （实测数据与取舍写在 `config.yaml` 的 `download.threads` 注释里）。

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

## 联系方式

如有问题或建议，请提交Issue或联系开发者。

---

**Enjoy TileScraping! 🎉**

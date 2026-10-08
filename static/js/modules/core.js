/**
 * 核心模块
 */

import MapModule from './map.js';
import ConfigModule from './config.js';

// 添加Math.radians方法
if (!Math.radians) {
    Math.radians = function(degrees) {
        return degrees * Math.PI / 180;
    };
}

class CoreModule {
    constructor() {
        this.mapModule = new MapModule();
        this.configModule = new ConfigModule();
        this.eventSource = null;
        this.isDownloading = false;
        this.isCancelled = false;
        this.statusPollTimer = null;
    }

    /**
     * 解析响应 JSON（即使响应非 2xx 也尝试读取响应体）
     * @param {Response} response - fetch 响应对象
     * @returns {Promise<Object|null>} 解析后的对象，解析失败返回 null
     */
    async parseJsonResponse(response) {
        try {
            return await response.json();
        } catch (error) {
            return null;
        }
    }

    /**
     * 将经度规整到 [-180, 180]
     * @param {number} lon - 经度
     * @returns {number} 规整后的经度
     */
    wrapLongitude(lon) {
        return ((lon + 180) % 360 + 360) % 360 - 180;
    }

    /**
     * 初始化应用
     */
    init() {
        console.log('初始化TileScraper应用...');

        // 初始化地图
        this.mapModule.initMap();

        // 绑定事件
        this.bindEvents();

        // 加载配置列表
        this.loadConfigList();

        // 检查后端是否已有正在进行的下载任务（刷新页面/多标签页场景）
        this.restoreDownloadState();
    }

    /**
     * 恢复后端已有的下载状态，避免把正在运行的任务显示成空闲状态
     */
    async restoreDownloadState() {
        try {
            const response = await fetch('/api/download-status');
            const status = await this.parseJsonResponse(response);
            if (!status || !status.is_downloading) {
                return;
            }

            this.isDownloading = true;
            this.isCancelled = false;
            this.toggleDownloadButtons(status.is_paused ? 'paused' : 'downloading');

            const downloadProgress = document.getElementById('downloadProgress');
            if (downloadProgress) {
                downloadProgress.style.display = 'block';
            }
            if (status.stats) {
                this.updateProgressFromStatus(status);
            }

            this.showStatus('检测到正在进行的下载任务', 'info');
            // 刷新/多标签页恢复时，startDownload 里的按钮处理器没有被绑过，
            // 这里补一套精简版，避免按钮"看得见点不动"
            this.bindRestoredControlHandlers();
            this.startProgressListener();
        } catch (error) {
            console.error('查询下载状态失败:', error);
        }
    }

    /**
     * 给"恢复的下载状态"绑定暂停/继续/取消按钮（精简版：不含计时等本地状态）
     */
    bindRestoredControlHandlers() {
        const post = async (url) => {
            try {
                const response = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await this.parseJsonResponse(response);
                if (!response.ok || !data || data.success === false) {
                    this.showStatus(
                        (data && (data.error || data.message))
                        || `请求失败（HTTP ${response.status}）`,
                        'danger'
                    );
                    return null;
                }
                return data;
            } catch (error) {
                this.showStatus('请求失败: ' + error.message, 'danger');
                return null;
            }
        };

        const pauseBtn = document.getElementById('pauseBtn');
        const resumeBtn = document.getElementById('resumeBtn');
        const cancelBtn = document.getElementById('cancelBtn');
        const progressArea = document.getElementById('downloadProgress');

        if (pauseBtn) {
            pauseBtn.onclick = async () => {
                if (await post('/api/pause-download')) {
                    this.toggleDownloadButtons('paused');
                    this.showStatus('已暂停（本页是恢复出来的任务，计时信息不完整）', 'warning');
                }
            };
        }
        if (resumeBtn) {
            resumeBtn.onclick = async () => {
                if (await post('/api/resume-download')) {
                    this.toggleDownloadButtons('downloading');
                    this.showStatus('已继续下载', 'info');
                }
            };
        }
        if (cancelBtn) {
            cancelBtn.onclick = async () => {
                this.isCancelled = true;
                this.stopStatusPolling();
                const data = await post('/api/cancel-download');
                this.isDownloading = false;
                this.toggleDownloadButtons('initial');
                if (progressArea) {
                    progressArea.style.display = 'none';
                }
                this.showStatus(
                    data ? '下载已取消' : '取消请求失败',
                    data ? 'warning' : 'danger'
                );
            };
        }
    }

    /**
     * 启动状态轮询回退：SSE 断开时定期查询下载状态
     */
    startStatusPolling() {
        // 避免重复启动轮询
        if (this.statusPollTimer) {
            return;
        }

        const poll = async () => {
            try {
                const response = await fetch('/api/download-status');
                const status = await this.parseJsonResponse(response);
                if (!status) {
                    return;
                }

                if (!status.is_downloading) {
                    // 任务已结束（或被取消）
                    this.isDownloading = false;
                    this.stopStatusPolling();
                    this.toggleDownloadButtons('initial');
                    return;
                }

                this.isDownloading = true;
                this.toggleDownloadButtons(status.is_paused ? 'paused' : 'downloading');

                const downloadProgress = document.getElementById('downloadProgress');
                if (downloadProgress) {
                    downloadProgress.style.display = 'block';
                }
                this.updateProgressFromStatus(status);

                // SSE 已恢复则停止轮询
                if (this.eventSource && this.eventSource.readyState === EventSource.OPEN) {
                    this.stopStatusPolling();
                }
            } catch (error) {
                console.error('查询下载状态失败:', error);
            }
        };

        this.statusPollTimer = setInterval(poll, 2000);
        poll();
    }

    /**
     * 停止状态轮询回退
     */
    stopStatusPolling() {
        if (this.statusPollTimer) {
            clearInterval(this.statusPollTimer);
            this.statusPollTimer = null;
        }
    }

    /**
     * 用 /api/download-status 的结果刷新进度显示
     * @param {Object} status - 下载状态对象
     */
    updateProgressFromStatus(status) {
        const stats = status.stats || {};
        const downloaded = stats.downloaded || 0;
        const total = stats.total || 0;
        const percentage = total > 0 ? Math.floor(downloaded / total * 100) : 0;

        const progressBar = document.querySelector('.progress-bar');
        if (progressBar) {
            progressBar.style.width = `${percentage}%`;
            progressBar.setAttribute('aria-valuenow', percentage);
        }

        const progressText = document.getElementById('progressText');
        if (progressText) {
            progressText.textContent = `${percentage}%`;
        }

        const downloadedCountText = document.getElementById('downloadedCountText');
        if (downloadedCountText) {
            downloadedCountText.textContent = downloaded;
        }

        const totalCountText = document.getElementById('totalCountText');
        if (totalCountText) {
            totalCountText.textContent = total;
        }
    }

    /**
     * 绑定事件
     */
    bindEvents() {
        // 绑定地图事件
        this.mapModule.bindMapEvents(
            (bbox) => this.updateBboxInputs(bbox),
            (bbox) => this.updateBboxInputs(bbox),
            () => this.clearBboxInputs()
        );

        // 绘制区域按钮
        document.getElementById('drawBboxBtn').addEventListener('click', () => {
            this.mapModule.activateRectangleDraw();
        });

        // 清除区域按钮
        document.getElementById('clearBboxBtn').addEventListener('click', () => {
            this.mapModule.clearDrawings();
            this.clearBboxInputs();
        });

        // 适配视图按钮
        document.getElementById('fitBboxBtn').addEventListener('click', () => {
            const bbox = this.mapModule.getCurrentBbox();
            if (bbox) {
                this.mapModule.fitBounds(bbox);
            } else {
                this.showStatus('请先绘制区域', 'warning');
            }
        });

        // 应用边界按钮
        document.getElementById('applyManualBboxBtn').addEventListener('click', () => {
            this.applyManualBbox();
        });

        // 统计瓦片数量按钮
        document.getElementById('calculateTilesBtn').addEventListener('click', () => {
            this.calculateTilesCount();
        });

        // 下载表单提交
        document.getElementById('downloadForm').addEventListener('submit', (e) => {
            this.handleDownloadSubmit(e);
        });

        // 保存配置按钮
        document.getElementById('saveConfigBtn').addEventListener('click', () => {
            this.saveConfig();
        });

        // 加载配置选择框
        document.getElementById('loadConfigSelect').addEventListener('change', (e) => {
            const configName = e.target.value;
            if (configName) {
                this.loadConfig(configName);
            }
        });

        // 删除配置按钮
        const deleteConfigBtn = document.getElementById('deleteConfigBtn');
        if (deleteConfigBtn) {
            deleteConfigBtn.addEventListener('click', () => {
                this.deleteConfig();
            });
        }
    }

    /**
     * 更新边界输入框
     * @param {Object} bbox - 边界对象
     */
    updateBboxInputs(bbox) {
        document.getElementById('manualNorth').value = bbox.north.toFixed(6);
        document.getElementById('manualSouth').value = bbox.south.toFixed(6);
        document.getElementById('manualWest').value = this.wrapLongitude(bbox.west).toFixed(6);
        document.getElementById('manualEast').value = this.wrapLongitude(bbox.east).toFixed(6);
    }

    /**
     * 清除边界输入框
     */
    clearBboxInputs() {
        document.getElementById('manualNorth').value = '';
        document.getElementById('manualSouth').value = '';
        document.getElementById('manualWest').value = '';
        document.getElementById('manualEast').value = '';
    }

    /**
     * 应用手动边界
     */
    applyManualBbox() {
        const north = parseFloat(document.getElementById('manualNorth').value);
        const south = parseFloat(document.getElementById('manualSouth').value);
        const west = parseFloat(document.getElementById('manualWest').value);
        const east = parseFloat(document.getElementById('manualEast').value);

        if (isNaN(north) || isNaN(south) || isNaN(west) || isNaN(east)) {
            this.showStatus('请输入有效的边界坐标', 'danger');
            return;
        }

        // 验证坐标范围（与后端一致，避免 400）
        if (Math.abs(north) > 85.0511 || Math.abs(south) > 85.0511) {
            this.showStatus('纬度必须在 ±85.0511° 范围内', 'danger');
            return;
        }

        if (Math.abs(west) > 180 || Math.abs(east) > 180) {
            this.showStatus('经度必须在 ±180° 范围内', 'danger');
            return;
        }

        // 验证边界
        if (north < south) {
            this.showStatus('北界必须大于南界', 'danger');
            return;
        }

        if (west > east) {
            this.showStatus('西界必须小于东界', 'danger');
            return;
        }

        // 更新当前边界
        const bbox = { north, south, west, east };
        this.mapModule.setCurrentBbox(bbox);
        this.mapModule.addRectangle(bbox);

        this.showStatus('边界已应用', 'success');
    }

    /**
     * 加载配置列表
     */
    async loadConfigList() {
        try {
            const configList = await this.configModule.loadConfigList();
            const selectElement = document.getElementById('loadConfigSelect');
            
            if (selectElement) {
                selectElement.innerHTML = '<option value="">加载配置...</option>';
                configList.forEach(configName => {
                    const option = document.createElement('option');
                    option.value = configName;
                    option.textContent = configName;
                    selectElement.appendChild(option);
                });
            }
        } catch (error) {
            console.error('加载配置列表失败:', error);
        }
    }

    /**
     * 保存配置
     */
    async saveConfig() {
        const configName = document.getElementById('configName').value;
        if (!configName) {
            this.showStatus('请输入配置名称', 'danger');
            return;
        }

        const formData = this.configModule.getFormData();
        const success = await this.configModule.saveConfig(configName, formData);

        if (success) {
            this.showStatus('配置保存成功', 'success');
            // 重新加载配置列表
            this.loadConfigList();
        } else {
            this.showStatus('保存失败', 'danger');
        }
    }

    /**
     * 加载配置
     * @param {string} configName - 配置名称
     */
    async loadConfig(configName) {
        const config = await this.configModule.loadConfig(configName);
        if (config) {
            this.configModule.fillForm(config);
            this.showStatus('配置加载成功', 'success');
        } else {
            this.showStatus('加载失败', 'danger');
        }
    }

    /**
     * 删除当前选中的配置
     */
    async deleteConfig() {
        const select = document.getElementById('loadConfigSelect');
        const configName = select ? select.value : '';
        if (!configName) {
            this.showStatus('请先选择要删除的配置', 'warning');
            return;
        }
        if (!window.confirm(`确定删除配置「${configName}」吗？`)) {
            return;
        }
        const success = await this.configModule.deleteConfig(configName);
        if (success) {
            this.showStatus('配置已删除', 'success');
            this.loadConfigList();
        } else {
            this.showStatus('删除失败', 'danger');
        }
    }

    /**
     * 显示状态消息
     * @param {string} message - 消息内容
     * @param {string} type - 消息类型 (success, danger, warning, info)
     */
    showStatus(message, type = 'info') {
        const statusMessage = document.getElementById('statusMessage');
        statusMessage.className = `status-message alert alert-${type}`;
        statusMessage.textContent = message;
        statusMessage.style.display = 'block';

        // 5秒后自动隐藏
        if (window.statusTimeout) {
            clearTimeout(window.statusTimeout);
        }
        window.statusTimeout = setTimeout(() => {
            statusMessage.style.display = 'none';
        }, 5000);
    }

    /**
     * 切换下载按钮状态
     * @param {string} state - 状态: initial, downloading, paused
     */
    toggleDownloadButtons(state) {
        const downloadBtn = document.getElementById('downloadBtn');
        const pauseBtn = document.getElementById('pauseBtn');
        const resumeBtn = document.getElementById('resumeBtn');
        const cancelBtn = document.getElementById('cancelBtn');

        switch (state) {
            case 'initial':
                downloadBtn.style.display = 'block';
                pauseBtn.style.display = 'none';
                resumeBtn.style.display = 'none';
                cancelBtn.style.display = 'none';
                break;
            case 'downloading':
                downloadBtn.style.display = 'none';
                pauseBtn.style.display = 'block';
                resumeBtn.style.display = 'none';
                cancelBtn.style.display = 'block';
                break;
            case 'paused':
                downloadBtn.style.display = 'none';
                pauseBtn.style.display = 'none';
                resumeBtn.style.display = 'block';
                cancelBtn.style.display = 'block';
                break;
        }
    }

    /**
     * 计算瓦片数量
     */
    calculateTilesCount() {
        const north = parseFloat(document.getElementById('manualNorth').value);
        const south = parseFloat(document.getElementById('manualSouth').value);
        const west = parseFloat(document.getElementById('manualWest').value);
        const east = parseFloat(document.getElementById('manualEast').value);
        const minZoom = parseInt(document.getElementById('minZoom').value);
        const maxZoom = parseInt(document.getElementById('maxZoom').value);

        if (isNaN(north) || isNaN(south) || isNaN(west) || isNaN(east) || isNaN(minZoom) || isNaN(maxZoom)) {
            this.showStatus('请输入有效的边界和缩放级别', 'danger');
            return;
        }

        // 验证边界
        if (north < south) {
            this.showStatus('北界必须大于南界', 'danger');
            return;
        }

        if (west > east) {
            this.showStatus('西界必须小于东界', 'danger');
            return;
        }

        if (minZoom < 0 || maxZoom < 0) {
            this.showStatus('缩放级别必须为非负数', 'danger');
            return;
        }

        if (minZoom > maxZoom) {
            this.showStatus('最小缩放级别必须小于或等于最大缩放级别', 'danger');
            return;
        }

        // 计算瓦片数量
        const totalTiles = this.estimateTilesCount(west, south, east, north, minZoom, maxZoom);

        document.getElementById('tileCountResult').textContent = `总计：${totalTiles}`;
        this.showStatus(`瓦片数量计算完成: ${totalTiles}`, 'success');
    }

    /**
     * 估算边界框与缩放级别范围内的瓦片总数
     * @param {number} west - 西边界经度
     * @param {number} south - 南边界纬度
     * @param {number} east - 东边界经度
     * @param {number} north - 北边界纬度
     * @param {number} minZoom - 最小缩放级别
     * @param {number} maxZoom - 最大缩放级别
     * @returns {number} 瓦片总数
     */
    estimateTilesCount(west, south, east, north, minZoom, maxZoom) {
        let totalTiles = 0;
        for (let zoom = minZoom; zoom <= maxZoom; zoom++) {
            totalTiles += this.calculateTilesInBbox(west, south, east, north, zoom);
        }
        return totalTiles;
    }

    /**
     * 计算边界框内的瓦片数量
     * @param {number} west - 西边界经度
     * @param {number} south - 南边界纬度
     * @param {number} east - 东边界经度
     * @param {number} north - 北边界纬度
     * @param {number} zoom - 缩放级别
     * @returns {number} 瓦片数量
     */
    calculateTilesInBbox(west, south, east, north, zoom) {
        // 限制纬度避免溢出
        north = Math.max(Math.min(north, 85.0511), -85.0511);
        south = Math.max(Math.min(south, 85.0511), -85.0511);

        const n = Math.pow(2, zoom);
        const maxValidTile = n - 1;

        // 计算边界瓦片坐标
        // 左上角使用向下取整
        const minX = Math.floor((west + 180.0) / 360.0 * n);
        const minY = Math.floor((1.0 - Math.log(Math.tan(Math.radians(north)) + 1.0 / Math.cos(Math.radians(north))) / Math.PI) / 2.0 * n);
        // 右下角使用向上取整
        const maxX = Math.ceil((east + 180.0) / 360.0 * n - 1e-10);
        const maxY = Math.ceil((1.0 - Math.log(Math.tan(Math.radians(south)) + 1.0 / Math.cos(Math.radians(south))) / Math.PI) / 2.0 * n - 1e-10);

        // 纠正顺序，保证 min <= max
        const correctedMinX = Math.min(minX, maxX);
        const correctedMaxX = Math.max(minX, maxX);
        const correctedMinY = Math.min(minY, maxY);
        const correctedMaxY = Math.max(minY, maxY);

        // 确保瓦片坐标在有效范围内
        const validMinX = Math.max(0, correctedMinX);
        const validMaxX = Math.min(maxValidTile, correctedMaxX);
        const validMinY = Math.max(0, correctedMinY);
        const validMaxY = Math.min(maxValidTile, correctedMaxY);

        // 计算瓦片数量
        if (validMinX > validMaxX || validMinY > validMaxY) {
            return 0;
        }

        const tilesX = validMaxX - validMinX + 1;
        const tilesY = validMaxY - validMinY + 1;
        return tilesX * tilesY;
    }

    /**
     * 处理下载提交
     * @param {Event} e - 事件对象
     */
    handleDownloadSubmit(e) {
        e.preventDefault();

        const north = parseFloat(document.getElementById('manualNorth').value);
        const south = parseFloat(document.getElementById('manualSouth').value);
        const west = parseFloat(document.getElementById('manualWest').value);
        const east = parseFloat(document.getElementById('manualEast').value);
        const minZoom = parseInt(document.getElementById('minZoom').value);
        const maxZoom = parseInt(document.getElementById('maxZoom').value);
        const providerUrl = document.getElementById('providerUrl').value;
        const outputPath = document.getElementById('outputPath').value;
        const saveFormat = document.getElementById('saveFormat').value;
        const subdomains = document.getElementById('subdomains').value;
        const tileFormat = document.getElementById('tileFormat').value;
        const tms = document.getElementById('tms').checked;

        // 线程数：空 / auto 表示自动；否则必须是正整数
        let threadsInput = (document.getElementById('threads').value || '').trim().toLowerCase();
        let threads;
        if (threadsInput === '' || threadsInput === 'auto') {
            threads = 'auto';
        } else if (/^\d+$/.test(threadsInput) && parseInt(threadsInput, 10) > 0) {
            threads = parseInt(threadsInput, 10);
        } else {
            this.showStatus('下载线程数请填写正整数或 auto', 'danger');
            return;
        }

        if (isNaN(north) || isNaN(south) || isNaN(west) || isNaN(east)) {
            this.showStatus('请输入有效的边界坐标', 'danger');
            return;
        }

        if (isNaN(minZoom) || isNaN(maxZoom)) {
            this.showStatus('请输入有效的缩放级别', 'danger');
            return;
        }

        if (!providerUrl) {
            this.showStatus('请输入瓦片服务器URL', 'danger');
            return;
        }

        if (!outputPath) {
            this.showStatus('请输入输出路径', 'danger');
            return;
        }

        // 验证边界
        if (north < south) {
            this.showStatus('北界必须大于南界', 'danger');
            return;
        }

        if (west > east) {
            this.showStatus('西界必须小于东界', 'danger');
            return;
        }

        if (minZoom < 0 || maxZoom < 0) {
            this.showStatus('缩放级别必须为非负数', 'danger');
            return;
        }

        if (minZoom > maxZoom) {
            this.showStatus('最小缩放级别必须小于或等于最大缩放级别', 'danger');
            return;
        }

        // 预估瓦片数量，数量过大时要求用户确认
        const estimatedTiles = this.estimateTilesCount(west, south, east, north, minZoom, maxZoom);
        if (estimatedTiles > 100000) {
            const confirmed = window.confirm(
                `预计需要下载 ${estimatedTiles} 个瓦片，任务可能耗时非常长并占用大量磁盘空间。是否继续？`
            );
            if (!confirmed) {
                return;
            }
        }

        // 准备下载参数
        const params = {
            provider_url: providerUrl,
            north: north,
            south: south,
            west: west,
            east: east,
            min_zoom: minZoom,
            max_zoom: maxZoom,
            output_dir: outputPath,
            threads: threads,
            tms: tms,
            subdomains: subdomains ? subdomains.split(',') : [],
            tile_format: tileFormat,
            save_format: saveFormat
        };

        // 开始下载
        this.startDownload(params);
    }

    /**
     * 开始下载
     * @param {Object} params - 下载参数
     */
    startDownload(params) {
        this.isDownloading = true;
        this.isCancelled = false;
        this.stopStatusPolling();
        this.toggleDownloadButtons('downloading');
        this.showStatus('下载任务已开始', 'success');

        // 显示下载进度区域
        const downloadProgress = document.getElementById('downloadProgress');
        if (downloadProgress) {
            downloadProgress.style.display = 'block';
        }

        // 进度相关变量
        let startTime = Date.now();

        // 暂停按钮事件处理
        const pauseBtn = document.getElementById('pauseBtn');
        pauseBtn.onclick = () => {
            // 立即更新按钮状态，给用户反馈
            this.toggleDownloadButtons('paused');
            
            // 发送暂停请求
            fetch('/api/pause-download', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            })
            .then(async (response) => {
                const data = await this.parseJsonResponse(response);
                if (!response.ok) {
                    throw new Error(data?.error || data?.message || `HTTP错误！状态码: ${response.status}`);
                }
                return data;
            })
            .then(result => {
                if (result && result.success) {
                    this.showStatus('下载已暂停', 'warning');
                } else {
                    // 如果暂停失败，恢复按钮状态
                    this.toggleDownloadButtons('downloading');
                    this.showStatus('暂停失败: ' + ((result && result.message) || '未知错误'), 'danger');
                }
            })
            .catch(error => {
                // 如果请求失败，恢复按钮状态
                this.toggleDownloadButtons('downloading');
                console.error('暂停下载失败:', error);
                this.showStatus('暂停下载失败: ' + error.message, 'danger');
            });
        };

        // 继续按钮事件处理
        const resumeBtn = document.getElementById('resumeBtn');
        resumeBtn.onclick = () => {
            // 立即更新按钮状态，给用户反馈
            this.toggleDownloadButtons('downloading');
            
            // 发送继续请求
            fetch('/api/resume-download', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            })
            .then(async (response) => {
                const data = await this.parseJsonResponse(response);
                if (!response.ok) {
                    throw new Error(data?.error || data?.message || `HTTP错误！状态码: ${response.status}`);
                }
                return data;
            })
            .then(result => {
                if (result && result.success) {
                    this.showStatus('下载已恢复', 'info');
                } else {
                    // 如果继续失败，恢复按钮状态
                    this.toggleDownloadButtons('paused');
                    this.showStatus('继续失败: ' + ((result && result.message) || '未知错误'), 'danger');
                }
            })
            .catch(error => {
                // 如果请求失败，恢复按钮状态
                this.toggleDownloadButtons('paused');
                console.error('继续下载失败:', error);
                this.showStatus('继续下载失败: ' + error.message, 'danger');
            });
        };

        // 取消按钮事件处理
        const cancelBtn = document.getElementById('cancelBtn');
        cancelBtn.onclick = () => {
            // 设置取消标志
            this.isCancelled = true;
            this.isDownloading = false;

            // 停止状态轮询回退
            this.stopStatusPolling();

            // 立即更新按钮状态，给用户反馈
            this.toggleDownloadButtons('initial');

            // 隐藏下载进度区域
            const downloadProgress = document.getElementById('downloadProgress');
            if (downloadProgress) {
                downloadProgress.style.display = 'none';
            }

            // 发送取消请求
            fetch('/api/cancel-download', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            })
            .then(async (response) => {
                const data = await this.parseJsonResponse(response);
                if (!response.ok) {
                    throw new Error(data?.error || data?.message || `HTTP错误！状态码: ${response.status}`);
                }
                return data;
            })
            .then(result => {
                if (result && result.success) {
                    // 计算总下载时间
                    const endTime = Date.now();
                    const totalTime = endTime - startTime;
                    
                    // 格式化总下载时间
                    let timeText;
                    if (totalTime < 1000) {
                        timeText = `${totalTime} 毫秒`;
                    } else if (totalTime < 60000) {
                        timeText = `${(totalTime / 1000).toFixed(1)} 秒`;
                    } else {
                        const minutes = Math.floor(totalTime / 60000);
                        const seconds = ((totalTime % 60000) / 1000).toFixed(1);
                        timeText = `${minutes} 分 ${seconds} 秒`;
                    }
                    
                    if (result.stats) {
                        const statusMessage = document.getElementById('statusMessage');
                        statusMessage.className = 'status-message alert alert-warning';
                        statusMessage.innerHTML = `
                            <h6>下载已取消！</h6>
                            <p>已下载：${result.stats.downloaded}</p>
                            <p>失败：${result.stats.failed}</p>
                            <p>跳过：${result.stats.skipped}</p>
                            <p>总计：${result.stats.total}</p>
                            <p>剩余：${result.stats.remaining}</p>
                            <p>用时：${timeText}</p>
                        `;
                        statusMessage.style.display = 'block';
                        // 清除可能存在的自动隐藏计时器
                        if (window.statusTimeout) {
                            clearTimeout(window.statusTimeout);
                        }
                    } else {
                        this.showStatus('下载已取消', 'warning');
                    }
                } else if (!result || result.message !== '没有正在进行的下载任务') {
                    // 只显示非"没有正在进行的下载任务"的错误信息
                    this.showStatus('取消失败: ' + ((result && result.message) || '未知错误'), 'danger');
                }
                
                // 关闭SSE连接
                if (this.eventSource) {
                    this.eventSource.close();
                    this.eventSource = null;
                }
            })
            .catch(error => {
                console.error('取消下载失败:', error);
                this.showStatus('取消下载失败: ' + error.message, 'danger');
                
                // 关闭SSE连接
                if (this.eventSource) {
                    this.eventSource.close();
                    this.eventSource = null;
                }
            });
        };

        // 发送下载请求
        fetch('/api/download', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(params)
        })
        .then(async (response) => {
            const data = await this.parseJsonResponse(response);
            if (!response.ok) {
                throw new Error(data?.error || data?.message || `HTTP错误！状态码: ${response.status}`);
            }
            return data;
        })
        .then(result => {
            if (!result || !result.success) {
                // 如果初始请求失败，关闭SSE连接
                if (this.eventSource) {
                    this.eventSource.close();
                    this.eventSource = null;
                }
                const statusMessage = document.getElementById('statusMessage');
                statusMessage.className = 'status-message alert alert-danger';
                statusMessage.textContent = `下载失败：${(result && result.error) || '未知错误'}`;
                statusMessage.style.display = 'block';
                
                // 恢复按钮状态
                this.toggleDownloadButtons('initial');
                // 隐藏下载进度区域
                const downloadProgress = document.getElementById('downloadProgress');
                if (downloadProgress) {
                    downloadProgress.style.display = 'none';
                }
            } else {
                // 开始监听进度
                this.startProgressListener();
            }
        })
        .catch(error => {
            // 关闭SSE连接
            if (this.eventSource) {
                this.eventSource.close();
                this.eventSource = null;
            }
            
            const statusMessage = document.getElementById('statusMessage');
            statusMessage.className = 'status-message alert alert-danger';
            statusMessage.textContent = `请求失败：${error.message}`;
            statusMessage.style.display = 'block';
            console.error('下载请求错误:', error);
            
            // 恢复按钮状态
            this.toggleDownloadButtons('initial');
            // 隐藏下载进度区域
            const downloadProgress = document.getElementById('downloadProgress');
            if (downloadProgress) {
                downloadProgress.style.display = 'none';
            }
        });
    }

    /**
     * 开始监听进度
     */
    startProgressListener() {
        // 关闭之前的事件源
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }

        // EventSource 无法自定义请求头；启用访问令牌时改用轮询回退，
        // 避免把 token 放进 URL（那样会出现在访问日志里）
        if (window.__API_AUTH_REQUIRED__) {
            this.startStatusPolling();
            return;
        }

        // 进度相关变量
        let startTime = Date.now();
        let lastDownloaded = 0;
        let lastTotalBytes = 0;
        let lastTime = Date.now();
        let lastEtaTime = null; // 上一次的剩余时间
        let speedHistory = [];
        const MAX_SPEED_HISTORY = 100; // 历史记录长度
        const MIN_TIME_DIFF = 1000; // 最小时间差（毫秒）

        // 创建新的事件源
        this.eventSource = new EventSource('/api/progress');

        // 连接成功：停止轮询回退
        this.eventSource.onopen = () => {
            this.stopStatusPolling();
        };

        // 监听消息事件
        this.eventSource.onmessage = (event) => {
            try {
                // 收到新的进度事件，说明 SSE 已恢复
                this.stopStatusPolling();

                const progress = JSON.parse(event.data);
                const statusMessage = document.getElementById('statusMessage');
                
                // 检查数据有效性
                if (typeof progress.downloaded !== 'number' || typeof progress.total !== 'number') {
                    console.error('无效的进度数据:', progress);
                    return;
                }
                
                if (progress.completed) {
                    // 如果已经取消下载，忽略完成事件
                    if (this.isCancelled) {
                        if (this.eventSource) {
                            this.eventSource.close();
                            this.eventSource = null;
                        }
                        return;
                    }
                    
                    // 检查是否真的完成
                    if (progress.total === 0) {
                        console.warn('收到完成事件，但总任务数为0，忽略');
                        return;
                    }
                    
                    // 计算总下载时间
                    const endTime = Date.now();
                    const totalTime = endTime - startTime;
                    
                    // 格式化总下载时间
                    let timeText;
                    if (totalTime < 1000) {
                        timeText = `${totalTime} 毫秒`;
                    } else if (totalTime < 60000) {
                        timeText = `${(totalTime / 1000).toFixed(1)} 秒`;
                    } else {
                        const minutes = Math.floor(totalTime / 60000);
                        const seconds = ((totalTime % 60000) / 1000).toFixed(1);
                        timeText = `${minutes} 分 ${seconds} 秒`;
                    }
                    
                    // 显示完成信息
                    if (progress.stats) {
                        statusMessage.className = 'status-message alert alert-success';
                        statusMessage.innerHTML = `
                            <h6>下载成功！</h6>
                            <p>下载数量：${progress.stats.downloaded}</p>
                            <p>失败数量：${progress.stats.failed}</p>
                            <p>跳过数量：${progress.stats.skipped}</p>
                            <p>总计数量：${progress.stats.total}</p>
                            <p>总计时间：${timeText}</p>
                        `;
                    } else {
                        statusMessage.className = 'status-message alert alert-success';
                        statusMessage.innerHTML = `
                            <h6>下载成功！</h6>
                            <p>下载数量：${progress.downloaded}</p>
                            <p>总计数量：${progress.total}</p>
                            <p>总计时间：${timeText}</p>
                        `;
                    }
                    statusMessage.style.display = 'block';
                    
                    // 停止进度条动画并设置为100%
                    const progressBar = document.querySelector('.progress-bar');
                    if (progressBar) {
                        progressBar.classList.remove('progress-bar-animated');
                        progressBar.style.width = '100%';
                        progressBar.setAttribute('aria-valuenow', 100);
                    }
                    
                    const progressText = document.getElementById('progressText');
                    if (progressText) {
                        progressText.textContent = '100%';
                    }
                    
                    const downloadSpeed = document.getElementById('downloadSpeed');
                    if (downloadSpeed) {
                        downloadSpeed.textContent = '0.0 KB/s';
                    }
                    
                    const etaTime = document.getElementById('etaTime');
                    if (etaTime) {
                        etaTime.textContent = '0 秒';
                    }
                    
                    this.isDownloading = false;
                    this.toggleDownloadButtons('initial');
                    this.stopStatusPolling();
                    if (this.eventSource) {
                        this.eventSource.close();
                        this.eventSource = null;
                    }
                    
                    // 隐藏下载进度区域
                    const downloadProgress = document.getElementById('downloadProgress');
                    if (downloadProgress) {
                        downloadProgress.style.display = 'none';
                    }
                } else {
                    // 更新进度条
                    const progressBar = document.querySelector('.progress-bar');
                    const progressText = document.getElementById('progressText');
                    const downloadedCountText = document.getElementById('downloadedCountText');
                    const totalCountText = document.getElementById('totalCountText');
                    const downloadSpeed = document.getElementById('downloadSpeed');
                    const etaTime = document.getElementById('etaTime');
                    
                    const percentage = progress.percentage || 0;
                    
                    if (progressBar) {
                        progressBar.style.width = `${percentage}%`;
                        progressBar.setAttribute('aria-valuenow', percentage);
                    }
                    
                    if (progressText) {
                        progressText.textContent = `${percentage}%`;
                    }
                    
                    if (downloadedCountText) {
                        downloadedCountText.textContent = progress.downloaded || 0;
                    }
                    
                    if (totalCountText) {
                        totalCountText.textContent = progress.total || 0;
                    }
                    
                    // 计算下载速度
                    const currentTime = Date.now();
                    const timeDiff = currentTime - lastTime;
                    const downloadDiff = (progress.downloaded || 0) - lastDownloaded;
                    const bytesDiff = Math.max(0, (progress.total_bytes || 0) - lastTotalBytes);
                    
                    if (timeDiff >= MIN_TIME_DIFF) {
                        // 计算当前速度（KB/s）
                        const speed = Math.max(0, (bytesDiff * 1000 / timeDiff) / 1024);
                        
                        // 应用速度上限，过滤异常值；0 表示停滞/暂停，不做下限抬升
                        const MAX_SPEED = 1000 * 1024; // 100 MB/s
                        const filteredSpeed = Math.min(speed, MAX_SPEED);
                        
                        // 添加到速度历史记录
                        speedHistory.push(filteredSpeed);
                        
                        // 限制历史记录长度
                        if (speedHistory.length > MAX_SPEED_HISTORY) {
                            speedHistory.shift();
                        }
                        
                        // 使用加权平均，最近的速度权重更高
                        let weightedSum = 0;
                        let totalWeight = 0;
                        const weights = [];
                        
                        // 生成权重数组
                        for (let i = 0; i < speedHistory.length; i++) {
                            const weight = i + 1;
                            weights.push(weight);
                            totalWeight += weight;
                        }
                        
                        // 计算加权平均
                        for (let i = 0; i < speedHistory.length; i++) {
                            weightedSum += speedHistory[i] * weights[i];
                        }
                        
                        const avgSpeed = Math.max(0, weightedSum / totalWeight);
                        
                        // 本窗口字节增量为 0：视为停滞/暂停，不做速度下限抬升
                        const isStalled = bytesDiff === 0;
                        
                        // 格式化速度显示（停滞或平均速度为 0 时显示 "-"）
                        if (isStalled || avgSpeed <= 0) {
                            if (downloadSpeed) {
                                downloadSpeed.textContent = '-';
                            }
                        } else {
                            let speedText;
                            if (avgSpeed < 1024) {
                                speedText = `${avgSpeed.toFixed(1)} KB/s`;
                            } else {
                                speedText = `${(avgSpeed / 1024).toFixed(1)} MB/s`;
                            }
                            
                            if (downloadSpeed) {
                                downloadSpeed.textContent = speedText;
                            }
                        }
                        
                        // 计算剩余时间
                        const remaining = (progress.total || 0) - (progress.downloaded || 0);
                        if (!isStalled && remaining > 0 && avgSpeed > 0) {
                            // 估算剩余字节数
                            const avgBytesPerTile = (progress.total_bytes || 0) / ((progress.downloaded || 0) || 1);
                            const remainingBytes = remaining * avgBytesPerTile;
                            let remainingTime = remainingBytes / (avgSpeed * 1024);
                            
                            // 平滑剩余时间
                            if (typeof lastEtaTime === 'number') {
                                const etaSmoothingFactor = 0.7;
                                remainingTime = lastEtaTime * (1 - etaSmoothingFactor) + remainingTime * etaSmoothingFactor;
                            }
                            lastEtaTime = remainingTime;
                            
                            // 格式化剩余时间
                            let etaText;
                            if (remainingTime < 60) {
                                etaText = `${Math.ceil(remainingTime)} 秒`;
                            } else if (remainingTime < 3600) {
                                const minutes = Math.floor(remainingTime / 60);
                                const seconds = Math.ceil(remainingTime % 60);
                                etaText = `${minutes} 分 ${seconds} 秒`;
                            } else {
                                const hours = Math.floor(remainingTime / 3600);
                                const minutes = Math.ceil((remainingTime % 3600) / 60);
                                etaText = `${hours} 小时 ${minutes} 分`;
                            }
                            
                            if (etaTime) {
                                etaTime.textContent = etaText;
                            }
                        } else {
                            if (etaTime) {
                                etaTime.textContent = '-';
                            }
                        }
                        
                        // 更新最后状态
                        lastDownloaded = progress.downloaded || 0;
                        lastTotalBytes = progress.total_bytes || 0;
                        lastTime = currentTime;
                    }
                    
                    statusMessage.className = 'status-message alert alert-info';
                    statusMessage.textContent = `正在下载... ${progress.downloaded || 0}/${progress.total || 0} (${percentage}%)`;
                    statusMessage.style.display = 'block';
                }
            } catch (error) {
                console.error('解析进度数据失败:', error);
            }
        };

        // 处理SSE错误
        this.eventSource.onerror = (error) => {
            console.error('SSE连接错误:', error);

            // 只关闭出错的这个连接，且必须仍是当前连接（避免关掉更新的流）
            const source = error && error.target;
            if (source && source === this.eventSource) {
                source.close();
            }

            // SSE 不可用时回退到轮询，保证界面状态仍然可见
            this.startStatusPolling();
        };
    }
}

export default CoreModule;

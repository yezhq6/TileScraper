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
    }

    /**
     * 初始化应用
     */
    init() {
        console.log('初始化TileHarvester应用...');

        // 初始化地图
        this.mapModule.initMap();

        // 绑定事件
        this.bindEvents();

        // 加载提供商列表
        this.loadProviders();

        // 加载配置列表
        this.loadConfigList();
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
    }

    /**
     * 更新边界输入框
     * @param {Object} bbox - 边界对象
     */
    updateBboxInputs(bbox) {
        document.getElementById('manualNorth').value = bbox.north.toFixed(6);
        document.getElementById('manualSouth').value = bbox.south.toFixed(6);
        document.getElementById('manualWest').value = bbox.west.toFixed(6);
        document.getElementById('manualEast').value = bbox.east.toFixed(6);
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
     * 加载提供商列表
     */
    loadProviders() {
        // 如果有提供商选择框，加载提供商列表
        const providerSelect = document.getElementById('providerSelect');
        if (providerSelect) {
            fetch('/api/providers')
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        providerSelect.innerHTML = '';
                        data.providers.forEach(provider => {
                            const option = document.createElement('option');
                            option.value = provider.name;
                            option.textContent = provider.name;
                            providerSelect.appendChild(option);
                        });
                    }
                })
                .catch(error => {
                    console.error('加载提供商列表失败:', error);
                });
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
        let totalTiles = 0;
        for (let zoom = minZoom; zoom <= maxZoom; zoom++) {
            const tilesInZoom = this.calculateTilesInBbox(west, south, east, north, zoom);
            totalTiles += tilesInZoom;
        }

        document.getElementById('tileCountResult').textContent = `总计：${totalTiles}`;
        this.showStatus(`瓦片数量计算完成: ${totalTiles}`, 'success');
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
        const threads = parseInt(document.getElementById('threads').value);
        const tileFormat = document.getElementById('tileFormat').value;
        const tms = document.getElementById('tms').checked;

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
            threads: threads || 4,
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
        this.toggleDownloadButtons('downloading');
        this.showStatus('下载任务已开始', 'success');

        // 显示下载进度区域
        const downloadProgress = document.getElementById('downloadProgress');
        if (downloadProgress) {
            downloadProgress.style.display = 'block';
        }

        // 进度相关变量
        let startTime = Date.now();
        let isCancelled = false;

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
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    this.showStatus('下载已暂停', 'warning');
                } else {
                    // 如果暂停失败，恢复按钮状态
                    this.toggleDownloadButtons('downloading');
                    this.showStatus('暂停失败: ' + result.message, 'danger');
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
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    this.showStatus('下载已恢复', 'info');
                } else {
                    // 如果继续失败，恢复按钮状态
                    this.toggleDownloadButtons('paused');
                    this.showStatus('继续失败: ' + result.message, 'danger');
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
            isCancelled = true;
            
            // 立即更新按钮状态，给用户反馈
            this.toggleDownloadButtons('initial');
            
            // 发送取消请求
            fetch('/api/cancel-download', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP错误！状态码: ${response.status}`);
                }
                return response.json();
            })
            .then(result => {
                if (result.success) {
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
                } else if (result.message !== '没有正在进行的下载任务') {
                    // 只显示非"没有正在进行的下载任务"的错误信息
                    this.showStatus('取消失败: ' + result.message, 'danger');
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
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP错误！状态码: ${response.status}`);
            }
            return response.json();
        })
        .then(result => {
            if (!result.success) {
                // 如果初始请求失败，关闭SSE连接
                if (this.eventSource) {
                    this.eventSource.close();
                    this.eventSource = null;
                }
                const statusMessage = document.getElementById('statusMessage');
                statusMessage.className = 'status-message alert alert-danger';
                statusMessage.textContent = `下载失败：${result.error}`;
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
        let isCancelled = false;

        // 创建新的事件源
        this.eventSource = new EventSource('/api/progress');

        // 监听消息事件
        this.eventSource.onmessage = (event) => {
            try {
                const progress = JSON.parse(event.data);
                const statusMessage = document.getElementById('statusMessage');
                
                // 检查数据有效性
                if (typeof progress.downloaded !== 'number' || typeof progress.total !== 'number') {
                    console.error('无效的进度数据:', progress);
                    return;
                }
                
                if (progress.completed) {
                    // 如果已经取消下载，忽略完成事件
                    if (isCancelled) {
                        this.eventSource.close();
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
                    this.eventSource.close();
                    
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
                        
                        // 应用速度限制，过滤异常值
                        const MAX_SPEED = 1000 * 1024; // 100 MB/s
                        const MIN_SPEED = 0.1; // 0.1 KB/s
                        const filteredSpeed = Math.min(Math.max(speed, MIN_SPEED), MAX_SPEED);
                        
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
                        
                        // 格式化速度显示
                        let speedText;
                        if (avgSpeed < 1024) {
                            speedText = `${avgSpeed.toFixed(1)} KB/s`;
                        } else {
                            speedText = `${(avgSpeed / 1024).toFixed(1)} MB/s`;
                        }
                        
                        if (downloadSpeed) {
                            downloadSpeed.textContent = speedText;
                        }
                        
                        // 计算剩余时间
                        const remaining = (progress.total || 0) - (progress.downloaded || 0);
                        if (remaining > 0 && avgSpeed > 0) {
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
            this.eventSource.close();
        };
    }
}

export default CoreModule;

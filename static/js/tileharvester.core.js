/**
 * TileHarvester Core Module
 * 核心功能和初始化代码
 */

const TileHarvester = {
    // 初始化
    init: function() {
        this.map = null;
        this.drawnItems = null;
        this.currentBbox = null;
        this.bingLayer = null;
        this.eventSource = null;
        this.isDownloading = false;
        
        // 初始化地图
        this.initMap();
        
        // 加载配置列表
        this.loadConfigList();
        
        // 检查后端下载状态
        this.checkDownloadStatus();
        
        // 获取当前下载参数
        this.getDownloadParams();
        
        // 绑定事件监听器
        this.bindEventListeners();
    },
    
    // 检查后端下载状态
    checkDownloadStatus: function() {
        fetch('/api/download-status')
            .then(response => response.json())
            .then(data => {
                if (data.success && data.is_downloading) {
                    // 后端正在下载，同步前端状态
                    this.isDownloading = true;
                    this.toggleDownloadButtons('downloading');
                    
                    // 显示下载状态
                    const statusMessage = document.getElementById('statusMessage');
                    statusMessage.className = 'status-message alert alert-info';
                    statusMessage.textContent = '正在下载...';
                    statusMessage.style.display = 'block';
                    
                    // 显示进度条
                    const downloadProgress = document.getElementById('downloadProgress');
                    downloadProgress.style.display = 'block';
                    
                    // 重新建立SSE连接
                    this.setupSSEConnection();
                }
            })
            .catch(error => {
                console.error('检查下载状态失败:', error);
            });
    },
    
    // 绑定事件监听器
    bindEventListeners: function() {
        // 绘制区域
        document.getElementById('drawBboxBtn').addEventListener('click', this.drawBbox.bind(this));
        
        // 清除区域
        document.getElementById('clearBboxBtn').addEventListener('click', this.clearBbox.bind(this));
        
        // 适配视图
        document.getElementById('fitBboxBtn').addEventListener('click', this.fitBbox.bind(this));
        
        // 应用手动设置的边界坐标
        document.getElementById('applyManualBboxBtn').addEventListener('click', this.applyManualBbox.bind(this));
        
        // 计算瓦片数量按钮事件
        document.getElementById('calculateTilesBtn').addEventListener('click', this.calculateTilesCount.bind(this));
        
        // 下载表单提交
        document.getElementById('downloadForm').addEventListener('submit', this.handleDownloadSubmit.bind(this));
        
        // 保存配置
        document.getElementById('saveConfigBtn').addEventListener('click', this.saveConfig.bind(this));
        
        // 加载配置
        document.getElementById('loadConfigSelect').addEventListener('change', this.loadConfig.bind(this));
    },
    
    // 切换下载控制按钮状态
    toggleDownloadButtons: function(state) {
        const downloadBtn = document.getElementById('downloadBtn');
        const pauseBtn = document.getElementById('pauseBtn');
        const resumeBtn = document.getElementById('resumeBtn');
        const cancelBtn = document.getElementById('cancelBtn');
        
        switch(state) {
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
    },
    
    // 显示状态信息
    showStatus: function(message, type = 'info') {
        const statusMessage = document.getElementById('statusMessage');
        statusMessage.className = `status-message alert alert-${type}`;
        statusMessage.textContent = message;
        statusMessage.style.display = 'block';
        
        // 3秒后自动隐藏
        if (window.statusTimeout) {
            clearTimeout(window.statusTimeout);
        }
        window.statusTimeout = setTimeout(() => {
            statusMessage.style.display = 'none';
        }, 3000);
    },
    
    // 加载配置列表
    loadConfigList: function() {
        fetch('/api/config/list')
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const select = document.getElementById('loadConfigSelect');
                    select.innerHTML = '<option value="">加载配置...</option>';
                    
                    data.configs.forEach(config => {
                        const option = document.createElement('option');
                        option.value = config.name;
                        option.textContent = config.name;
                        select.appendChild(option);
                    });
                }
            })
            .catch(error => {
                console.error('加载配置列表失败:', error);
            });
    },
    
    // 保存配置
    saveConfig: function() {
        const configName = document.getElementById('configName').value.trim();
        if (!configName) {
            this.showStatus('请输入配置名称！', 'danger');
            return;
        }
        
        // 获取当前所有参数
        const formData = new FormData(document.getElementById('downloadForm'));
        const configData = Object.fromEntries(formData);
        
        // 添加TMS选项
        configData.tms = document.getElementById('tms').checked;
        
        // 添加边界框数据
        if (this.currentBbox) {
            configData.north = this.currentBbox.north;
            configData.south = this.currentBbox.south;
            configData.west = this.currentBbox.west;
            configData.east = this.currentBbox.east;
        } else {
            // 使用手动输入的边界坐标
            configData.north = parseFloat(document.getElementById('manualNorth').value);
            configData.south = parseFloat(document.getElementById('manualSouth').value);
            configData.west = parseFloat(document.getElementById('manualWest').value);
            configData.east = parseFloat(document.getElementById('manualEast').value);
        }
        
        // 处理子域名列表
        configData.subdomains = configData.subdomains ? configData.subdomains.split(',').map(s => s.trim()).filter(s => s) : [];
        
        // 保存配置
        fetch('/api/config/save', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ config_name: configName, config_data: configData })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                this.showStatus('配置保存成功！', 'success');
                // 重新加载配置列表
                this.loadConfigList();
            } else {
                this.showStatus('配置保存失败: ' + data.error, 'danger');
            }
        })
        .catch(error => {
            console.error('保存配置失败:', error);
            this.showStatus('配置保存失败: ' + error.message, 'danger');
        });
    },
    
    // 加载配置
    loadConfig: function(e) {
        const configName = e.target.value;
        if (!configName) return;
        
        fetch(`/api/config/load/${configName}`)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const config = data.config;
                    const configData = config.data;
                    
                    // 填充表单数据
                    document.getElementById('providerUrl').value = configData.provider_url || '';
                    document.getElementById('outputPath').value = configData.output_path || '';
                    document.getElementById('saveFormat').value = configData.save_format || 'directory';
                    document.getElementById('subdomains').value = configData.subdomains ? configData.subdomains.join(',') : '';
                    document.getElementById('minZoom').value = configData.min_zoom || 1;
                    document.getElementById('maxZoom').value = configData.max_zoom || 18;
                    document.getElementById('threads').value = configData.threads || 4;
                    document.getElementById('tileFormat').value = configData.tile_format || 'jpg';
                    document.getElementById('tms').checked = configData.tms || false;
                    
                    // 填充边界坐标
                    if (configData.north) document.getElementById('manualNorth').value = configData.north;
                    if (configData.south) document.getElementById('manualSouth').value = configData.south;
                    if (configData.west) document.getElementById('manualWest').value = configData.west;
                    if (configData.east) document.getElementById('manualEast').value = configData.east;
                    
                    // 如果有边界坐标，应用边界
                    if (configData.north && configData.south && configData.west && configData.east) {
                        this.currentBbox = {
                            north: configData.north,
                            south: configData.south,
                            west: configData.west,
                            east: configData.east
                        };
                    }
                    
                    this.showStatus('配置加载成功！', 'success');
                } else {
                    this.showStatus('配置加载失败: ' + data.error, 'danger');
                }
            })
            .catch(error => {
                console.error('加载配置失败:', error);
                this.showStatus('配置加载失败: ' + error.message, 'danger');
            });
    },
    
    // 获取当前下载参数
    getDownloadParams: function() {
        fetch('/api/download-params')
            .then(response => response.json())
            .then(data => {
                if (data.success && data.params) {
                    const params = data.params;
                    
                    // 填充表单数据
                    document.getElementById('providerUrl').value = params.provider_url || '';
                    document.getElementById('outputPath').value = params.output_dir || '';
                    document.getElementById('saveFormat').value = params.save_format || 'directory';
                    document.getElementById('subdomains').value = params.subdomains ? params.subdomains.join(',') : '';
                    document.getElementById('minZoom').value = params.min_zoom || 1;
                    document.getElementById('maxZoom').value = params.max_zoom || 18;
                    document.getElementById('threads').value = params.threads || 4;
                    document.getElementById('tileFormat').value = params.tile_format || 'jpg';
                    document.getElementById('tms').checked = params.tms || false;
                    
                    // 填充边界坐标
                    if (params.north) document.getElementById('manualNorth').value = params.north;
                    if (params.south) document.getElementById('manualSouth').value = params.south;
                    if (params.west) document.getElementById('manualWest').value = params.west;
                    if (params.east) document.getElementById('manualEast').value = params.east;
                    
                    // 如果有边界坐标，应用边界
                    if (params.north && params.south && params.west && params.east) {
                        this.currentBbox = {
                            north: params.north,
                            south: params.south,
                            west: params.west,
                            east: params.east
                        };
                        
                        // 应用边界到地图
                        this.applyManualBbox();
                    }
                    
                    this.showStatus('下载参数加载成功！', 'success');
                }
            })
            .catch(error => {
                console.error('获取下载参数失败:', error);
            });
    }
};

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    TileHarvester.init();
});

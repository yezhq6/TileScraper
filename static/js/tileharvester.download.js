/**
 * TileHarvester Download Module
 * 下载和进度处理功能
 */

// 设置SSE连接
TileHarvester.setupSSEConnection = function() {
    // 关闭现有的连接
    if (this.eventSource) {
        this.eventSource.close();
    }
    
    // 建立新的SSE连接
    this.eventSource = new EventSource('/api/progress');
    
    // 进度相关变量
    let startTime = Date.now();
    let lastDownloaded = 0;
    let lastTotalBytes = 0;
    let lastTime = Date.now();
    let lastEtaTime = null; // 上一次的剩余时间
    let speedHistory = [];
    const MAX_SPEED_HISTORY = 100; // 增加历史记录长度到100条
    const MIN_TIME_DIFF = 1000; // 最小时间差（毫秒），避免高频更新导致的波动
    let isCancelled = false;
    
    // 监听进度事件
    this.eventSource.onmessage = function(event) {
        try {
            const progressData = JSON.parse(event.data);
            const { downloaded, total, total_bytes = 0, percentage, completed, stats } = progressData;
            
            // 检查数据有效性
            if (typeof downloaded !== 'number' || typeof total !== 'number') {
                console.error('无效的进度数据:', progressData);
                return;
            }
            
            // 更新右侧进度条
            const downloadProgress = document.getElementById('downloadProgress');
            const progressBar = downloadProgress.querySelector('.progress-bar');
            progressBar.style.width = `${percentage}%`;
            progressBar.setAttribute('aria-valuenow', percentage);
            document.getElementById('progressText').textContent = `${percentage}%`;
            document.getElementById('downloadedCountText').textContent = downloaded;
            document.getElementById('totalCountText').textContent = total;
            
            // 计算下载速度
            const currentTime = Date.now();
            const timeDiff = currentTime - lastTime;
            const downloadDiff = downloaded - lastDownloaded;
            const bytesDiff = Math.max(0, total_bytes - lastTotalBytes); // 确保字节差不为负
            
            // 使用实际下载的字节数来计算速度，而不是瓦片数量
            if (timeDiff >= MIN_TIME_DIFF) {
                // 计算当前速度（KB/s），确保速度不为负
                const speed = Math.max(0, (bytesDiff * 1000 / timeDiff) / 1024);
                
                // 应用速度限制，过滤异常值
                const MAX_SPEED = 1000 * 1024; // 100 MB/s，设置合理的上限
                const MIN_SPEED = 0.1; // 0.1 KB/s，设置合理的下限
                const filteredSpeed = Math.min(Math.max(speed, MIN_SPEED), MAX_SPEED);
                
                speedHistory.push(filteredSpeed);
                
                // 限制历史记录长度
                if (speedHistory.length > MAX_SPEED_HISTORY) {
                    speedHistory.shift();
                }
                
                // 使用加权平均，最近的速度权重更高
                let weightedSum = 0;
                let totalWeight = 0;
                const weights = [];
                
                // 生成权重数组，最近的速度权重更高
                for (let i = 0; i < speedHistory.length; i++) {
                    const weight = i + 1; // 权重从1到speedHistory.length
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
                document.getElementById('downloadSpeed').textContent = speedText;
                
                // 计算剩余时间
                const remaining = total - downloaded;
                if (remaining > 0 && avgSpeed > 0) {
                    // 估算剩余字节数：假设每个瓦片平均大小
                    const avgBytesPerTile = total_bytes / (downloaded || 1);
                    const remainingBytes = remaining * avgBytesPerTile;
                    let remainingTime = remainingBytes / (avgSpeed * 1024);
                    
                    // 平滑剩余时间：与上一次剩余时间进行加权平均
                    if (typeof lastEtaTime === 'number') {
                        const etaSmoothingFactor = 0.7; // 平滑因子，值越大变化越慢
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
                    document.getElementById('etaTime').textContent = etaText;
                } else {
                    document.getElementById('etaTime').textContent = '-';
                }
                
                // 更新最后状态
                lastDownloaded = downloaded;
                lastTotalBytes = total_bytes;
                lastTime = currentTime;
            }
            
            // 更新状态信息
            if (completed) {
                // 如果已经取消下载，忽略完成事件
                if (isCancelled) {
                    this.eventSource.close();
                    return;
                }
                
                // 检查是否真的完成
                if (total === 0) {
                    console.warn('收到完成事件，但总任务数为0，忽略');
                    return;
                }
                
                // 下载完成
                this.eventSource.close();
                
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
                const statusMessage = document.getElementById('statusMessage');
                if (stats) {
                    statusMessage.className = 'status-message alert alert-success';
                    statusMessage.innerHTML = `
                        <h6>下载成功！</h6>
                        <p>下载数量：${stats.downloaded}</p>
                        <p>失败数量：${stats.failed}</p>
                        <p>跳过数量：${stats.skipped}</p>
                        <p>总计数量：${stats.total}</p>
                        <p>总计时间：${timeText}</p>
                    `;
                    statusMessage.style.display = 'block';
                } else {
                    statusMessage.className = 'status-message alert alert-success';
                    statusMessage.innerHTML = `
                        <h6>下载成功！</h6>
                        <p>下载数量：${downloaded}</p>
                        <p>总计数量：${total}</p>
                        <p>总计时间：${timeText}</p>
                    `;
                    statusMessage.style.display = 'block';
                }
                
                // 停止进度条动画并设置为100%
                const progressBar = document.getElementById('downloadProgress').querySelector('.progress-bar');
                progressBar.classList.remove('progress-bar-animated');
                progressBar.style.width = '100%';
                progressBar.setAttribute('aria-valuenow', 100);
                document.getElementById('progressText').textContent = '100%';
                document.getElementById('downloadSpeed').textContent = '0.0 KB/s';
                document.getElementById('etaTime').textContent = '0 秒';
                
                // 恢复按钮状态
                TileHarvester.toggleDownloadButtons('initial');
                TileHarvester.isDownloading = false;
            } else {
                // 正在下载
                const statusMessage = document.getElementById('statusMessage');
                statusMessage.textContent = `正在下载... ${downloaded}/${total} (${percentage}%)`;
            }
        } catch (error) {
            console.error('处理进度数据失败:', error);
        }
    }.bind(this);
    
    // 处理SSE错误
    this.eventSource.onerror = function(error) {
        console.error('SSE连接错误:', error);
        this.eventSource.close();
    }.bind(this);
};

// 处理下载表单提交
TileHarvester.handleDownloadSubmit = function(e) {
    e.preventDefault();
    
    if (!this.currentBbox) {
        this.showStatus('请先绘制下载区域！', 'danger');
        return;
    }
    
    // 获取表单数据
    const formData = new FormData(e.target);
    const data = Object.fromEntries(formData);
    
    // 添加TMS选项
    data.tms = document.getElementById('tms').checked;
    
    // 添加边界框数据
    data.north = this.currentBbox.north;
    data.south = this.currentBbox.south;
    data.west = this.currentBbox.west;
    data.east = this.currentBbox.east;
    
    // 处理子域名列表
    data.subdomains = data.subdomains ? data.subdomains.split(',').map(s => s.trim()).filter(s => s) : [];
    
    // 重命名参数：output_path 改为 output_dir
    data.output_dir = data.output_path;
    delete data.output_path;
    
    // 显示加载状态
    const downloadBtn = document.getElementById('downloadBtn');
    const pauseBtn = document.getElementById('pauseBtn');
    const resumeBtn = document.getElementById('resumeBtn');
    const cancelBtn = document.getElementById('cancelBtn');
    const statusMessage = document.getElementById('statusMessage');
    const downloadProgress = document.getElementById('downloadProgress');
    
    // 显示暂停和取消按钮，隐藏下载按钮
    this.toggleDownloadButtons('downloading');
    
    statusMessage.className = 'status-message alert alert-info';
    statusMessage.textContent = '正在准备下载...';
    statusMessage.style.display = 'block';
    
    downloadProgress.style.display = 'block';
    
    // 进度相关变量
    let startTime = Date.now();
    let isCancelled = false;
    
    // 设置下载状态
    this.isDownloading = true;
    
    // 建立SSE连接，实时接收进度更新
    this.setupSSEConnection();
    
    // 暂停按钮事件处理
    pauseBtn.addEventListener('click', function() {
        // 立即更新按钮状态，给用户反馈
        TileHarvester.toggleDownloadButtons('paused');
        
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
                TileHarvester.showStatus('下载已暂停', 'warning');
            } else {
                // 如果暂停失败，恢复按钮状态
                TileHarvester.toggleDownloadButtons('downloading');
                TileHarvester.showStatus('暂停失败: ' + result.message, 'danger');
            }
        })
        .catch(error => {
            // 如果请求失败，恢复按钮状态
            TileHarvester.toggleDownloadButtons('downloading');
            console.error('暂停下载失败:', error);
            TileHarvester.showStatus('暂停下载失败: ' + error.message, 'danger');
        });
    });
    
    // 继续按钮事件处理
    resumeBtn.addEventListener('click', function() {
        // 立即更新按钮状态，给用户反馈
        TileHarvester.toggleDownloadButtons('downloading');
        
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
                TileHarvester.showStatus('下载已恢复', 'info');
            } else {
                // 如果继续失败，恢复按钮状态
                TileHarvester.toggleDownloadButtons('paused');
                TileHarvester.showStatus('继续失败: ' + result.message, 'danger');
            }
        })
        .catch(error => {
            // 如果请求失败，恢复按钮状态
            TileHarvester.toggleDownloadButtons('paused');
            console.error('继续下载失败:', error);
            TileHarvester.showStatus('继续下载失败: ' + error.message, 'danger');
        });
    });
    
    // 取消按钮事件处理
    cancelBtn.addEventListener('click', function() {
        // 设置取消标志
        isCancelled = true;
        
        // 立即更新按钮状态，给用户反馈
        TileHarvester.toggleDownloadButtons('initial');
        
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
                    TileHarvester.showStatus('下载已取消', 'warning');
                }
            } else if (result.message !== '没有正在进行的下载任务') {
                // 只显示非"没有正在进行的下载任务"的错误信息
                TileHarvester.showStatus('取消失败: ' + result.message, 'danger');
            }
            
            // 关闭SSE连接
            if (TileHarvester.eventSource) {
                TileHarvester.eventSource.close();
                TileHarvester.eventSource = null;
            }
        })
        .catch(error => {
            console.error('取消下载失败:', error);
            TileHarvester.showStatus('取消下载失败: ' + error.message, 'danger');
            
            // 关闭SSE连接
            if (TileHarvester.eventSource) {
                TileHarvester.eventSource.close();
                TileHarvester.eventSource = null;
            }
        });
    });
    
    // 发送下载请求
    fetch('/api/download', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
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
            if (TileHarvester.eventSource) {
                TileHarvester.eventSource.close();
                TileHarvester.eventSource = null;
            }
            statusMessage.className = 'status-message alert alert-danger';
            statusMessage.textContent = `下载失败：${result.error}`;
            
            // 恢复按钮状态
            TileHarvester.toggleDownloadButtons('initial');
        }
    })
    .catch(error => {
        // 关闭SSE连接
        if (TileHarvester.eventSource) {
            TileHarvester.eventSource.close();
            TileHarvester.eventSource = null;
        }
        
        statusMessage.className = 'status-message alert alert-danger';
        statusMessage.textContent = `请求失败：${error.message}`;
        console.error('下载请求错误:', error);
        
        // 恢复按钮状态
        TileHarvester.toggleDownloadButtons('initial');
    });
};

// 计算瓦片数量
TileHarvester.calculateTilesCount = function() {
    // 直接从currentBbox变量获取边界坐标，而不是从DOM元素
    if (!this.currentBbox) {
        this.showStatus('请先绘制或设置边界坐标！', 'danger');
        return;
    }
    
    const { north, south, west, east } = this.currentBbox;
    
    // 获取缩放级别
    const minZoom = parseInt(document.getElementById('minZoom').value);
    const maxZoom = parseInt(document.getElementById('maxZoom').value);
    
    // 验证缩放级别
    if (isNaN(minZoom) || isNaN(maxZoom) || minZoom > maxZoom) {
        this.showStatus('请设置有效的缩放级别范围！', 'danger');
        return;
    }
    
    // 墨卡托投影有效纬度范围：约±85.0511度
    const max_valid_lat = 85.0511;
    const min_valid_lat = -85.0511;
    
    // 限制经纬度在有效范围内
    const valid_north = Math.min(Math.max(north, min_valid_lat), max_valid_lat);
    const valid_south = Math.min(Math.max(south, min_valid_lat), max_valid_lat);
    const valid_west = Math.min(Math.max(west, -180), 180);
    const valid_east = Math.min(Math.max(east, -180), 180);
    
    // 计算每个缩放级别的瓦片数量
    let totalTiles = 0;
    for (let zoom = minZoom; zoom <= maxZoom; zoom++) {
        // 计算瓦片坐标范围，与后端保持一致
        const n = Math.pow(2, zoom);
        
        try {
            // 左上角瓦片坐标（向下取整）
            const min_x = Math.floor((valid_west + 180.0) / 360.0 * n);
            const tan_north = Math.tan(valid_north * Math.PI / 180);
            const cos_north = Math.cos(valid_north * Math.PI / 180);
            const min_y = Math.floor((1.0 - Math.log(tan_north + 1.0 / cos_north) / Math.PI) / 2.0 * n);
            
            // 右下角瓦片坐标（向上取整，使用1e-10避免浮点精度问题）
            const max_x = Math.ceil(((valid_east + 180.0) / 360.0 * n) - 1e-10);
            const tan_south = Math.tan(valid_south * Math.PI / 180);
            const cos_south = Math.cos(valid_south * Math.PI / 180);
            const max_y = Math.ceil(((1.0 - Math.log(tan_south + 1.0 / cos_south) / Math.PI) / 2.0 * n) - 1e-10);
            
            // 纠正顺序，保证 min <= max
            let corrected_min_x = Math.min(min_x, max_x);
            let corrected_max_x = Math.max(min_x, max_x);
            let corrected_min_y = Math.min(min_y, max_y);
            let corrected_max_y = Math.max(min_y, max_y);
            
            // 限制瓦片坐标在有效范围内（每个缩放级别瓦片坐标范围是0到n-1）
            const max_tile_coord = n - 1;
            corrected_min_x = Math.max(0, corrected_min_x);
            corrected_max_x = Math.min(max_tile_coord, corrected_max_x);
            corrected_min_y = Math.max(0, corrected_min_y);
            corrected_max_y = Math.min(max_tile_coord, corrected_max_y);
            
            // 计算当前缩放级别的瓦片数量
            const tilesAtZoom = (corrected_max_x - corrected_min_x + 1) * (corrected_max_y - corrected_min_y + 1);
            totalTiles += tilesAtZoom;
        } catch (error) {
            console.error('瓦片计算错误:', error);
            this.showStatus('瓦片计算错误: 请检查经纬度范围是否有效', 'danger');
            return;
        }
    }
    
    // 在按钮旁边显示统计结果
    const tileCountResult = document.getElementById('tileCountResult');
    tileCountResult.textContent = `总计：${totalTiles} 个瓦片`;
    tileCountResult.className = 'text-primary font-weight-bold';
    
    // 显示成功提示
    this.showStatus(`瓦片数量统计完成，总计 ${totalTiles} 个瓦片！`, 'success');
};

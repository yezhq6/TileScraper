/**
 * TileHarvester Map Module
 * 地图相关功能
 */

// 扩展 TileHarvester 对象，添加地图相关功能
TileHarvester.initMap = function() {
    // 设置默认视图为全球范围，缩放级别为1，最小缩放级别为1
    this.map = L.map('map', {
        minZoom: 1,
        maxZoom: 20
    }).setView([0, 0], 1);
    
    // 初始化绘图控件
    this.drawnItems = new L.FeatureGroup();
    this.map.addLayer(this.drawnItems);
    
    // 监听绘图事件
    this.map.on('click', this.handleMapClick.bind(this));
    
    // 使用Bing地图图层
    const bingUrlTemplate = 'http://ecn.{s}.tiles.virtualearth.net/tiles/a{q}.jpeg?g=1';
    
    // 创建Bing图层
    this.bingLayer = new this.BingTileLayer(bingUrlTemplate, {
        attribution: '&copy; Microsoft Corporation',
        crossOrigin: 'anonymous',
        subdomains: ['t0', 't1', 't2', 't3']
    });
    
    // 添加Bing图层到地图
    this.bingLayer.addTo(this.map);
    
    // 确保地图视图设置为全球范围
    this.map.setView([0, 0], 1);
    
    // 显示地图加载成功信息
    this.showStatus('Bing地图加载成功！', 'success');
};

// 处理地图点击事件
TileHarvester.handleMapClick = function(e) {
    // 这里可以添加点击处理逻辑
};

// 绘制区域
TileHarvester.drawBbox = function() {
    // 简单实现：点击地图添加点，双击结束绘制区域
    let points = [];
    let tempMarkers = [];
    
    const onMapClick = (e) => {
        points.push(e.latlng);
        
        // 添加临时标记并保存到数组
        let marker = L.marker(e.latlng).addTo(this.map);
        tempMarkers.push(marker);
        
        if (points.length >= 2) {
            // 计算边界框
            let north = Math.max(points[0].lat, points[1].lat);
            let south = Math.min(points[0].lat, points[1].lat);
            let east = Math.max(points[0].lng, points[1].lng);
            let west = Math.min(points[0].lng, points[1].lng);
            
            // 创建边界框
            let bbox = L.rectangle([[south, west], [north, east]], {
                color: 'red',
                weight: 2,
                fillColor: 'red',
                fillOpacity: 0.1
            });
            
            // 清除之前的绘制和临时标记
            this.drawnItems.clearLayers();
            tempMarkers.forEach(marker => this.map.removeLayer(marker));
            
            // 添加边界框
            this.drawnItems.addLayer(bbox);
            
            // 更新边界框信息
            this.updateBboxInfo(north, south, west, east);
            
            // 更新手动输入框的值
            this.updateManualInputFields(north, south, west, east);
            
            // 移除事件监听器
            this.map.off('click', onMapClick);
            
            // 显示成功提示
            this.showStatus('区域绘制完成！', 'success');
        }
    };
    
    this.map.on('click', onMapClick);
    this.showStatus('请点击地图上的两个点来绘制下载区域（对角线）', 'info');
};

// 清除区域
TileHarvester.clearBbox = function() {
    // 清除所有绘制的图层
    this.drawnItems.clearLayers();
    
    // 清除边界框信息
    this.clearBboxInfo();
    
    // 重置手动输入框
    this.resetManualInputFields();
    
    // 显示状态
    this.showStatus('区域已清除', 'info');
};

// 适配视图
TileHarvester.fitBbox = function() {
    if (this.drawnItems.getLayers().length > 0) {
        this.map.fitBounds(this.drawnItems.getBounds());
        this.showStatus('视图已适配区域', 'info');
    } else {
        this.showStatus('没有绘制区域可适配', 'warning');
    }
};

// 应用手动设置的边界坐标
TileHarvester.applyManualBbox = function() {
    // 获取手动输入的边界坐标
    const manualNorth = parseFloat(document.getElementById('manualNorth').value);
    const manualSouth = parseFloat(document.getElementById('manualSouth').value);
    const manualWest = parseFloat(document.getElementById('manualWest').value);
    const manualEast = parseFloat(document.getElementById('manualEast').value);
    
    // 验证输入
    if (isNaN(manualNorth) || isNaN(manualSouth) || isNaN(manualWest) || isNaN(manualEast)) {
        this.showStatus('请填写完整的边界坐标！', 'danger');
        return;
    }
    
    if (manualNorth <= manualSouth) {
        this.showStatus('北坐标必须大于南坐标！', 'danger');
        return;
    }
    
    if (manualEast <= manualWest) {
        this.showStatus('东坐标必须大于西坐标！', 'danger');
        return;
    }
    
    // 清除之前的绘制
    this.drawnItems.clearLayers();
    
    // 创建边界框 - 使用红色边框
    const bbox = L.rectangle([[manualSouth, manualWest], [manualNorth, manualEast]], {
        color: 'red',
        weight: 2,
        fillColor: 'red',
        fillOpacity: 0.1
    });
    
    // 添加边界框到地图
    this.drawnItems.addLayer(bbox);
    
    // 更新边界框信息
    this.updateBboxInfo(manualNorth, manualSouth, manualWest, manualEast);
    
    // 适配视图
    this.map.fitBounds(bbox.getBounds());
    
    // 显示成功提示
    this.showStatus('边界坐标已应用！', 'success');
};

// 更新边界框信息
TileHarvester.updateBboxInfo = function(north, south, west, east) {
    this.currentBbox = {
        north: north,
        south: south,
        west: west,
        east: east
    };
};

// 清除边界框信息
TileHarvester.clearBboxInfo = function() {
    this.currentBbox = null;
};

// 更新手动输入框的值
TileHarvester.updateManualInputFields = function(north, south, west, east) {
    document.getElementById('manualNorth').value = north.toFixed(6);
    document.getElementById('manualSouth').value = south.toFixed(6);
    document.getElementById('manualWest').value = west.toFixed(6);
    document.getElementById('manualEast').value = east.toFixed(6);
};

// 重置手动输入框
TileHarvester.resetManualInputFields = function() {
    document.getElementById('manualNorth').value = '90';
    document.getElementById('manualSouth').value = '-90';
    document.getElementById('manualWest').value = '-180';
    document.getElementById('manualEast').value = '180';
};

// BingTileLayer 类定义
TileHarvester.BingTileLayer = L.TileLayer.extend({
    initialize: function(url, options) {
        L.TileLayer.prototype.initialize.call(this, url, options);
    },
    
    getTileUrl: function(coords) {
        // 生成Bing地图的quadkey
        let quadkey = this._tileCoordsToQuadKey(coords);
        
        // 当缩放级别为0时，使用一个默认的有效QuadKey
        if (coords.z === 0) {
            quadkey = '';
        }
        
        // 使用字符串替换来生成URL，而不是L.Util.template
        let url = this._url;
        
        // 替换子域名占位符{s}
        if (url.includes('{s}')) {
            const subdomain = this._getSubdomain(coords);
            url = url.replace('{s}', subdomain);
        }
        
        // 替换QuadKey占位符{q}
        if (url.includes('{q}')) {
            url = url.replace('{q}', quadkey);
        }
        
        return url;
    },
    
    _tileCoordsToQuadKey: function(coords) {
        // 将瓦片坐标转换为Bing地图的quadkey
        let quadkey = '';
        let x = coords.x;
        let y = coords.y;
        let z = coords.z;
        
        // 处理缩放级别为0的情况
        if (z === 0) {
            return '0';
        }
        
        // 重新实现 QuadKey 生成算法
        for (let i = z; i > 0; i--) {
            let digit = 0;
            let mask = 1 << (i - 1);
            
            if ((x & mask) !== 0) {
                digit |= 1;
            }
            if ((y & mask) !== 0) {
                digit |= 2;
            }
            quadkey += digit;
        }
        
        return quadkey;
    }
});

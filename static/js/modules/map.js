/**
 * 地图模块
 */

import { BingTileLayer } from './bing.js';

class MapModule {
    constructor() {
        this.map = null;
        this.drawnItems = null;
        this.currentBbox = null;
        this.bingLayer = null;
    }

    /**
     * 初始化地图
     */
    initMap() {
        // 初始化地图，设置默认视图为全球范围
        this.map = L.map('map', {
            minZoom: 1,
            maxZoom: 20
        }).setView([0, 0], 1);

        // 初始化绘图层
        this.drawnItems = new L.FeatureGroup();
        this.map.addLayer(this.drawnItems);

        // 使用Bing地图图层
        const bingUrlTemplate = 'http://ecn.{s}.tiles.virtualearth.net/tiles/a{q}.jpeg?g=1';

        // 创建Bing图层
        this.bingLayer = new BingTileLayer(bingUrlTemplate, {
            attribution: '&copy; Microsoft Corporation',
            crossOrigin: 'anonymous',
            subdomains: ['t0', 't1', 't2', 't3']
        });

        // 添加Bing图层到地图
        this.bingLayer.addTo(this.map);

        // 确保地图视图设置为全球范围
        this.map.setView([0, 0], 1);

        // 初始化绘制控件
        this.initDrawControl();
    }

    /**
     * 初始化绘制控件
     */
    initDrawControl() {
        const drawControl = new L.Control.Draw({
            draw: {
                polygon: {
                    allowIntersection: false,
                    showArea: true,
                    drawError: {
                        color: '#e1e100',
                        message: '不能相交！'
                    },
                    shapeOptions: {
                        color: '#97009c'
                    }
                },
                rectangle: {
                    shapeOptions: {
                        color: '#0000ff'
                    }
                },
                circle: false,
                marker: false,
                polyline: false
            },
            edit: {
                featureGroup: this.drawnItems,
                remove: true
            }
        });

        this.map.addControl(drawControl);
    }

    /**
     * 绑定地图事件
     * @param {Function} onCreated - 绘制完成回调
     * @param {Function} onEdited - 编辑完成回调
     * @param {Function} onDeleted - 删除回调
     */
    bindMapEvents(onCreated, onEdited, onDeleted) {
        // 绘制完成事件
        this.map.on(L.Draw.Event.CREATED, (e) => {
            const layer = e.layer;
            this.drawnItems.addLayer(layer);

            // 获取边界
            const bounds = layer.getBounds();
            this.currentBbox = {
                north: bounds.getNorth(),
                south: bounds.getSouth(),
                west: bounds.getWest(),
                east: bounds.getEast()
            };

            if (onCreated) {
                onCreated(this.currentBbox);
            }
        });

        // 编辑完成事件
        this.map.on(L.Draw.Event.EDITED, (e) => {
            const layers = e.layers;
            layers.eachLayer((layer) => {
                const bounds = layer.getBounds();
                this.currentBbox = {
                    north: bounds.getNorth(),
                    south: bounds.getSouth(),
                    west: bounds.getWest(),
                    east: bounds.getEast()
                };

                if (onEdited) {
                    onEdited(this.currentBbox);
                }
            });
        });

        // 删除事件
        this.map.on(L.Draw.Event.DELETED, () => {
            this.currentBbox = null;
            if (onDeleted) {
                onDeleted();
            }
        });
    }

    /**
     * 清除绘制
     */
    clearDrawings() {
        this.drawnItems.clearLayers();
        this.currentBbox = null;
    }

    /**
     * 适配视图到边界
     * @param {Object} bbox - 边界对象
     */
    fitBounds(bbox) {
        if (bbox) {
            this.map.fitBounds([
                [bbox.south, bbox.west],
                [bbox.north, bbox.east]
            ]);
        }
    }

    /**
     * 添加矩形边界
     * @param {Object} bbox - 边界对象
     */
    addRectangle(bbox) {
        // 清除现有绘制
        this.drawnItems.clearLayers();

        // 添加矩形
        const rectangle = L.rectangle([
            [bbox.south, bbox.west],
            [bbox.north, bbox.east]
        ], {
            color: '#0000ff',
            weight: 2
        });
        this.drawnItems.addLayer(rectangle);

        // 适配视图
        this.fitBounds(bbox);
    }

    /**
     * 激活矩形绘制
     */
    activateRectangleDraw() {
        new L.Draw.Rectangle(this.map, {
            shapeOptions: {
                color: '#0000ff'
            }
        }).enable();
    }

    /**
     * 获取当前边界
     * @returns {Object} 当前边界
     */
    getCurrentBbox() {
        return this.currentBbox;
    }

    /**
     * 设置当前边界
     * @param {Object} bbox - 边界对象
     */
    setCurrentBbox(bbox) {
        this.currentBbox = bbox;
    }
}

export default MapModule;

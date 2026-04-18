/**
 * 配置管理模块
 */

class ConfigModule {
    constructor() {
        this.configList = [];
    }

    /**
     * 加载配置列表
     * @returns {Promise<Array>} 配置列表
     */
    async loadConfigList() {
        try {
            const response = await fetch('/api/config/list');
            const data = await response.json();
            
            if (data.success) {
                this.configList = data.configs;
                return this.configList;
            } else {
                console.error('加载配置列表失败:', data.error);
                return [];
            }
        } catch (error) {
            console.error('加载配置列表失败:', error);
            return [];
        }
    }

    /**
     * 保存配置
     * @param {string} configName - 配置名称
     * @param {Object} configData - 配置数据
     * @returns {Promise<boolean>} 是否保存成功
     */
    async saveConfig(configName, configData) {
        try {
            const response = await fetch('/api/config/save', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    config_name: configName,
                    config_data: configData
                })
            });
            
            const result = await response.json();
            return result.success;
        } catch (error) {
            console.error('保存配置失败:', error);
            return false;
        }
    }

    /**
     * 加载配置
     * @param {string} configName - 配置名称
     * @returns {Promise<Object>} 配置数据
     */
    async loadConfig(configName) {
        try {
            const response = await fetch(`/api/config/load/${configName}`);
            const result = await response.json();
            
            if (result.success) {
                return result.config.data;
            } else {
                console.error('加载配置失败:', result.error);
                return null;
            }
        } catch (error) {
            console.error('加载配置失败:', error);
            return null;
        }
    }

    /**
     * 获取表单数据
     * @returns {Object} 表单数据
     */
    getFormData() {
        return {
            provider_url: document.getElementById('providerUrl').value,
            output_path: document.getElementById('outputPath').value,
            save_format: document.getElementById('saveFormat').value,
            subdomains: document.getElementById('subdomains').value,
            min_zoom: document.getElementById('minZoom').value,
            max_zoom: document.getElementById('maxZoom').value,
            threads: document.getElementById('threads').value,
            tile_format: document.getElementById('tileFormat').value,
            tms: document.getElementById('tms').checked,
            north: document.getElementById('manualNorth').value,
            south: document.getElementById('manualSouth').value,
            west: document.getElementById('manualWest').value,
            east: document.getElementById('manualEast').value
        };
    }

    /**
     * 填充表单
     * @param {Object} config - 配置数据
     */
    fillForm(config) {
        if (config.provider_url) {
            document.getElementById('providerUrl').value = config.provider_url;
        }
        if (config.output_path) {
            document.getElementById('outputPath').value = config.output_path;
        }
        if (config.save_format) {
            document.getElementById('saveFormat').value = config.save_format;
        }
        if (config.subdomains) {
            document.getElementById('subdomains').value = config.subdomains;
        }
        if (config.min_zoom) {
            document.getElementById('minZoom').value = config.min_zoom;
        }
        if (config.max_zoom) {
            document.getElementById('maxZoom').value = config.max_zoom;
        }
        if (config.threads) {
            document.getElementById('threads').value = config.threads;
        }
        if (config.tile_format) {
            document.getElementById('tileFormat').value = config.tile_format;
        }
        if (config.tms !== undefined) {
            document.getElementById('tms').checked = config.tms;
        }
        if (config.north) {
            document.getElementById('manualNorth').value = config.north;
        }
        if (config.south) {
            document.getElementById('manualSouth').value = config.south;
        }
        if (config.west) {
            document.getElementById('manualWest').value = config.west;
        }
        if (config.east) {
            document.getElementById('manualEast').value = config.east;
        }
    }
}

export default ConfigModule;

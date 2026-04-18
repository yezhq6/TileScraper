/**
 * Bing 地图图层模块
 */

// BingTileLayer 类定义
export const BingTileLayer = L.TileLayer.extend({
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

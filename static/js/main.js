/**
 * 应用入口点
 */

import CoreModule from './modules/core.js';

// 页面加载完成后初始化应用
document.addEventListener('DOMContentLoaded', () => {
    const app = new CoreModule();
    app.init();
});

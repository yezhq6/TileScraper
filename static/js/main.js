/**
 * 应用入口点
 *
 * 这里还装了一个全局 fetch 拦截器，用于可选的 API 访问令牌
 * （config.yaml 的 server.api_token）：
 *   - 本地存了 token 就自动带上 `X-API-Token` 请求头；
 *   - 服务端返回 401 时弹框让用户输入一次，存到 localStorage 后重试。
 * 只拦截 fetch，不影响静态资源；SSE（EventSource）无法带自定义头，
 * 因此启用令牌时 core.js 会改用轮询。
 */

import CoreModule from './modules/core.js';

const TOKEN_STORAGE_KEY = 'tilescraper_api_token';

function getStoredToken() {
    try {
        return (localStorage.getItem(TOKEN_STORAGE_KEY) || '').trim();
    } catch (error) {
        return '';
    }
}

function storeToken(token) {
    try {
        localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } catch (error) {
        console.warn('无法保存访问令牌:', error);
    }
}

function installApiTokenInterceptor() {
    const originalFetch = window.fetch.bind(window);

    window.fetch = async (input, init = {}) => {
        const options = { ...init };
        const token = getStoredToken();
        if (token) {
            options.headers = { ...(options.headers || {}), 'X-API-Token': token };
        }

        let response = await originalFetch(input, options);
        if (response.status !== 401) {
            return response;
        }

        // 未授权（或令牌不对）：让用户输入一次再重试
        const entered = (window.prompt(
            '该服务启用了访问令牌，请输入 config.yaml 里的 server.api_token：',
            ''
        ) || '').trim();
        if (!entered) {
            return response;
        }
        storeToken(entered);
        options.headers = { ...(options.headers || {}), 'X-API-Token': entered };
        response = await originalFetch(input, options);
        if (response.status !== 401) {
            // 令牌生效：刷新页面，让初始状态（是否有任务在跑）重新拉取
            window.location.reload();
        }
        return response;
    };
}

installApiTokenInterceptor();

// 页面加载完成后初始化应用
document.addEventListener('DOMContentLoaded', () => {
    const app = new CoreModule();
    app.init();
});

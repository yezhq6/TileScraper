"""应用层改进相关的测试：健康检查、provider 信息、配置删除、生产服务器探测。"""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ApiMiscTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = app.test_client()

    def test_health(self):
        resp = self.client.get('/api/health')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['status'], 'ok')
        self.assertIn('downloading', body)

    def test_providers_expose_template(self):
        resp = self.client.get('/api/providers')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body['success'])
        names = {p['name'] for p in body['providers']}
        self.assertIn('bing', names)
        self.assertNotIn('osm', names)
        for provider in body['providers']:
            self.assertIn('url_template', provider)
            self.assertIn('subdomains', provider)

    def test_config_save_list_load_delete_roundtrip(self):
        from src.config import config_manager

        with tempfile.TemporaryDirectory() as tmp:
            original = config_manager.config.get('paths', {}).get('config_dir')
            config_manager.config.setdefault('paths', {})['config_dir'] = tmp
            try:
                name = "unit_test_config"
                payload = {
                    "config_name": name,
                    "config_data": {"output_path": "abc", "threads": "8"},
                }
                save = self.client.post('/api/config/save', json=payload)
                self.assertTrue(save.get_json()['success'])

                listing = self.client.get('/api/config/list').get_json()
                self.assertIn(name, listing['configs'])

                loaded = self.client.get(f'/api/config/load/{name}').get_json()
                self.assertTrue(loaded['success'])
                self.assertEqual(loaded['config']['data']['output_path'], 'abc')

                deleted = self.client.post(
                    f'/api/config/delete/{name}', json={}
                ).get_json()
                self.assertTrue(deleted['success'])

                listing2 = self.client.get('/api/config/list').get_json()
                self.assertNotIn(name, listing2['configs'])
            finally:
                if original is not None:
                    config_manager.config['paths']['config_dir'] = original

    def test_config_delete_rejects_path_traversal(self):
        from src.config import config_manager
        self.assertFalse(config_manager.delete_config("../etc/passwd"))
        self.assertFalse(config_manager.delete_config(""))

    def test_config_name_validation(self):
        from src.config import config_manager
        for good in ("unit_test", "my-config", "cfg.v2"):
            self.assertTrue(config_manager.is_valid_config_name(good), good)
        for bad in (None, "", "..", ".", "../etc/passwd", "a/b", "a\\b", "x\x00y"):
            self.assertFalse(config_manager.is_valid_config_name(bad), repr(bad))

    def test_config_save_rejects_path_traversal(self):
        """回归：/api/config/save 曾可写 configs/ 之外的任意路径。"""
        from src.config import config_manager

        with tempfile.TemporaryDirectory() as tmp:
            original = config_manager.config.get('paths', {}).get('config_dir')
            config_manager.config.setdefault('paths', {})['config_dir'] = tmp
            try:
                resp = self.client.post(
                    '/api/config/save',
                    json={"config_name": "../pwned", "config_data": {"x": 1}},
                )
                self.assertEqual(resp.status_code, 400)
                self.assertFalse(os.path.exists(os.path.join(tmp, "..", "pwned.yaml")))
                # 直接调用底层 API 也应被拒绝
                self.assertFalse(config_manager.save_config("../pwned2", {"x": 1}))
                self.assertEqual(config_manager.load_config("../pwned2"), {})
            finally:
                if original is not None:
                    config_manager.config['paths']['config_dir'] = original

    def test_download_rejects_bad_zoom_and_coords(self):
        """缩放级别越界 / 非有限坐标应当是 400，而不是 500 或"成功但 0 瓦片"。"""
        base = {
            "provider_url": "http://tiles.local/{z}/{x}/{y}.png",
            "output_dir": os.path.join(tempfile.mkdtemp(), "tiles"),
            "west": 116.3, "east": 116.5, "south": 39.8, "north": 40.0,
            "min_zoom": 8, "max_zoom": 8,
        }
        for override in (
            {"min_zoom": -1, "max_zoom": 8},
            {"min_zoom": 8, "max_zoom": 99},
            {"min_zoom": float("inf"), "max_zoom": 8},
            {"west": float("inf")},
            {"east": 999},
        ):
            payload = {**base, **override}
            resp = self.client.post('/api/download', json=payload)
            self.assertEqual(resp.status_code, 400, f"{override} → {resp.status_code}")

    def test_download_rejects_invalid_threads(self):
        """线程数只接受正整数或 auto，非法值应在启动前被拒绝。"""
        payload = {
            "provider_url": "http://tiles.local/{z}/{x}/{y}.png",
            "output_dir": os.path.join(tempfile.mkdtemp(), "tiles"),
            "west": 116.3, "east": 116.5, "south": 39.8, "north": 40.0,
            "min_zoom": 8, "max_zoom": 8, "threads": "abc",
        }
        resp = self.client.post('/api/download', json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('线程数', resp.get_json()['error'])

    def test_index_page_ui_expectations(self):
        """首页不再有"地图源"下拉；缩放级别标注最低/最高层级；无代理输入框。"""
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertNotIn('地图源', html)
        self.assertNotIn('providerSelect', html)
        self.assertIn('最低层级', html)
        self.assertIn('最高层级', html)
        self.assertIn('id="providerUrl"', html)

    def test_proxy_is_config_only_not_in_ui(self):
        """代理改为 config.yaml 专属：页面只读展示，不再有可编辑输入框。"""
        resp = self.client.get('/')
        html = resp.get_data(as_text=True)
        self.assertNotIn('id="proxy"', html, "页面上不应再有代理输入框")
        self.assertIn('网络代理', html)
        self.assertIn('download.proxy', html)
        self.assertIn('直连', html)

    def test_download_payload_cannot_override_proxy(self):
        """请求里的 proxy 字段必须被忽略（防止用 HTTP 端口改写服务端代理）。"""
        from src.downloader.controller import DownloadController

        normalized = DownloadController._normalize_params({
            "provider_url": "http://tiles.local/{z}/{x}/{y}.png",
            "output_dir": "/tmp/whatever",
            "west": 1.0, "east": 2.0, "south": 1.0, "north": 2.0,
            "min_zoom": 1, "max_zoom": 2,
            "proxy": "http://attacker.invalid:8080",
        })
        self.assertNotIn('proxy', normalized)

    def test_describe_proxy_masks_credentials(self):
        from src.downloader.request import describe_proxy

        self.assertIn('直连', describe_proxy(""))
        self.assertIn('环境变量', describe_proxy("env"))
        described = describe_proxy("http://user:secret@proxy.local:7890")
        self.assertIn('proxy.local', described)
        self.assertNotIn('secret', described)
        self.assertNotIn('user', described)


class SubdomainValidationTests(unittest.TestCase):
    """回归：子域名带空格/空项会拼出坏主机名，导致大部分瓦片直接失败。"""

    def _normalize(self, **overrides):
        from src.downloader.controller import DownloadController

        payload = {
            "provider_url": "http://ecn.{s}.tiles.local/{z}/{x}/{y}.png",
            "output_dir": "/tmp/whatever",
            "west": 1.0, "east": 2.0, "south": 1.0, "north": 2.0,
            "min_zoom": 1, "max_zoom": 2,
            "subdomains": "t0, t1 ,,t2",
        }
        payload.update(overrides)
        return DownloadController._normalize_params(payload)

    def test_subdomains_are_trimmed_and_deduped(self):
        normalized = self._normalize()
        self.assertEqual(normalized['subdomains'], ["t0", "t1", "t2"])

    def test_subdomains_array_from_frontend_is_cleaned(self):
        normalized = self._normalize(subdomains=["t0", " t1", "", "t0", "t2 "])
        self.assertEqual(normalized['subdomains'], ["t0", "t1", "t2"])

    def test_template_with_s_requires_subdomains(self):
        with self.assertRaises(ValueError) as ctx:
            self._normalize(subdomains=[])
        self.assertIn("子域名", str(ctx.exception))

    def test_template_without_placeholders_is_rejected(self):
        with self.assertRaises(ValueError):
            self._normalize(provider_url="http://tiles.local/fixed.png")


class CsrfProtectionTests(unittest.TestCase):
    """CSRF：状态变更接口要求同源 + JSON 内容类型（跨站表单只能发 urlencoded）。"""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = app.test_client()

    def test_bodyless_post_without_json_is_rejected(self):
        """浏览器跨站表单无法设置 JSON 内容类型 → 415。"""
        for path in ('/api/pause-download', '/api/resume-download',
                     '/api/cancel-download'):
            resp = self.client.post(path, data='')
            self.assertEqual(resp.status_code, 415, path)
            self.assertFalse(resp.get_json()['success'])

    def test_cross_site_origin_is_rejected(self):
        resp = self.client.post(
            '/api/cancel-download', json={},
            headers={'Origin': 'http://evil.example'},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn('跨站', resp.get_json()['error'])

    def test_cross_site_fetch_metadata_is_rejected(self):
        resp = self.client.post(
            '/api/pause-download', json={},
            headers={'Sec-Fetch-Site': 'cross-site'},
        )
        self.assertEqual(resp.status_code, 403)

    def test_same_origin_and_script_clients_are_allowed(self):
        # 同源（浏览器带 Sec-Fetch-Site: same-origin）
        resp = self.client.post(
            '/api/pause-download', json={},
            headers={'Sec-Fetch-Site': 'same-origin'},
        )
        self.assertEqual(resp.status_code, 200)
        # 脚本/curl：既没有 Origin 也没有 Sec-Fetch-Site，且有 JSON 头
        resp = self.client.post('/api/pause-download', json={})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['success'])  # 没有任务在跑

    def test_trusted_origin_bypasses_host_check(self):
        from src.config import config_manager

        with mock.patch.dict(
            config_manager.config['server'], {'trusted_origins': ['tiles.example.com']}
        ):
            resp = self.client.post(
                '/api/resume-download', json={},
                headers={'Origin': 'https://tiles.example.com'},
            )
        self.assertEqual(resp.status_code, 200)

    def test_form_encoded_download_is_rejected(self):
        """跨站表单提交 /api/download 只能是 urlencoded → 415（而不是静默失败）。"""
        resp = self.client.post(
            '/api/download',
            data='west=1&east=2&south=1&north=2&min_zoom=1&max_zoom=2&output_dir=/tmp/x',
            content_type='application/x-www-form-urlencoded',
        )
        self.assertEqual(resp.status_code, 415)

    def test_config_delete_requires_json_for_post(self):
        # DELETE 无法由表单发起，允许不带 JSON（方便 curl）
        resp = self.client.delete('/api/config/delete/whatever')
        self.assertIn(resp.status_code, (200, 404))
        # POST 需要 JSON
        resp = self.client.post('/api/config/delete/whatever', data='')
        self.assertEqual(resp.status_code, 415)
        resp = self.client.post('/api/config/delete/whatever', json={})
        self.assertIn(resp.status_code, (200, 404))


class ApiTokenTests(unittest.TestCase):
    """可选访问令牌（server.api_token）：默认关闭，开启后所有 /api/* 需要令牌。"""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = app.test_client()

    def setUp(self):
        from src.config import config_manager

        self.config = config_manager.config.setdefault('server', {})
        self._original = self.config.get('api_token')
        self.addCleanup(self._restore)

    def _restore(self):
        if self._original is None:
            self.config.pop('api_token', None)
        else:
            self.config['api_token'] = self._original

    def _set_token(self, value):
        self.config['api_token'] = value

    def test_disabled_by_default_allows_requests(self):
        self._set_token('')
        self.assertEqual(self.client.get('/api/download-status').status_code, 200)

    def test_missing_or_wrong_token_is_401(self):
        self._set_token('s3cret-token')
        endpoints = [
            ('get', '/api/download-status'),
            ('get', '/api/failed-tiles'),
            ('get', '/api/providers'),
            ('post', '/api/pause-download'),
        ]
        for method, path in endpoints:
            kwargs = {'json': {}} if method == 'post' else {}
            response = getattr(self.client, method)(path, **kwargs)
            self.assertEqual(response.status_code, 401, f"{method} {path}")
            self.assertFalse(response.get_json()['success'])
        wrong = self.client.get(
            '/api/download-status', headers={'X-API-Token': 'nope'}
        )
        self.assertEqual(wrong.status_code, 401)

    def test_correct_token_via_header_and_bearer(self):
        self._set_token('s3cret-token')
        by_header = self.client.get(
            '/api/download-status', headers={'X-API-Token': 's3cret-token'}
        )
        self.assertEqual(by_header.status_code, 200)
        by_bearer = self.client.get(
            '/api/download-status',
            headers={'Authorization': 'Bearer s3cret-token'},
        )
        self.assertEqual(by_bearer.status_code, 200)

    def test_health_and_index_stay_open(self):
        self._set_token('s3cret-token')
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_page_only_reveals_that_auth_is_required(self):
        self._set_token('s3cret-token')
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('__API_AUTH_REQUIRED__ = true', html)
        self.assertNotIn('s3cret-token', html, "令牌绝不能出现在页面里")

    def test_page_flag_is_false_without_token(self):
        self._set_token('')
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('__API_AUTH_REQUIRED__ = false', html)


class ServerErrorSanitizationTests(unittest.TestCase):
    """回归：500 分支曾把异常原文回给客户端（可能含路径/SQL 细节）。"""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = app.test_client()

    def test_internal_error_is_not_echoed(self):
        from src.routes import main as routes_main

        secret = RuntimeError("boom /home/user/secret/progress.db is corrupt")
        payload = {
            "provider_url": "http://tiles.local/{z}/{x}/{y}.png",
            "output_dir": "/tmp/whatever",
            "west": 1.0, "east": 2.0, "south": 1.0, "north": 2.0,
            "min_zoom": 1, "max_zoom": 2,
        }
        with mock.patch.object(routes_main.controller, 'start', side_effect=secret):
            resp = self.client.post('/api/download', json=payload)

        self.assertEqual(resp.status_code, 500)
        body = resp.get_data(as_text=True)
        self.assertNotIn('boom', body)
        self.assertNotIn('secret', body)
        self.assertIn('服务器内部错误', resp.get_json()['error'])

    def test_actionable_messages_are_still_returned(self):
        """我们自己的、可操作的错误（400/409）仍要原样返回给用户。"""
        resp = self.client.post('/api/download', json={"north": 1})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('缺少必需参数', resp.get_json()['error'])


class BingQuadkeyTests(unittest.TestCase):
    def test_zoom_zero_quadkey_is_zero(self):
        from src.providers.bing import BingTileProvider

        self.assertEqual(BingTileProvider.tile_to_quadkey(0, 0, 0), "0")

    def test_normal_quadkeys(self):
        from src.providers.bing import BingTileProvider

        self.assertEqual(BingTileProvider.tile_to_quadkey(0, 0, 1), "0")
        self.assertEqual(BingTileProvider.tile_to_quadkey(1, 0, 1), "1")
        self.assertEqual(BingTileProvider.tile_to_quadkey(0, 1, 1), "2")
        self.assertEqual(BingTileProvider.tile_to_quadkey(1, 1, 1), "3")
        self.assertEqual(BingTileProvider.tile_to_quadkey(3, 5, 3), "213")


class ProviderManagerLockTests(unittest.TestCase):
    """读方法加锁后仍能正常工作（并发注册时不再遍历到一半）。"""

    def test_read_helpers_work(self):
        from src.providers import ProviderManager

        self.assertIn('bing', ProviderManager.list_providers())
        self.assertTrue(ProviderManager.provider_exists('bing'))
        self.assertFalse(ProviderManager.provider_exists('definitely_missing'))
        info = ProviderManager.get_provider_info('bing')
        self.assertEqual(info['name'], 'bing')
        self.assertIsNone(ProviderManager.get_provider_info('definitely_missing'))
        self.assertTrue(any(p['name'] == 'bing'
                            for p in ProviderManager.get_all_providers_info()))


class ConnectionPoolTests(unittest.TestCase):
    def test_same_key_different_path_rebuilds_connection(self):
        import tempfile
        from pathlib import Path

        from src.downloader.connection_pool import ConnectionPool

        pool = ConnectionPool()
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.mbtiles"
            second = Path(tmp) / "b.mbtiles"
            conn_a = pool.get_connection(("t", 1), first)
            self.assertTrue(first.exists())
            # 同一个 key 换库：必须新建连接，而不是把 a 的连接当成 b 的
            conn_b = pool.get_connection(("t", 1), second)
            self.assertIsNot(conn_a, conn_b)
            self.assertTrue(second.exists())
            self.assertEqual(pool.paths[("t", 1)], second)
            pool.close_all_connections()
            self.assertEqual(pool.connections, {})
            self.assertEqual(pool.paths, {})


class FrontendAssetsTests(unittest.TestCase):
    """回归：前端库必须本地内置（不依赖 CDN），且模板引用的静态资源都能取到。"""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = app.test_client()

    def test_index_has_no_external_assets(self):
        html = self.client.get('/').get_data(as_text=True)
        external = [
            url for url in re.findall(r'(?:src|href)="([^"]+)"', html)
            if url.startswith(('http://', 'https://', '//'))
        ]
        self.assertEqual(
            external, [],
            f"页面不应引用外部资源（CDN 被投毒等于同源脚本，且离线不可用）: {external}",
        )

    def test_all_referenced_static_assets_are_served(self):
        html = self.client.get('/').get_data(as_text=True)
        urls = [
            url for url in re.findall(r'(?:src|href)="([^"]+)"', html)
            if url.startswith('/static/')
        ]
        self.assertTrue(urls, "模板里应该引用内置的静态资源")
        for url in urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f"{url} 无法访问")
            self.assertGreater(len(resp.get_data()), 0, f"{url} 是空文件")

    def test_vendored_libraries_and_css_images_exist(self):
        needed = [
            "vendor/bootstrap/bootstrap.min.css",
            "vendor/leaflet/leaflet.css",
            "vendor/leaflet/leaflet.js",
            "vendor/leaflet/images/layers.png",
            "vendor/leaflet/images/layers-2x.png",
            "vendor/leaflet/images/marker-icon.png",
            "vendor/leaflet/images/marker-icon-2x.png",
            "vendor/leaflet/images/marker-shadow.png",
            "vendor/leaflet-draw/leaflet.draw.css",
            "vendor/leaflet-draw/leaflet.draw.js",
            "vendor/leaflet-draw/images/spritesheet.png",
            "vendor/leaflet-draw/images/spritesheet-2x.png",
            "vendor/leaflet-draw/images/spritesheet.svg",
        ]
        missing = [rel for rel in needed if not (Path("static") / rel).exists()]
        self.assertEqual(missing, [], f"缺失内置资源（CSS 里的图片也要一起内置）: {missing}")

    def test_css_image_references_resolve_locally(self):
        """CSS 里的 url(...) 必须指向本目录下真实存在的文件（否则图标 404）。"""
        bad = []
        for css in (
            Path("static/vendor/leaflet/leaflet.css"),
            Path("static/vendor/leaflet-draw/leaflet.draw.css"),
        ):
            text = css.read_text(encoding="utf-8", errors="replace")
            for ref in re.findall(r'url\(\s*[\'"]?([^\'")]+)[\'"]?\s*\)', text):
                if ref.startswith(("data:", "http:", "https:", "//", "#")):
                    continue
                if not (css.parent / ref).exists():
                    bad.append(f"{css} -> {ref}")
        self.assertEqual(bad, [], f"CSS 引用了不存在的资源: {bad}")


class ProductionServerTests(unittest.TestCase):
    def test_get_production_server_is_safe(self):
        from app import get_production_server

        # 未安装 waitress 时返回 None，安装后返回可调用对象；两者都不应抛异常
        server = get_production_server()
        self.assertTrue(server is None or callable(server))


if __name__ == "__main__":
    unittest.main()

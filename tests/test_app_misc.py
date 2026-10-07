"""应用层改进相关的测试：健康检查、provider 信息、配置删除、生产服务器探测。"""

import os
import tempfile
import unittest


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
        self.assertIn('osm', names)
        self.assertIn('bing', names)
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

                deleted = self.client.post(f'/api/config/delete/{name}').get_json()
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


class ProductionServerTests(unittest.TestCase):
    def test_get_production_server_is_safe(self):
        from app import get_production_server

        # 未安装 waitress 时返回 None，安装后返回可调用对象；两者都不应抛异常
        server = get_production_server()
        self.assertTrue(server is None or callable(server))


if __name__ == "__main__":
    unittest.main()

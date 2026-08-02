import datetime
import json
import threading
import unittest
import urllib.error
import urllib.request

import app

from tests.helpers import ProjectFixture


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.fx = ProjectFixture()
        company = {
            "t": "NVDA",
            "name": "NVIDIA",
            "country": "美国",
            "city": "Santa Clara",
            "lat": 37.35,
            "lon": -121.95,
            "seg": "GPU",
            "source": "base",
        }
        self.fx.write_json("watchlist.json", {"companies": [company]})
        self.fx.write_json(
            "user_watchlist.json",
            {"version": 1, "replace_base": False, "add": {}, "remove": []},
        )
        self.fx.write_json(
            "active_universe.json",
            {"companies": [company], "counts": {"active": 1, "base": 1}},
        )
        self.fx.write_json(
            "live.json",
            {"asof": "2026-07-11 09:00", "items": {"NVDA": {"t": "NVDA", "p": 160, "chg": 1, "source": "yahoo_chart"}}},
        )
        self.fx.write_json(
            "live_status.json",
            {"status": "success", "last_success": "2026-07-11 09:00", "fresh_count": 1, "expected_count": 1},
        )
        self.fx.write_json(
            "semi_market.json",
            {
                "companies": [{"t": "NVDA"}],
                "dates": [datetime.date.today().isoformat()],
                "meta": {"fetched": datetime.date.today().isoformat()},
            },
        )
        (self.fx.data / "admin_token.txt").write_text("test-token\n", encoding="utf-8")
        (self.fx.base / "index.html").write_text("dashboard", encoding="utf-8")

        self.old_globals = (app.BASE, app.DATA, app.ADMIN_TOKEN_PATH)
        app.BASE = self.fx.base
        app.DATA = self.fx.data
        app.ADMIN_TOKEN_PATH = self.fx.data / "admin_token.txt"
        app.reset_admin_runtime()
        self.server = app.FastThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        app.BASE, app.DATA, app.ADMIN_TOKEN_PATH = self.old_globals
        app.reset_admin_runtime()
        self.fx.close()

    def request(self, method, path, body=None, token=None, content_type="application/json"):
        if body is None:
            raw = None
        elif isinstance(body, bytes):
            raw = body
        else:
            raw = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": content_type}
        if token:
            headers["X-AICM-Admin-Token"] = token
        request = urllib.request.Request(
            self.base_url + path,
            data=raw,
            headers=headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
            return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def request_json(self, method, path, body=None, token=None):
        status, headers, raw = self.request(method, path, body, token)
        return status, headers, json.loads(raw)

    def test_admin_overview_rejects_missing_token(self):
        status, _, payload = self.request_json("GET", "/api/admin/overview")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "admin_token_required")

    def test_verify_accepts_token_header(self):
        status, _, payload = self.request_json(
            "POST",
            "/api/admin/auth/verify",
            {},
            token="test-token",
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["authenticated"], True)

    def test_overview_returns_separate_quote_and_history_states(self):
        status, _, payload = self.request_json(
            "GET",
            "/api/admin/overview",
            token="test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["quotes"]["status"], "healthy")
        self.assertEqual(payload["data"]["history"]["status"], "healthy")

    def test_import_preview_accepts_raw_binary_and_filename(self):
        status, _, payload = self.request_json(
            "POST",
            "/api/admin/import/preview?filename=companies.csv",
            b"ticker,name\nNVDA,NVIDIA\n",
            token="test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["summary"]["invalid"], 0)

    def test_template_download_has_excel_headers(self):
        status, headers, raw = self.request(
            "GET",
            "/api/admin/templates/companies.xlsx?variant=blank",
            token="test-token",
        )
        self.assertEqual(status, 200)
        self.assertIn("spreadsheetml", headers.get("Content-Type"))
        self.assertIn("attachment", headers.get("Content-Disposition"))
        self.assertGreater(len(raw), 1000)

    def test_existing_health_endpoint_keeps_legacy_shape(self):
        status, _, payload = self.request_json("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["ok"], True)
        self.assertIn("watchlist_count", payload)
        self.assertNotIn("meta", payload)

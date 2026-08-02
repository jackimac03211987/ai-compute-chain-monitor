import io
import unittest
from types import SimpleNamespace

from tests.helpers import ProjectFixture


class AdminCommonTests(unittest.TestCase):
    def setUp(self):
        self.fx = ProjectFixture()
        (self.fx.base / "index.html").write_text("ok", encoding="utf-8")
        (self.fx.base / "admin").mkdir()
        (self.fx.base / "admin" / "index.html").write_text("admin", encoding="utf-8")

    def tearDown(self):
        self.fx.close()

    def test_safe_static_path_maps_admin_directory_to_index(self):
        from admin_common import safe_static_path

        self.assertEqual(
            safe_static_path(self.fx.base, "/admin/"),
            (self.fx.base / "admin" / "index.html").resolve(),
        )

    def test_safe_static_path_rejects_escape(self):
        from admin_common import ApiError, safe_static_path

        with self.assertRaises(ApiError) as raised:
            safe_static_path(self.fx.base, "/../secret")
        self.assertEqual(raised.exception.status, 404)

    def test_read_limited_body_rejects_oversize(self):
        from admin_common import ApiError, read_limited_body

        handler = SimpleNamespace(
            headers={"Content-Length": "5"},
            rfile=io.BytesIO(b"12345"),
        )
        with self.assertRaises(ApiError) as raised:
            read_limited_body(handler, 4)
        self.assertEqual(raised.exception.status, 413)

    def test_api_envelope_contains_stable_shape(self):
        from admin_common import api_envelope

        payload = api_envelope(data={"value": 1}, request_id="req-1")
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["data"], {"value": 1})
        self.assertEqual(payload["error"], None)
        self.assertEqual(payload["meta"]["request_id"], "req-1")

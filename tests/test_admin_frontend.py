import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]


class AdminFrontendTests(unittest.TestCase):
    def test_admin_page_has_required_regions_and_no_webgl(self):
        html = (BASE / "admin" / "index.html").read_text(encoding="utf-8")
        for value in (
            "admin-auth",
            "admin-shell",
            "admin-nav",
            "admin-main",
            "return-dashboard",
        ):
            self.assertIn(value, html)
        self.assertNotIn("three.min.js", html)
        self.assertNotIn("globe.gl", html)

    def test_admin_assets_are_local(self):
        html = (BASE / "admin" / "index.html").read_text(encoding="utf-8")
        self.assertIn("../vendor/lucide.min.js", html)
        self.assertIn("admin.css", html)
        self.assertIn("admin.js", html)
        self.assertNotIn("https://", html)

    def test_frontend_references_all_admin_modules(self):
        javascript = (BASE / "admin" / "admin.js").read_text(encoding="utf-8")
        for path in (
            "/api/admin/overview",
            "/api/admin/interfaces",
            "/api/admin/companies",
            "/api/admin/import/preview",
            "/api/admin/export",
            "/api/admin/jobs",
            "/api/admin/audit",
        ):
            self.assertIn(path, javascript)

    def test_admin_css_has_mobile_layout_and_no_decorative_gradients(self):
        css = (BASE / "admin" / "admin.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 760px)", css)
        self.assertNotIn("gradient(", css)
        self.assertNotIn("letter-spacing: -", css)

    def test_main_page_links_to_admin_with_one_button(self):
        html = (BASE / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="adminbtn"'), 1)
        self.assertIn("navigateWithFlip('/admin/')", html)
        self.assertIn("prefers-reduced-motion", html)

    def test_admin_return_uses_reverse_flip(self):
        javascript = (BASE / "admin" / "admin.js").read_text(encoding="utf-8")
        self.assertIn("navigateWithFlip('/', 'to-dashboard')", javascript)
        self.assertIn("prepareFlipIn()", javascript)

    def test_admin_token_is_tab_scoped(self):
        javascript = (BASE / "admin" / "admin.js").read_text(encoding="utf-8")
        self.assertIn("sessionStorage.getItem('aicmAdminToken')", javascript)
        self.assertIn("sessionStorage.setItem('aicmAdminToken', token)", javascript)
        self.assertNotIn("localStorage.getItem('aicmAdminToken')", javascript)
        self.assertNotIn("localStorage.setItem('aicmAdminToken'", javascript)

    def test_main_dashboard_escapes_external_html_and_uses_event_delegation(self):
        html = (BASE / "index.html").read_text(encoding="utf-8")
        self.assertIn("function esc(value)", html)
        self.assertIn('data-pick-ticker="${esc(c.t)}"', html)
        self.assertIn('data-edit-ticker="${esc(c.t)}"', html)
        self.assertIn('data-remove-ticker="${esc(c.t)}"', html)
        self.assertNotIn("onclick='pick", html)
        self.assertNotIn("onclick='editWatchTicker", html)
        self.assertNotIn("onclick='removeWatchTicker", html)

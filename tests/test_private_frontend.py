import unittest
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]


class PrivateFrontendTests(unittest.TestCase):
    def test_admin_has_user_management_controls(self):
        html=(BASE/"admin"/"index.html").read_text()
        for value in ("users-nav","users-view","create-user","user-dialog","issued-token-dialog"):
            self.assertIn(f'id="{value}"',html)

    def test_private_workspace_has_token_gate_and_modules(self):
        html=(BASE/"private"/"index.html").read_text()
        for value in ("private-auth","private-shell","personal-watchlist","personal-interfaces"):
            self.assertIn(f'id="{value}"',html)
        javascript=(BASE/"private"/"private.js").read_text()
        self.assertIn("/api/v1/me/profile",javascript)
        self.assertIn("/api/v1/me/watchlist",javascript)
        self.assertNotIn("X-AICM-Admin-Token",javascript)

    def test_private_assets_are_local_and_responsive(self):
        html=(BASE/"private"/"index.html").read_text(); css=(BASE/"private"/"private.css").read_text()
        self.assertNotIn("https://",html); self.assertIn("@media",css)
        self.assertIn("prefers-reduced-motion",css)

    def test_watchlist_rows_can_be_removed(self):
        javascript=(BASE/"private"/"private.js").read_text()
        self.assertIn('data-remove-ticker',javascript)
        self.assertIn('/api/v1/me/watchlist/remove',javascript)

    def test_interface_dialog_has_required_fields_and_create_action(self):
        html=(BASE/"private"/"index.html").read_text()
        for value in (
            "interface-dialog",
            "interface-form",
            "interface-name",
            "interface-provider",
            "interface-mode",
            "interface-url",
            "interface-interval",
            "interface-timeout",
            "interface-auth-type",
            "interface-secret",
        ):
            self.assertIn(f'id="{value}"',html)
        javascript=(BASE/"private"/"private.js").read_text()
        self.assertIn("showModal()",javascript)
        self.assertIn("/api/v1/me/interfaces",javascript)
        self.assertIn("method:'POST'",javascript)
        self.assertIn("/credentials",javascript)

    def test_private_page_has_visible_error_region_without_secret_rendering(self):
        html=(BASE/"private"/"index.html").read_text()
        javascript=(BASE/"private"/"private.js").read_text()
        self.assertIn('id="workspace-notice"',html)
        self.assertNotIn("credential_value",javascript)
        self.assertNotIn("raw_token",javascript)

    def test_private_workspace_exposes_transfer_audit_and_support_modules(self):
        html=(BASE/"private"/"index.html").read_text()
        for value in (
            "personal-transfer",
            "personal-audit",
            "personal-support",
            "interface-import-file",
            "download-interface-template",
            "export-interfaces",
            "audit-rows",
            "support-grant-form",
            "revoke-support-grant",
            "effective-user-banner",
        ):
            self.assertIn(f'id="{value}"',html)
        javascript=(BASE/"private"/"private.js").read_text()
        for path in (
            "/api/v1/me/interfaces/template",
            "/api/v1/me/interfaces/import/preview",
            "/api/v1/me/interfaces/import/apply",
            "/api/v1/me/interfaces/export",
            "/api/v1/me/audit",
            "/api/v1/me/support-grants",
        ):
            self.assertIn(path,javascript)

    def test_interface_rows_offer_test_and_custom_delete_actions(self):
        javascript=(BASE/"private"/"private.js").read_text()
        self.assertIn("data-test-interface",javascript)
        self.assertIn("data-delete-interface",javascript)
        self.assertIn("method:'DELETE'",javascript)
        self.assertIn("/history",javascript)

    def test_support_grant_can_be_revoked_from_the_same_workspace(self):
        javascript=(BASE/"private"/"private.js").read_text()
        self.assertIn("/revoke",javascript)
        self.assertIn("currentSupportGrantId",javascript)

    def test_personal_token_is_tab_scoped_and_login_uses_submitted_value(self):
        javascript=(BASE/"private"/"private.js").read_text()
        self.assertIn("sessionStorage.getItem('aicmPersonalToken')",javascript)
        self.assertIn("sessionStorage.setItem('aicmPersonalToken', token)",javascript)
        self.assertIn("api.call('/api/v1/me/profile', {}, token)",javascript)
        self.assertNotIn("localStorage.getItem('aicmPersonalToken')",javascript)
        self.assertNotIn("localStorage.setItem('aicmPersonalToken'",javascript)

    def test_hidden_auth_and_workspace_regions_leave_the_layout(self):
        css=(BASE/"private"/"private.css").read_text()
        self.assertIn("[hidden]",css)
        self.assertIn("display: none !important",css)


if __name__=="__main__": unittest.main()

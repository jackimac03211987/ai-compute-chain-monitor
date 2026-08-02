import datetime
import tempfile
import unittest
from pathlib import Path


class AuthContextTests(unittest.TestCase):
    def setUp(self):
        from identity_store import IdentityStore

        self.tmp = tempfile.TemporaryDirectory()
        self.store = IdentityStore(Path(self.tmp.name))
        self.store.bootstrap_owner("owner-secret")
        self.owner = self.store.verify_token("owner-secret")

    def tearDown(self):
        self.tmp.cleanup()

    def create_auth(self, name, role, permissions=None):
        user = self.store.create_user(name, role, permissions)
        token = self.store.issue_token(user["user_id"])["token"]
        return user, self.store.verify_token(token)

    def test_role_templates_and_permission_overrides(self):
        from admin_common import ApiError
        from auth_context import require_permission

        _, viewer = self.create_auth("Viewer", "viewer")
        require_permission(viewer, "watchlist.read")
        with self.assertRaises(ApiError):
            require_permission(viewer, "watchlist.write")

        _, custom = self.create_auth(
            "Custom", "viewer", {"watchlist.write": True, "interfaces.read": False}
        )
        require_permission(custom, "watchlist.write")
        with self.assertRaises(ApiError):
            require_permission(custom, "interfaces.read")

    def test_support_grant_is_scoped_to_named_administrator(self):
        from auth_context import require_permission

        admin_user, admin = self.create_auth("Admin", "administrator")
        _, wrong_admin = self.create_auth("Wrong Admin", "administrator")
        user, user_auth = self.create_auth("User", "operator")
        expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).isoformat()
        grant = self.store.create_support_grant(
            user_auth, admin_user["user_id"], ["interfaces"], "Investigate failed probe", expires
        )
        support = self.store.resolve_support_context(admin, grant["grant_id"])
        self.assertEqual(support.user_id, user["user_id"])
        self.assertEqual(support.support_grant["grant_id"], grant["grant_id"])
        require_permission(support, "interfaces.read")
        require_permission(support, "interfaces.test")
        with self.assertRaises(Exception):
            require_permission(support, "watchlist.read")
        with self.assertRaises(Exception):
            self.store.resolve_support_context(wrong_admin, grant["grant_id"])

    def test_support_grant_rejects_long_duration_expiry_and_revocation(self):
        admin_user, admin = self.create_auth("Admin", "administrator")
        _, user_auth = self.create_auth("User", "operator")
        too_long = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=25)).isoformat()
        with self.assertRaises(ValueError):
            self.store.create_support_grant(user_auth, admin_user["user_id"], ["watchlist"], "Help", too_long)

        valid_until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
        grant = self.store.create_support_grant(
            user_auth, admin_user["user_id"], ["watchlist"], "Help", valid_until
        )
        self.assertTrue(self.store.revoke_support_grant(user_auth, grant["grant_id"]))
        with self.assertRaises(Exception):
            self.store.resolve_support_context(admin, grant["grant_id"])

    def test_support_access_is_audited(self):
        admin_user, admin = self.create_auth("Admin", "administrator")
        _, user_auth = self.create_auth("User", "operator")
        expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
        grant = self.store.create_support_grant(
            user_auth, admin_user["user_id"], ["audit"], "Review activity", expires
        )
        self.store.resolve_support_context(admin, grant["grant_id"])
        actions = [row["action"] for row in self.store.list_audit()]
        self.assertIn("support.grant", actions)
        self.assertIn("support.enter", actions)


if __name__ == "__main__":
    unittest.main()

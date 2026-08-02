import datetime
import tempfile
import unittest
from pathlib import Path


class IdentityStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_owner_bootstrap_is_idempotent_and_preserves_admin_token(self):
        from identity_store import IdentityStore

        store = IdentityStore(self.base)
        first = store.bootstrap_owner("existing-admin-secret")
        second = store.bootstrap_owner("existing-admin-secret")
        self.assertEqual(first["user_id"], second["user_id"])
        principal = store.verify_token("existing-admin-secret")
        self.assertEqual(principal.user_id, first["user_id"])
        self.assertEqual(principal.role, "owner")

    def test_personal_tokens_are_distinct_verified_and_never_stored_raw(self):
        from identity_store import IdentityStore

        store = IdentityStore(self.base)
        store.bootstrap_owner("owner-secret")
        user = store.create_user("Research User", "operator")
        first = store.issue_token(user["user_id"])
        second = store.issue_token(user["user_id"])
        self.assertNotEqual(first["token"], second["token"])
        self.assertTrue(first["token"].startswith("aicm_u_"))
        self.assertEqual(store.verify_token(first["token"]).user_id, user["user_id"])
        runtime = b"".join(
            path.read_bytes()
            for path in (self.base / "data" / "auth").glob("aicm_identity.db*")
            if path.is_file()
        )
        self.assertNotIn(first["token"].encode(), runtime)
        self.assertNotIn(second["token"].encode(), runtime)

    def test_revoke_disable_and_expiry_take_effect(self):
        from identity_store import IdentityStore

        store = IdentityStore(self.base)
        store.bootstrap_owner("owner-secret")
        user = store.create_user("Analyst", "viewer")
        issued = store.issue_token(user["user_id"])
        self.assertIsNotNone(store.verify_token(issued["token"]))
        self.assertTrue(store.revoke_token(issued["token_id"]))
        self.assertIsNone(store.verify_token(issued["token"]))

        active = store.issue_token(user["user_id"])
        store.set_user_status(user["user_id"], "disabled")
        self.assertIsNone(store.verify_token(active["token"]))
        store.set_user_status(user["user_id"], "active")
        expired = store.issue_token(
            user["user_id"],
            expires_at=(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).isoformat(),
        )
        self.assertIsNone(store.verify_token(expired["token"]))

    def test_schema_migration_and_file_permissions(self):
        from identity_store import IdentityStore

        store = IdentityStore(self.base)
        self.assertEqual(store.schema_version(), 1)
        self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(store.path.parent.stat().st_mode & 0o777, 0o700)

    def test_monitor_contexts_include_only_active_users_with_test_permission(self):
        from identity_store import IdentityStore
        store=IdentityStore(self.base); store.bootstrap_owner("owner-secret")
        operator=store.create_user("Operator","operator")
        viewer=store.create_user("Viewer","viewer")
        disabled=store.create_user("Disabled","operator"); store.set_user_status(disabled["user_id"],"disabled")
        contexts=store.monitor_contexts()
        self.assertEqual([item.user_id for item in contexts],[operator["user_id"]])
        self.assertTrue(contexts[0].allows("interfaces.test"))
        self.assertNotIn("owner",[item.user_id for item in contexts])


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from identity_store import AuthContext


def auth(tenant, user):
    return AuthContext(tenant, user, "operator", frozenset({"watchlist.read"}), "token")


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_users_have_distinct_private_roots(self):
        from workspace import WorkspaceContext

        first = WorkspaceContext(self.base, auth("tenant_a", "user_a"))
        second = WorkspaceContext(self.base, auth("tenant_b", "user_b"))
        self.assertNotEqual(first.root, second.root)
        first.write_json("watchlist.json", {"items": ["NVDA"]})
        second.write_json("watchlist.json", {"items": ["AMD"]})
        self.assertEqual(first.read_json("watchlist.json")["items"], ["NVDA"])
        self.assertEqual(second.read_json("watchlist.json")["items"], ["AMD"])

    def test_rejects_invalid_database_ids_and_path_escape(self):
        from workspace import WorkspaceContext

        with self.assertRaises(ValueError):
            WorkspaceContext(self.base, auth("../tenant", "user_a"))
        workspace = WorkspaceContext(self.base, auth("tenant_a", "user_a"))
        for name in ("../secret", "/etc/passwd", "unknown.json"):
            with self.assertRaises(ValueError):
                workspace.path(name)

    def test_directory_permissions_and_atomic_json(self):
        from workspace import WorkspaceContext

        workspace = WorkspaceContext(self.base, auth("tenant_a", "user_a"))
        workspace.write_json("interface_registry.json", {"version": 1})
        self.assertEqual(workspace.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(workspace.path("interface_registry.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse(any(workspace.root.glob("*.tmp")))

    def test_owner_compatibility_uses_existing_files(self):
        from workspace import WorkspaceContext

        owner = AuthContext("owner", "owner", "owner", frozenset({"*"}), "owner-admin")
        workspace = WorkspaceContext(self.base, owner, owner_compat=True)
        self.assertEqual(
            workspace.path("watchlist.json"),
            (self.base / "data" / "user_watchlist.json").resolve(),
        )
        workspace.write_json("watchlist.json", {"version": 1, "add": {}})
        self.assertEqual(workspace.read_json("watchlist.json")["version"], 1)


if __name__ == "__main__":
    unittest.main()

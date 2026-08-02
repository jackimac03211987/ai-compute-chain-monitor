import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from identity_store import AuthContext
from workspace import WorkspaceContext


def workspace(base, tenant, user):
    auth = AuthContext(tenant, user, "operator", frozenset({"interfaces.read", "interfaces.write"}), "token")
    return WorkspaceContext(base, auth)


def custom_record():
    return {"name": "Example API", "provider": "Example", "category": "provider", "purpose": "Health", "monitor_mode": "http", "method": "GET", "url": "https://example.com/health", "timeout_seconds": 8, "interval_minutes": 15, "expected_statuses": [200]}


class InterfaceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def test_each_user_gets_eight_private_builtins(self):
        from interface_registry import list_records
        first = list_records(workspace(self.base, "tenant_a", "user_a"))
        second = list_records(workspace(self.base, "tenant_b", "user_b"))
        self.assertEqual(first["total"], 8)
        self.assertEqual(second["total"], 8)
        self.assertNotEqual(first["workspace_hash"], "")

    def test_custom_lifecycle_and_builtin_reset_delete_rules(self):
        from admin_common import ApiError
        from interface_registry import create_record, delete_record, get_record, reset_builtin, update_record
        ws = workspace(self.base, "tenant_a", "user_a")
        created = create_record(ws, custom_record())
        update_record(ws, created["id"], {"interval_minutes": 30})
        self.assertEqual(get_record(ws, created["id"])["interval_minutes"], 30)
        delete_record(ws, created["id"])
        with self.assertRaises(ApiError): delete_record(ws, "yahoo_quote")
        update_record(ws, "yahoo_quote", {"interval_minutes": 12})
        self.assertEqual(reset_builtin(ws, "yahoo_quote")["interval_minutes"], 3)

    def test_custom_record_is_invisible_to_other_user(self):
        from interface_registry import create_record, list_records
        first = workspace(self.base, "tenant_a", "user_a")
        second = workspace(self.base, "tenant_b", "user_b")
        create_record(first, custom_record())
        self.assertEqual(list_records(first)["total"], 9)
        self.assertEqual(list_records(second)["total"], 8)

    def test_custom_quota_and_interval_are_enforced_without_counting_builtins(self):
        from admin_common import ApiError
        from interface_registry import create_record
        ws = workspace(self.base, "tenant_a", "user_a")
        with patch.dict("os.environ", {"AICM_MAX_INTERFACES_PER_USER":"1","AICM_MIN_CUSTOM_INTERVAL":"15"}):
            with self.assertRaises(ApiError) as invalid:
                create_record(ws, {**custom_record(), "interval_minutes": 1})
            self.assertEqual(invalid.exception.code, "invalid_interval")
            create_record(ws, custom_record())
            with self.assertRaises(ApiError) as quota:
                create_record(ws, custom_record())
            self.assertEqual(quota.exception.code, "quota_exceeded")

    def test_operator_cannot_enable_private_targets(self):
        from admin_common import ApiError
        from interface_registry import create_record
        with self.assertRaises(ApiError) as denied:
            create_record(workspace(self.base, "tenant_a", "user_a"), {**custom_record(), "allow_private_target": True})
        self.assertEqual(denied.exception.code, "private_target_forbidden")

    def test_import_batch_is_atomic_when_one_row_is_forbidden(self):
        from admin_common import ApiError
        from interface_registry import create_records, list_records
        ws = workspace(self.base, "tenant_a", "user_a")
        with self.assertRaises(ApiError):
            create_records(ws, [custom_record(), {**custom_record(), "name":"Private", "allow_private_target":True}])
        self.assertEqual(list_records(ws)["total"], 8)


if __name__ == "__main__": unittest.main()

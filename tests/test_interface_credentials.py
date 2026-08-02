import json
import unittest
from unittest.mock import patch

from identity_store import AuthContext


class FakeRunner:
    def __init__(self): self.items = {}
    def put(self, account, value): self.items[account] = value
    def get(self, account): return self.items.get(account)
    def delete(self, account): return self.items.pop(account, None) is not None


class InterfaceCredentialTests(unittest.TestCase):
    def test_accounts_are_user_scoped_and_results_are_secret_free(self):
        from interface_credentials import KeychainStore
        runner = FakeRunner()
        first = KeychainStore(AuthContext("tenant_a", "user_a", "operator", frozenset(), "t"), runner)
        second = KeychainStore(AuthContext("tenant_b", "user_b", "operator", frozenset(), "t"), runner)
        result = first.put("custom_1", {"type": "bearer", "token": "secret-value"})
        self.assertTrue(result["credential_configured"])
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertEqual(first.get("custom_1")["token"], "secret-value")
        self.assertEqual(second.get("custom_1"), {})
        self.assertIn("tenant:tenant_a:user:user_a:interface:custom_1", runner.items)

    def test_stage_promote_and_cleanup(self):
        from interface_credentials import KeychainStore
        runner = FakeRunner()
        store = KeychainStore(AuthContext("tenant_a", "user_a", "operator", frozenset(), "t"), runner)
        staged = store.stage("preview1", 2, {"type": "api_key", "value": "k"})
        store.promote(staged, "custom_2")
        self.assertEqual(store.get("custom_2")["value"], "k")
        self.assertEqual(store.clear_preview("preview1"), 0)

    def test_security_runner_never_places_secret_in_process_arguments(self):
        from interface_credentials import SecurityRunner
        with patch("interface_credentials.subprocess.run") as run:
            run.return_value.returncode = 0
            SecurityRunner().put("scoped-account", "top-secret-value")
        args = run.call_args.args[0]
        self.assertNotIn("top-secret-value", args)
        self.assertEqual(args[-1], "-w")
        self.assertEqual(run.call_args.kwargs["input"], "top-secret-value\n")


if __name__ == "__main__": unittest.main()

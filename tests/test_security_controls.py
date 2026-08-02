import os
import unittest
from unittest.mock import patch

from security_controls import AuthRateLimiter, client_identity


class SecurityControlTests(unittest.TestCase):
    def test_failure_window_locks_then_success_clears(self):
        now=[1000.0]
        limiter=AuthRateLimiter(threshold=3,window_seconds=300,max_entries=64,clock=lambda:now[0])
        key=("admin","ip:127.0.0.1")
        self.assertEqual(limiter.failure(key),0)
        self.assertEqual(limiter.failure(key),0)
        self.assertEqual(limiter.failure(key),60)
        self.assertEqual(limiter.retry_after(key),60)
        limiter.success(key)
        self.assertEqual(limiter.retry_after(key),0)

    def test_tailscale_identity_header_is_trusted_only_from_loopback_proxy(self):
        class Handler:
            headers={"Tailscale-User-Login":"tester@example.com"}
            client_address=("127.0.0.1",1234)
        with patch.dict(os.environ,{"AICM_TRUST_TAILSCALE_HEADERS":"1"}):
            self.assertTrue(client_identity(Handler()).startswith("tailscale:"))
        Handler.client_address=("100.90.1.2",1234)
        with patch.dict(os.environ,{"AICM_TRUST_TAILSCALE_HEADERS":"1"}):
            self.assertEqual(client_identity(Handler()),"ip:100.90.1.2")


if __name__ == "__main__": unittest.main()

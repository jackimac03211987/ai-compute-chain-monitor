import datetime, json, unittest


class InterfaceProbeTests(unittest.TestCase):
    def test_target_policy_rejects_unsafe_targets(self):
        from interface_probe import TargetPolicy
        def resolve(host):
            return {"example.com": ["93.184.216.34"], "private.test": ["127.0.0.1"]}.get(host, [host])
        policy = TargetPolicy(resolve)
        for url in ("file:///etc/passwd", "https://u:p@example.com", "http://169.254.169.254/latest", "http://100.64.0.10:8911", "http://private.test"):
            with self.assertRaises(ValueError): policy.validate(url, False)
        self.assertEqual(policy.validate("https://example.com/health", False)["host"], "example.com")
        self.assertEqual(policy.validate("http://private.test", True)["host"], "private.test")

    def test_rules_cover_status_latency_keyword_json_and_freshness(self):
        from interface_probe import evaluate_rules
        now = datetime.datetime(2026, 7, 11, 10, 0, tzinfo=datetime.timezone.utc)
        record = {"expected_statuses": [200], "max_latency_ms": 500, "body_keyword": "ready", "json_path": "data.state", "expected_value": "ok", "freshness_json_path": "data.updated_at", "max_age_minutes": 10}
        body = json.dumps({"message": "ready", "data": {"state": "ok", "updated_at": "2026-07-11T09:55:00Z"}}).encode()
        self.assertEqual(evaluate_rules(record, 200, 80, body, now)["status"], "healthy")
        self.assertEqual(evaluate_rules(record, 503, 80, body, now)["status"], "failed")

    def test_sanitized_errors_remove_query_values(self):
        from interface_probe import sanitize_probe_error
        value = sanitize_probe_error("failed https://x.test/a?api_key=secret&x=1 Authorization: Bearer abc")
        self.assertNotIn("secret", value)
        self.assertNotIn("Bearer abc", value)

    def test_redirect_handler_revalidates_destination(self):
        from interface_probe import SafeRedirectHandler, TargetPolicy
        policy = TargetPolicy(lambda host: ["127.0.0.1"] if host == "private.test" else ["93.184.216.34"])
        handler = SafeRedirectHandler(policy, False)
        with self.assertRaises(ValueError):
            handler.redirect_request(None, None, 302, "Found", {}, "http://private.test/")


if __name__ == "__main__": unittest.main()

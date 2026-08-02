import unittest
from unittest import mock

from tests.helpers import ProjectFixture


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.fx = ProjectFixture()

    def tearDown(self):
        self.fx.close()

    def test_token_fields_are_redacted_before_write(self):
        from admin_audit import append_event, query_events

        append_event(
            self.fx.base,
            "auth.verify",
            "failed",
            token="secret-token",
            nested={"Authorization": "Bearer secret-token", "safe": "kept"},
        )
        event = query_events(self.fx.base, {"page": "1", "page_size": "20"})[
            "items"
        ][0]
        raw = (self.fx.data / "admin_audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("secret-token", raw)
        self.assertEqual(event["token"], "[REDACTED]")
        self.assertEqual(event["nested"]["Authorization"], "[REDACTED]")
        self.assertEqual(event["nested"]["safe"], "kept")

    def test_query_filters_action_and_result(self):
        from admin_audit import append_event, query_events

        append_event(self.fx.base, "export", "success", affected_count=2)
        append_event(self.fx.base, "import.apply", "failed", affected_count=0)
        data = query_events(
            self.fx.base,
            {"action": "export", "result": "success", "page_size": "20"},
        )
        self.assertEqual([item["action"] for item in data["items"]], ["export"])
        self.assertEqual(data["total"], 1)
        self.assertIn("actions", data["facets"])

    def test_rotation_keeps_newest_events_in_active_file(self):
        import admin_audit

        with mock.patch.object(admin_audit, "MAX_EVENTS", 3):
            for index in range(5):
                admin_audit.append_event(
                    self.fx.base,
                    "test",
                    "success",
                    sequence=index,
                )
        active = admin_audit.query_events(
            self.fx.base,
            {"page": "1", "page_size": "20"},
        )["items"]
        self.assertEqual([item["sequence"] for item in active], [4, 3, 2])
        archives = list(self.fx.data.glob("admin_audit.*.jsonl"))
        self.assertEqual(len(archives), 1)
        self.assertIn('"sequence": 0', archives[0].read_text(encoding="utf-8"))

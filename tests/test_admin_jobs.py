import unittest

from tests.helpers import ProjectFixture


class JobTests(unittest.TestCase):
    def setUp(self):
        self.fx = ProjectFixture()

    def tearDown(self):
        self.fx.close()

    def test_second_process_lock_is_unavailable(self):
        from aicm_io import try_process_lock

        first = try_process_lock(self.fx.base, "history.lock")
        second = try_process_lock(self.fx.base, "history.lock")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        first.close()

    def test_duplicate_running_job_returns_existing_job(self):
        from admin_jobs import AdminJobManager

        manager = AdminJobManager(self.fx.base, runner=lambda job, done: None)
        first = manager.start("live")
        second = manager.start("live")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertTrue(second["already_running"])
        self.assertEqual(manager.get(first["job_id"])["status"], "running")

    def test_runner_callback_records_success_result(self):
        from admin_jobs import AdminJobManager

        def runner(job, done):
            done("succeeded", {"item_count": 2}, "")

        manager = AdminJobManager(self.fx.base, runner=runner)
        job = manager.start("interface_test")
        stored = manager.get(job["job_id"])
        self.assertEqual(stored["status"], "succeeded")
        self.assertEqual(stored["result"]["item_count"], 2)

    def test_restart_marks_running_jobs_interrupted(self):
        from admin_jobs import AdminJobManager

        manager = AdminJobManager(self.fx.base, runner=lambda job, done: None)
        job = manager.start("history")
        manager2 = AdminJobManager(self.fx.base, runner=lambda job, done: None)
        stored = manager2.get(job["job_id"])
        self.assertEqual(stored["status"], "interrupted")
        self.assertIn("service restarted", stored["error"])

    def test_list_filters_kind_and_status(self):
        from admin_jobs import AdminJobManager

        manager = AdminJobManager(self.fx.base, runner=lambda job, done: None)
        manager.start("live")
        data = manager.list({"kind": "live", "status": "running"})
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["kind"], "live")

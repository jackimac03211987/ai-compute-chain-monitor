import datetime, tempfile, threading, time, unittest
from pathlib import Path
from identity_store import AuthContext
from workspace import WorkspaceContext


class InterfaceSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
        auth = AuthContext("tenant_a", "user_a", "operator", frozenset({"interfaces.read","interfaces.write","interfaces.test"}), "t")
        self.workspace = WorkspaceContext(self.base, auth)
    def tearDown(self): self.tmp.cleanup()

    def test_retry_sequence(self):
        from interface_scheduler import next_delay_minutes
        self.assertEqual([next_delay_minutes(n, 60) for n in range(1,6)], [1,3,10,60,60])

    def test_result_history_is_bounded_and_private(self):
        from interface_scheduler import InterfaceResultStore
        store = InterfaceResultStore(self.workspace)
        for i in range(520):
            store.record("x", {"status":"healthy", "tested_at": (datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=i)).isoformat()})
        self.assertEqual(len(store.history("x", 1000)["items"]), 500)
        other_auth = AuthContext("tenant_b","user_b","operator",frozenset({"interfaces.read"}),"t")
        other = InterfaceResultStore(WorkspaceContext(self.base, other_auth))
        self.assertEqual(other.history("x")["items"], [])

    def test_due_time_uses_normal_interval_and_failure_backoff(self):
        from interface_scheduler import is_due
        now=datetime.datetime(2026,7,11,12,0,tzinfo=datetime.timezone.utc)
        self.assertTrue(is_due(None,60,now))
        healthy={"status":"healthy","tested_at":(now-datetime.timedelta(minutes=30)).isoformat()}
        self.assertFalse(is_due(healthy,60,now))
        self.assertTrue(is_due(healthy,20,now))
        failed={"status":"failed","consecutive_failures":2,"tested_at":(now-datetime.timedelta(minutes=2)).isoformat()}
        self.assertFalse(is_due(failed,60,now))
        self.assertTrue(is_due(failed,60,now+datetime.timedelta(minutes=2)))

    def test_duplicate_and_per_host_work_is_suppressed(self):
        from interface_scheduler import InterfaceMonitorScheduler
        entered=threading.Event(); release=threading.Event(); results=[]
        def operation(): entered.set(); release.wait(2); return {"status":"healthy"}
        scheduler=InterfaceMonitorScheduler(max_workers=4,per_host_limit=1)
        try:
            self.assertTrue(scheduler.submit("a",operation,results.append,host="example.com"))
            self.assertTrue(entered.wait(1))
            self.assertFalse(scheduler.submit("a",operation,results.append,host="other.com"))
            self.assertFalse(scheduler.submit("b",operation,results.append,host="example.com"))
            self.assertTrue(scheduler.submit("c",lambda:{"status":"healthy"},results.append,host="other.com"))
        finally:
            release.set(); scheduler.stop()
        self.assertEqual(len(results),2)

    def test_background_loop_runs_due_items_and_stops(self):
        from interface_scheduler import InterfaceMonitorScheduler
        completed=threading.Event(); calls=[]
        def provider():
            return [{"key":"x","host":"local","interval_minutes":15,"latest":None,
                     "operation":lambda:{"status":"healthy"},"callback":lambda result:(calls.append(result),completed.set())}]
        scheduler=InterfaceMonitorScheduler(max_workers=1,poll_seconds=.01)
        scheduler.start(provider)
        self.assertTrue(completed.wait(1))
        scheduler.stop()
        count=len(calls); time.sleep(.03)
        self.assertEqual(len(calls),count)

    def test_pending_queue_and_tenant_budget_are_bounded(self):
        from interface_scheduler import InterfaceMonitorScheduler
        release=threading.Event()
        scheduler=InterfaceMonitorScheduler(max_workers=1,per_host_limit=5,max_pending=1,tenant_hourly_budget=1)
        try:
            self.assertTrue(scheduler.submit(("tenant_a","user_a","one"),lambda:(release.wait(2) or {"status":"healthy"}),lambda result:None,host="one.example"))
            self.assertFalse(scheduler.submit(("tenant_b","user_b","two"),lambda:{"status":"healthy"},lambda result:None,host="two.example"))
            self.assertEqual(scheduler.stats()["dropped"].get("capacity"),1)
        finally:
            release.set(); scheduler.stop()
        budget=InterfaceMonitorScheduler(max_workers=1,per_host_limit=5,max_pending=4,tenant_hourly_budget=1)
        done=threading.Event()
        try:
            self.assertTrue(budget.submit(("tenant_a","user_a","one"),lambda:{"status":"healthy"},lambda result:done.set(),host="one.example"))
            self.assertTrue(done.wait(1))
            self.assertFalse(budget.submit(("tenant_a","user_a","two"),lambda:{"status":"healthy"},lambda result:None,host="two.example"))
            self.assertEqual(budget.stats()["dropped"].get("budget"),1)
        finally: budget.stop()


if __name__ == "__main__": unittest.main()

import datetime as dt
import hashlib
import tempfile
import unittest
from pathlib import Path

from identity_store import IdentityStore
from interface_registry import create_record, list_records
from interface_transfer import apply_preview, create_preview
from personal_watchlist import add_personal_tickers, list_personal_watchlist
from workspace import WorkspaceContext


class PrivateEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name); self.data=self.base/"data"; self.data.mkdir()
        for name,payload in {
            "active_universe.json":b'{"companies":[]}',"live.json":b'{"items":{}}',"semi_market.json":b'{"companies":[]}'
        }.items(): (self.data/name).write_bytes(payload)
        self.protected=("active_universe.json","live.json","semi_market.json")
    def tearDown(self): self.tmp.cleanup()
    def hashes(self):
        return {name:hashlib.sha256((self.data/name).read_bytes()).hexdigest() for name in self.protected}

    def test_two_user_lifecycle_is_private_and_production_files_are_immutable(self):
        before=self.hashes(); store=IdentityStore(self.base); store.bootstrap_owner("owner-secret")
        first=store.create_user("First","operator"); second=store.create_user("Second","operator")
        first_token=store.issue_token(first["user_id"]); second_token=store.issue_token(second["user_id"])
        first_auth=store.verify_token(first_token["token"]); second_auth=store.verify_token(second_token["token"])
        first_ws=WorkspaceContext(self.base,first_auth); second_ws=WorkspaceContext(self.base,second_auth)

        add_personal_tickers(first_ws,[{"ticker":"NVDA","name":"NVIDIA"}])
        add_personal_tickers(second_ws,[{"ticker":"AMD","name":"AMD"}])
        self.assertEqual([row["ticker"] for row in list_personal_watchlist(first_ws)["items"]],["NVDA"])
        self.assertEqual([row["ticker"] for row in list_personal_watchlist(second_ws)["items"]],["AMD"])

        create_record(first_ws,{"name":"First local","monitor_mode":"local_task","interval_minutes":15,"timeout_seconds":8})
        preview=create_preview(second_ws,[{"name":"Second import","monitor_mode":"local_task","interval_minutes":20,"timeout_seconds":8}],"interfaces.json")
        self.assertEqual(apply_preview(second_ws,preview["preview_id"])["applied"],1)
        self.assertEqual(list_records(first_ws)["total"],9); self.assertEqual(list_records(second_ws)["total"],9)
        self.assertNotIn("Second import",[row["name"] for row in list_records(first_ws)["items"]])

        expiry=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)).isoformat()
        grant=store.create_support_grant(first_auth,"owner",["interfaces"],"End-to-end support",expiry)
        support=store.resolve_support_context(store.verify_token("owner-secret"),grant["grant_id"])
        self.assertEqual(support.user_id,first["user_id"]); self.assertTrue(store.revoke_support_grant(first_auth,grant["grant_id"]))
        self.assertTrue(store.revoke_token(first_token["token_id"])); self.assertIsNone(store.verify_token(first_token["token"]))
        self.assertIsNotNone(store.verify_token(second_token["token"]))
        self.assertEqual(self.hashes(),before)


if __name__=="__main__": unittest.main()

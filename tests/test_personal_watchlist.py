import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from identity_store import AuthContext
from workspace import WorkspaceContext


def auth(tenant, user):
    return AuthContext(
        tenant, user, "operator",
        frozenset({"watchlist.read", "watchlist.write"}), "token",
    )


class PersonalWatchlistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        data = self.base / "data"
        data.mkdir()
        for name, payload in {
            "active_universe.json": {"companies": [{"t": "NVDA"}]},
            "live.json": {"items": {"NVDA": {"t": "NVDA", "p": 160, "chg": 1.2}}},
            "semi_market.json": {"companies": [{"t": "NVDA"}], "dates": ["2026-07-11"]},
        }.items():
            (data / name).write_text(json.dumps(payload), encoding="utf-8")
        self.protected = [data / name for name in ("active_universe.json", "live.json", "semi_market.json")]

    def tearDown(self):
        self.tmp.cleanup()

    def hashes(self):
        return [hashlib.sha256(path.read_bytes()).hexdigest() for path in self.protected]

    def test_users_have_independent_watchlists_with_shared_quote_overlay(self):
        from personal_watchlist import add_personal_tickers, list_personal_watchlist

        first = WorkspaceContext(self.base, auth("tenant_a", "user_a"))
        second = WorkspaceContext(self.base, auth("tenant_b", "user_b"))
        add_personal_tickers(first, [{"ticker": "NVDA", "name": "NVIDIA"}])
        add_personal_tickers(second, [{"ticker": "AMD", "name": "AMD"}])
        quotes = {"NVDA": {"p": 160, "chg": 1.2}}
        one = list_personal_watchlist(first, quotes)
        two = list_personal_watchlist(second, quotes)
        self.assertEqual([row["ticker"] for row in one["items"]], ["NVDA"])
        self.assertEqual(one["items"][0]["quote"]["p"], 160)
        self.assertEqual([row["ticker"] for row in two["items"]], ["AMD"])

    def test_updates_preserve_blank_fields_and_removal_is_private(self):
        from personal_watchlist import add_personal_tickers, remove_personal_tickers, update_personal_ticker

        workspace = WorkspaceContext(self.base, auth("tenant_a", "user_a"))
        add_personal_tickers(workspace, [{"ticker": "NVDA", "name": "NVIDIA", "city": "Santa Clara"}])
        updated = update_personal_ticker(workspace, "NVDA", {"name": "", "city": "San Jose"})
        self.assertEqual(updated["item"]["name"], "NVIDIA")
        self.assertEqual(updated["item"]["city"], "San Jose")
        self.assertEqual(remove_personal_tickers(workspace, ["NVDA"])["count"], 0)

    def test_personal_mutations_do_not_change_production_files(self):
        from personal_watchlist import add_personal_tickers, remove_personal_tickers

        before = self.hashes()
        workspace = WorkspaceContext(self.base, auth("tenant_a", "user_a"))
        add_personal_tickers(workspace, [{"ticker": "TSLA", "name": "Tesla"}])
        remove_personal_tickers(workspace, ["TSLA"])
        self.assertEqual(self.hashes(), before)


if __name__ == "__main__":
    unittest.main()

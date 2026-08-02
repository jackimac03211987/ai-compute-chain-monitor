import unittest

from tests.helpers import ProjectFixture


class AdminServiceTests(unittest.TestCase):
    def setUp(self):
        self.fx = ProjectFixture()
        self.base_rows = [
            {
                "t": "NVDA",
                "name": "NVIDIA",
                "country": "美国",
                "city": "Santa Clara",
                "lat": 37.35,
                "lon": -121.95,
                "seg": "GPU",
            },
            {
                "t": "600519.SS",
                "name": "贵州茅台",
                "country": "中国大陆",
                "city": "Guiyang",
                "lat": 26.65,
                "lon": 106.63,
                "seg": "Other",
            },
        ]
        self.fx.write_json("watchlist.json", {"companies": self.base_rows})
        self.fx.write_json(
            "user_watchlist.json",
            {"version": 1, "replace_base": False, "add": {}, "remove": []},
        )
        self.fx.write_json(
            "active_universe.json",
            {
                "companies": [
                    {**row, "source": "base"} for row in self.base_rows
                ],
                "counts": {"active": 2, "base": 2, "user_add": 0, "removed": 0},
            },
        )
        self.fx.write_json(
            "live.json",
            {
                "asof": "2026-07-11 08:00",
                "items": {
                    "NVDA": {
                        "t": "NVDA",
                        "p": 160,
                        "chg": 1.2,
                        "market_time": 1783730000,
                        "source": "yahoo_chart",
                    }
                },
            },
        )
        self.fx.write_json(
            "live_status.json",
            {
                "status": "success",
                "last_success": "2026-07-11 08:00",
                "elapsed_s": 3.2,
                "expected_count": 2,
                "fresh_count": 1,
                "failed_count": 1,
            },
        )
        self.fx.write_json(
            "semi_market.json",
            {"companies": [], "dates": [], "meta": {"fetched": "2026-06-26"}},
        )

    def tearDown(self):
        self.fx.close()

    def default_companies(self):
        return {
            row["t"]: (
                row["name"],
                row["country"],
                row["city"],
                row["lat"],
                row["lon"],
                row["seg"],
            )
            for row in self.base_rows
        }

    def test_overview_keeps_live_and_history_health_separate(self):
        from admin_service import build_overview

        data = build_overview(self.fx.base)
        self.assertEqual(data["quotes"]["status"], "healthy")
        self.assertEqual(data["quotes"]["fresh"], 1)
        self.assertEqual(data["history"]["status"], "failed")
        self.assertGreaterEqual(data["attention"]["count"], 2)

    def test_overview_does_not_double_count_retry_failures(self):
        from admin_service import build_overview

        status = self.fx.read_json("live_status.json")
        status["failed_count"] = 4
        self.fx.write_json("live_status.json", status)
        quotes = build_overview(self.fx.base)["quotes"]
        self.assertEqual(quotes["failed"], 1)
        self.assertEqual(quotes["provider_failure_events"], 4)

    def test_interface_inventory_has_all_eight_categories(self):
        from admin_service import list_interfaces

        items = list_interfaces(self.fx.base)
        self.assertEqual(len(items), 8)
        self.assertEqual(
            {item["id"] for item in items},
            {
                "web_api",
                "yahoo_quote",
                "yahoo_fx",
                "live_task",
                "history_task",
                "catalog",
                "browser_json",
                "transfer_engine",
            },
        )

    def test_history_task_error_overrides_retained_history_file(self):
        from admin_service import build_overview

        self.fx.write_json(
            "semi_market.json",
            {
                "companies": [{"t": "NVDA"}],
                "dates": ["2026-07-11"],
                "meta": {"fetched": "2026-07-11"},
            },
        )
        self.fx.write_json(
            "history_status.json",
            {
                "status": "error",
                "last_attempt": "2026-07-11 07:30:00",
                "last_success": "2026-06-26 07:39:00",
                "last_error": "provider returned no rows",
            },
        )
        history = build_overview(self.fx.base)["history"]
        self.assertEqual(history["status"], "failed")
        self.assertEqual(history["retained_data_available"], True)
        self.assertIn("provider returned no rows", history["message"])

    def test_company_query_filters_country_and_marks_missing_quote(self):
        from admin_service import query_companies

        data = query_companies(
            self.fx.base,
            {"country": "中国大陆", "page": "1", "page_size": "50"},
        )
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["t"], "600519.SS")
        self.assertEqual(data["items"][0]["quote_state"], "missing")
        self.assertEqual(data["items"][0]["exchange"], "SSE")

    def test_company_query_searches_name_and_paginates(self):
        from admin_service import query_companies

        data = query_companies(
            self.fx.base,
            {"q": "nvidia", "page": "1", "page_size": "25"},
        )
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["t"], "NVDA")
        self.assertIn("countries", data["facets"])

    def test_update_preserves_blank_fields(self):
        from admin_service import update_company

        result = update_company(
            self.fx.base,
            "NVDA",
            {"name": "", "city": "San Jose"},
            self.default_companies,
        )
        self.assertEqual(result["company"]["name"], "NVIDIA")
        self.assertEqual(result["company"]["city"], "San Jose")
        self.assertEqual(result["counts"]["active"], 2)

    def test_toggle_base_company_updates_removed_state(self):
        from admin_service import query_companies, toggle_company

        result = toggle_company(
            self.fx.base,
            "NVDA",
            False,
            self.default_companies,
        )
        self.assertEqual(result["counts"]["active"], 1)
        rows = query_companies(self.fx.base, {"status": "removed"})["items"]
        self.assertEqual([row["t"] for row in rows], ["NVDA"])

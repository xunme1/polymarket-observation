import argparse
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import observe
from market_data import normalize_event


def event(key="oil"):
    return {"id": key, "title": "原油 <观察>", "topic": "原油", "url": "https://polymarket.com/event/example"}


def quote():
    return normalize_event({"slug": "example", "title": "Public event", "volume": "12345",
        "markets": [{"id": "1", "question": "Question?", "outcomes": '["Yes", "No"]',
                     "outcomePrices": '["0.6", "0.4"]'}]}, event(), observe.now())


def success(key="oil"):
    return dict(event(key), status="ok", market_data=quote(), captured_at=observe.now(), original_title="Public event",
                images=[{"jpeg": "test-fixture", "sha256": "test", "width": 900, "height": 600}])


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.market_patch = patch.object(observe, "collect_market", side_effect=lambda _: quote())
        self.collect = self.market_patch.start()
        self.addCleanup(self.market_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        os.chdir(self.temp.name)
        self.archive = Path("snapshots")
        self.output = Path("dist")
        self.date = datetime.now(observe.TZ).date().isoformat()
        self.snapshot = {"version": 1, "date": self.date, "generated_at": observe.now(), "events": [success()]}

    def tearDown(self):
        os.chdir(self.old)
        self.temp.cleanup()

    def args(self, force=False):
        return argparse.Namespace(archive="snapshots", output="dist", attempts=1, retry_seconds=0, force=force, event=None)

    def test_render_escapes_source_and_keeps_missing_event_empty(self):
        self.snapshot["events"].append(dict(event("gold"), status="error", error="<script>bad</script>"))
        text = observe.render(self.snapshot, [self.date]).decode()
        self.assertIn("部分更新", text)
        self.assertIn("原油 &lt;观察&gt;", text)
        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", text)
        self.assertNotIn("<script>bad</script>", text)
        self.assertEqual(text.count('class="shot"'), 1)

    def test_oversize_never_replaces_previous_snapshot_or_page(self):
        observe.build_site(self.archive, self.output, self.snapshot)
        before = (self.output / "index.html").read_bytes()
        stored = (self.archive / f"{self.date}.json").read_bytes()
        changed = copy.deepcopy(self.snapshot)
        changed["events"][0]["title"] = "new content"
        with patch.object(observe, "MAX_REPORT", 100), self.assertRaisesRegex(ValueError, "8 MiB"):
            observe.build_site(self.archive, self.output, changed)
        self.assertEqual(before, (self.output / "index.html").read_bytes())
        self.assertEqual(stored, (self.archive / f"{self.date}.json").read_bytes())

    def test_manifest_hash_matches_report(self):
        observe.build_site(self.archive, self.output, self.snapshot)
        data = json.loads((self.output / "manifest.json").read_text())["reports"][0]
        body = (self.output / data["url"]).read_bytes()
        self.assertEqual(data["sha256"], observe.hashlib.sha256(body).hexdigest())
        self.assertEqual(data["size"], len(body))

    def test_complete_same_day_run_does_not_open_browser(self):
        observe.build_site(self.archive, self.output, self.snapshot)
        with patch.object(observe, "watchlist", return_value=[event()]), patch("playwright.sync_api.sync_playwright") as browser:
            observe.run(self.args())
            browser.assert_not_called()

    def test_all_failed_force_run_preserves_previous_report(self):
        observe.build_site(self.archive, self.output, self.snapshot)
        before = (self.archive / f"{self.date}.json").read_bytes()
        self.collect.side_effect = TimeoutError("API timed out")
        with patch.object(observe, "watchlist", return_value=[event()]), patch("playwright.sync_api.sync_playwright", return_value=MagicMock()), patch.object(observe, "capture", side_effect=TimeoutError("Timed out")):
            with self.assertRaisesRegex(ValueError, "All events failed"):
                observe.run(self.args(force=True))
        self.assertEqual(before, (self.archive / f"{self.date}.json").read_bytes())
        self.assertTrue((self.output / "index.html").exists())

    def test_partial_retry_only_captures_failed_event(self):
        self.snapshot["events"].append(dict(event("gold"), status="error", error="Timeout"))
        observe.build_site(self.archive, self.output, self.snapshot)
        with patch.object(observe, "watchlist", return_value=[event(), event("gold")]), patch("playwright.sync_api.sync_playwright", return_value=MagicMock()), patch.object(observe, "capture", return_value=success("gold")) as capture:
            observe.run(self.args())
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(capture.call_args.args[1]["id"], "gold")
        report = json.loads((self.archive / f"{self.date}.json").read_text())
        self.assertEqual([e["status"] for e in report["events"]], ["ok", "ok"])

    def test_history_remains_available(self):
        earlier = copy.deepcopy(self.snapshot)
        earlier["date"] = "2026-01-01"
        observe.build_site(self.archive, self.output, earlier)
        observe.build_site(self.archive, self.output, self.snapshot)
        self.assertTrue((self.output / "reports/2026-01-01.html").exists())
        self.assertIn("2026-01-01", (self.output / "index.html").read_text())

    def test_legacy_today_enriches_only_prices_without_browser(self):
        del self.snapshot["events"][0]["market_data"]
        observe.build_site(self.archive, self.output, self.snapshot)
        with patch.object(observe, "watchlist", return_value=[event()]), patch("playwright.sync_api.sync_playwright") as browser:
            observe.run(self.args())
            browser.assert_not_called()
        self.collect.assert_called_once()
        stored = json.loads((self.archive / f"{self.date}.json").read_text())
        self.assertEqual(stored["events"][0]["captured_at"], self.snapshot["events"][0]["captured_at"])
        self.assertEqual(stored["version"], 2)

    def test_api_only_success_publishes_and_retry_only_captures_screenshot(self):
        with patch.object(observe, "watchlist", return_value=[event()]), patch("playwright.sync_api.sync_playwright", return_value=MagicMock()), patch.object(observe, "capture", side_effect=TimeoutError("Timeout")):
            observe.run(self.args())
        text = (self.output / "index.html").read_text()
        self.assertIn("部分更新", text)
        self.assertIn("60%", text)
        self.assertIn("原站截图暂不可用", text)
        self.collect.reset_mock()
        with patch.object(observe, "watchlist", return_value=[event()]), patch("playwright.sync_api.sync_playwright", return_value=MagicMock()), patch.object(observe, "capture", return_value=success()):
            observe.run(self.args())
        self.collect.assert_not_called()

    def test_failed_api_keeps_screenshot_and_next_run_only_retries_api(self):
        del self.snapshot["events"][0]["market_data"]
        observe.build_site(self.archive, self.output, self.snapshot)
        self.collect.side_effect = TimeoutError("API timed out")
        with patch.object(observe, "watchlist", return_value=[event()]), patch("playwright.sync_api.sync_playwright") as browser:
            observe.run(self.args())
            browser.assert_not_called()
        text = (self.output / "index.html").read_text()
        self.assertIn("本期仅提供截图", text)
        self.assertIn("API timed out", text)
        self.collect.side_effect = lambda _: quote()
        with patch.object(observe, "watchlist", return_value=[event()]), patch("playwright.sync_api.sync_playwright") as browser:
            observe.run(self.args())
            browser.assert_not_called()
        self.assertIn("完整更新", (self.output / "index.html").read_text())

    def test_older_history_never_receives_current_quotes(self):
        earlier = copy.deepcopy(self.snapshot)
        earlier["date"] = "2026-01-01"
        del earlier["events"][0]["market_data"]
        observe.build_site(self.archive, self.output, earlier)
        stored = (self.archive / "2026-01-01.json").read_bytes()
        with patch.object(observe, "watchlist", return_value=[event()]), patch("playwright.sync_api.sync_playwright", return_value=MagicMock()), patch.object(observe, "capture", return_value=success()):
            observe.run(self.args())
        self.assertEqual(stored, (self.archive / "2026-01-01.json").read_bytes())
        text = (self.output / "reports/2026-01-01.html").read_text()
        self.assertIn("本期仅提供截图", text)
        self.assertNotIn("60%", text)

    def test_changed_event_url_invalidates_both_cached_sources(self):
        observe.build_site(self.archive, self.output, self.snapshot)
        changed = dict(event(), url="https://polymarket.com/event/new-source")
        with patch.object(observe, "watchlist", return_value=[changed]), patch("playwright.sync_api.sync_playwright", return_value=MagicMock()), patch.object(observe, "capture", return_value=dict(success(), url=changed["url"])) as capture:
            observe.run(self.args())
        self.collect.assert_called_once_with(changed)
        capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()

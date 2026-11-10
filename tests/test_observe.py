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


def event(key="oil"):
    return {"id": key, "title": "原油 <观察>", "topic": "原油", "url": "https://polymarket.com/event/example"}


def success(key="oil"):
    return dict(event(key), status="ok", captured_at=observe.now(), original_title="Public event",
                images=[{"jpeg": "test-fixture", "sha256": "test", "width": 900, "height": 600}])


class SnapshotTests(unittest.TestCase):
    def setUp(self):
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


if __name__ == "__main__":
    unittest.main()

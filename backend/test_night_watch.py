"""Fast, dependency-free checks for the optional Night Watch Crew extension."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import balance
import server


class NightWatchTests(unittest.TestCase):
    def test_config_rejects_unsafe_ranges_and_boolean_counts(self):
        repo = str(Path(__file__).resolve().parents[3])
        for values in (
            {"count": 0},
            {"count": 10001},
            {"count": True},
            {"threshold": float("nan")},
            {"firstSeed": -1},
            {"firstSeed": 2**32 - 1, "count": 2},
            {"baseline": "HEAD\nother"},
        ):
            with self.subTest(values=values):
                with self.assertRaises(balance.ConfigError):
                    balance.validate_config({"repo": repo, **values})

    def test_same_seed_threshold_alerts_and_seat_growth(self):
        config = {"count": 1000, "threshold": 10}
        baseline = {"cowboy": 500, "wizard": 400, "first": 570, "second": 330}
        candidate = {"cowboy": 600, "wizard": 350, "first": 650, "second": 300}
        status, detail = balance.judge(config, baseline, candidate)
        self.assertEqual(status, "alert")
        self.assertIn("Cowboy win share rose 10.0 pp.", detail)
        self.assertIn("First-seat advantage grew 11.0 pp.", detail)

    def test_small_variances_do_not_trigger_alert(self):
        config = {"count": 1000, "threshold": 10}
        baseline = {"cowboy": 500, "wizard": 400, "first": 530, "second": 470}
        candidate = {"cowboy": 540, "wizard": 370, "first": 550, "second": 450}
        status, detail = balance.judge(config, baseline, candidate)
        self.assertEqual(status, "steady")
        self.assertIn("within", detail)

    def test_isolated_state_store_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="night-watch-unit-") as directory:
            with patch.dict(os.environ, {"NIGHT_WATCH_DATA_DIR": directory}):
                self.assertEqual(server.load_reports(), [])
                self.assertEqual(server.load_config()["count"], 1000)
                report = {"id": "test-only", "state": "steady"}
                server.write_json("history.json", [report])
                self.assertEqual(server.load_reports(), [report])
                self.assertTrue((Path(directory) / "history.json").is_file())


if __name__ == "__main__":
    unittest.main()

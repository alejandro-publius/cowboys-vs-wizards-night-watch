"""Fast, dependency-free checks for the optional Night Watch Crew extension."""
import os
import shutil
import subprocess
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

    def assert_extracted_fixture_runs(self, files):
        with tempfile.TemporaryDirectory(prefix="night-watch-fixture-") as directory:
            repo = os.path.join(directory, "repo")
            for name, body in files.items():
                path = os.path.join(repo, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                Path(path).write_text(body)
            git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com"]
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True)
            commit = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            snapshot = os.path.join(directory, "snapshot")
            balance.extract_snapshot(repo, commit, snapshot)
            state, count = balance.run_game_tests(snapshot, balance.node_command())
            self.assertEqual((state, count), ("passed", 1))

    @unittest.skipUnless(shutil.which("node") and shutil.which("git"), "needs node and git")
    def test_extracted_snapshot_runs_game_test_that_imports_profile_module(self):
        self.assert_extracted_fixture_runs({
            "package.json": '{"type": "module"}',
            "src/game/engine.js": "export const turn = 1;\n",
            "src/profile/player-profile.js": "export const profile = 'ok';\n",
            "scripts/play-match.mjs": "",
            "scripts/simulate-matches.mjs": "",
            "tests/game/player-profile.test.js": (
                "import test from 'node:test';\nimport assert from 'node:assert';\n"
                "import { profile } from '../../src/profile/player-profile.js';\n"
                "test('profile', () => assert.equal(profile, 'ok'));\n"
            ),
        })

    @unittest.skipUnless(shutil.which("node") and shutil.which("git"), "needs node and git")
    def test_extracted_legacy_snapshot_without_profile_runs_game_test(self):
        self.assert_extracted_fixture_runs({
            "package.json": '{"type": "module"}',
            "src/game/engine.js": "export const turn = 1;\n",
            "scripts/play-match.mjs": "",
            "scripts/simulate-matches.mjs": "",
            "tests/game/engine.test.js": (
                "import test from 'node:test';\nimport assert from 'node:assert';\n"
                "import { turn } from '../../src/game/engine.js';\n"
                "test('legacy engine', () => assert.equal(turn, 1));\n"
            ),
        })


if __name__ == "__main__":
    unittest.main()

"""Fast, dependency-free checks for the optional Night Watch Crew extension."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import balance
import issues
import lean
import server


def git_checkout_fixture(test):
    """A tiny real Git checkout with one commit at HEAD, removed after the test, so config checks do not depend on
    where this app is installed (apps/night-watch in the game, or a standalone clone)."""
    directory = tempfile.mkdtemp(prefix="night-watch-config-")
    test.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    git = ["git", "-C", directory, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", directory], check=True)
    Path(directory, "README.md").write_text("fixture\n")
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True)
    return os.path.realpath(directory)


class NightWatchTests(unittest.TestCase):
    def test_config_rejects_unsafe_ranges_and_boolean_counts(self):
        repo = git_checkout_fixture(self)
        self.assertEqual(balance.validate_config({"repo": repo})["repo"], repo)
        for values, field in (
            ({"count": 0}, "Match count"),
            ({"count": 10001}, "Match count"),
            ({"count": True}, "Match count"),
            ({"threshold": float("nan")}, "Threshold"),
            ({"firstSeed": -1}, "First seed"),
            ({"firstSeed": 2**32 - 1, "count": 2}, "First seed"),
            ({"baseline": "HEAD\nother"}, "baseline ref"),
        ):
            with self.subTest(values=values):
                with self.assertRaisesRegex(balance.ConfigError, field):
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


class ShowdownModeTests(unittest.TestCase):
    """Showdown mode: each deal played twice with the first seat swapped (prototypes/showdown/sim.mjs)."""

    def test_config_accepts_only_known_modes_and_keeps_lanes_default(self):
        repo = git_checkout_fixture(self)
        self.assertEqual(balance.DEFAULT_CONFIG["mode"], "lanes")
        self.assertEqual(balance.validate_config({"repo": repo, "mode": "showdown"})["mode"], "showdown")
        with self.assertRaises(balance.ConfigError):
            balance.validate_config({"repo": repo, "mode": "trail"})

    def test_parse_requires_both_seats_for_every_deal(self):
        config = {"firstSeed": 0, "count": 2, "mode": "showdown"}
        good = {"mode": "showdown", "seeds": {"from": 0, "to": 1, "deals": 2},
                "games": 4, "cowboy": 3, "wizard": 1, "draw": 0, "first": 3, "second": 1,
                "byStartingSide": {"cowboy": {"cowboy": 2, "wizard": 0, "draw": 0},
                                   "wizard": {"cowboy": 1, "wizard": 1, "draw": 0}}}
        self.assertEqual(balance.parse_showdown(good, config)["games"], 4)
        for change in ({"games": 2, "cowboy": 1, "wizard": 1}, {"first": 2}, {"seeds": {"from": 1, "to": 2, "deals": 2}},
                       {"byStartingSide": {"cowboy": {"cowboy": 1, "wizard": 1, "draw": 0},
                                           "wizard": {"cowboy": 1, "wizard": 1, "draw": 0}}}):
            with self.subTest(change=change):
                with self.assertRaises(balance.CheckError):
                    balance.parse_showdown({**good, **change}, config)

    def test_judge_divides_showdown_shifts_by_games_not_deals(self):
        config = {"count": 1000, "threshold": 10}
        base = {"games": 2000, "cowboy": 1186, "wizard": 814, "draw": 0, "first": 1150, "second": 850}
        # +150 Cowboy wins is 7.5 pp of 2,000 games (it would be 15 pp of 1,000 deals).
        cand = {**base, "cowboy": 1336, "wizard": 664}
        self.assertEqual(balance.judge(config, base, cand)[0], "steady")
        cand = {**base, "cowboy": 1386, "wizard": 614}
        status, detail = balance.judge(config, base, cand)
        self.assertEqual(status, "alert")
        self.assertIn("Cowboy win share rose 10.0 pp.", detail)

    def test_nightly_job_selects_showdown(self):
        calls = []
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {"NIGHT_WATCH_DATA_DIR": data}), \
                patch.object(server.shutil, "which", return_value="/usr/bin/kirocrew"), \
                patch.object(server, "load_config", return_value={"repo": "/tmp/x"}), \
                patch.object(server.subprocess, "run", side_effect=lambda argv: calls.append(argv) or subprocess.CompletedProcess(argv, 0)):
            self.assertEqual(server.main(["schedule", "--time", "02:00"]), 0)
        command = calls[0][calls[0].index("--command") + 1]
        self.assertTrue(command.endswith("run --scheduled --mode showdown"), command)

    @unittest.skipUnless(shutil.which("node") and shutil.which("git"), "needs node and git")
    def test_runner_plays_each_deal_from_both_seats_in_an_extracted_snapshot(self):
        rules = (
            "export function createShowdown(seed, {first}) { return {seed, first, turn: 0, result: null}; }\n"
            "export function playTurn(state) {\n"
            "  const winner = state.seed % 3 === 2 ? null : state.seed % 3 === 0 ? state.first : 'cowboy';\n"
            "  return {state: {...state, turn: 1, result: {winner, reason: 'base'}}, events: []};\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory(prefix="night-watch-showdown-") as directory:
            repo = os.path.join(directory, "repo")
            for name, body in {"package.json": '{"type": "module"}', "src/game/engine.js": "",
                               "prototypes/showdown/rules.js": rules}.items():
                os.makedirs(os.path.dirname(os.path.join(repo, name)), exist_ok=True)
                Path(repo, name).write_text(body)
            git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com"]
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True)
            commit = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            snapshot = os.path.join(directory, "snapshot")
            balance.extract_snapshot(repo, commit, snapshot, "showdown")
            counts = balance.simulate(snapshot, balance.node_command(), {"firstSeed": 0, "count": 3, "mode": "showdown"})
        # Seed 0: the first seat wins both games. Seed 1: Cowboys win both. Seed 2: two draws.
        self.assertEqual(counts, {"games": 6, "cowboy": 3, "wizard": 1, "draw": 2, "first": 3, "second": 1,
                                  "byStartingSide": {"cowboy": {"cowboy": 2, "wizard": 0, "draw": 1},
                                                     "wizard": {"cowboy": 1, "wizard": 1, "draw": 1}}})


SHA_A = "a" * 40
SHA_B = "b" * 40


def showdown_counts(first, second, cowboy, draw=0):
    """Counts for 2,000 Showdown games (1,000 deals), built so the two seat totals add up."""
    games = first + second + draw
    return {"games": games, "first": first, "second": second, "cowboy": cowboy, "wizard": first + second - cowboy, "draw": draw}


def record_for(base_counts, cand_counts, band, base=SHA_A, cand=SHA_A, threshold=10):
    config = {"count": 1000, "threshold": threshold}
    state, message = balance.judge(config, base_counts, cand_counts)
    return {
        "at": "2026-11-19T10:00:00Z", "source": "on-demand", "firstSeed": 0, "count": 1000, "threshold": threshold,
        "mode": "showdown", "band": band, "state": state, "message": message,
        "baseline": {"ref": "main", "commit": base, "counts": base_counts},
        "candidate": {"ref": "main", "commit": cand, "counts": cand_counts},
        "rules": {"state": "passed", "tests": 3}, "changes": [],
    }


PROVENANCE = {"bot": "playTurn level 2 (both seats)", "repository": "owner/game", "commands": ["node x"]}


class LeanTests(unittest.TestCase):
    """Absolute lean against an operator band, separate from the baseline shift."""

    def test_band_is_optional_and_validated(self):
        repo = git_checkout_fixture(self)
        self.assertIsNone(balance.DEFAULT_CONFIG["band"])
        self.assertIsNone(balance.validate_config({"repo": repo})["band"])
        self.assertEqual(balance.validate_config({"repo": repo, "band": {"low": 45, "high": 55}})["band"], {"low": 45, "high": 55})
        for band in ({"low": 55, "high": 45}, {"low": 45}, {"low": True, "high": 55}, {"low": 45, "high": 101},
                     {"low": 45, "high": 55, "x": 1}, [45, 55]):
            with self.subTest(band=band):
                with self.assertRaises(balance.ConfigError):
                    balance.validate_config({"repo": repo, "band": band})

    def test_wilson_interval_known_value(self):
        self.assertEqual(lean.wilson(50, 100), [40.38, 59.62])
        self.assertIsNone(lean.wilson(0, 0))

    def test_lean_is_reported_when_baseline_equals_candidate(self):
        counts = showdown_counts(first=1200, second=800, cowboy=1188)
        receipt = lean.build_receipt(record_for(counts, counts, {"low": 45, "high": 55}), PROVENANCE)
        self.assertTrue(receipt["sameCommit"])
        self.assertEqual(receipt["shift"]["state"], "steady")
        self.assertEqual(receipt["lean"]["verdict"], "lean")
        self.assertEqual(receipt["lean"]["firstPlayer"], {"verdict": "lean", "direction": "first"})
        self.assertEqual(receipt["lean"]["faction"], {"verdict": "lean", "direction": "cowboy"})
        self.assertEqual(receipt["measured"]["candidate"]["firstPlayer"]["share"], 60.0)
        self.assertEqual([f["kind"] for f in receipt["findings"]], ["lean", "lean"])
        self.assertEqual(receipt["cards"]["measured"], False)
        self.assertEqual(receipt["band"]["source"], "operator-configured")

    def test_within_inconclusive_unconfigured_and_shift_are_distinct(self):
        even = showdown_counts(first=1000, second=1000, cowboy=1000)
        self.assertEqual(lean.build_receipt(record_for(even, even, {"low": 45, "high": 55}), PROVENANCE)["lean"]["verdict"], "within")
        near = showdown_counts(first=1110, second=890, cowboy=1000)  # 55.5%, CI crosses 55
        self.assertEqual(lean.build_receipt(record_for(near, near, {"low": 45, "high": 55}), PROVENANCE)["lean"]["verdict"], "inconclusive")
        unset = lean.build_receipt(record_for(near, near, None), PROVENANCE)
        self.assertEqual((unset["lean"]["verdict"], unset["findings"]), ("unconfigured", []))
        # A swing inside the band is a shift finding, not a lean.
        swing = lean.build_receipt(record_for(even, showdown_counts(first=1000, second=1000, cowboy=1000 + 210),
                                              {"low": 30, "high": 70}, cand=SHA_B), PROVENANCE)
        self.assertEqual((swing["shift"]["state"], swing["lean"]["verdict"]), ("alert", "within"))
        self.assertEqual([f["kind"] for f in swing["findings"]], ["shift"])

    def test_finding_keys_are_stable_across_retries_and_commits(self):
        counts = showdown_counts(first=1200, second=800, cowboy=1000)
        first = lean.build_receipt(record_for(counts, counts, {"low": 45, "high": 55}), PROVENANCE)
        retry = record_for(counts, counts, {"low": 45, "high": 55}, cand=SHA_B, base=SHA_B)
        retry["at"] = "2026-11-20T10:00:00Z"
        second = lean.build_receipt(retry, PROVENANCE)
        self.assertEqual([f["key"] for f in first["findings"]], [f["key"] for f in second["findings"]])
        self.assertEqual(len(first["findings"]), 1)

    def test_recheck_rejects_edited_receipts(self):
        counts = showdown_counts(first=1200, second=800, cowboy=1000)
        receipt = lean.build_receipt(record_for(counts, counts, {"low": 45, "high": 55}), PROVENANCE)
        self.assertEqual(lean.recheck(receipt), receipt["findings"])
        for edit in (lambda r: r["samples"]["candidate"].update(first=1300),
                     lambda r: r["lean"].update(verdict="within"),
                     lambda r: r["findings"].append({"key": "x"}),
                     lambda r: r["candidate"].update(commit="main"),
                     lambda r: r.update(mode="lanes; rm -rf /")):
            with self.subTest():
                copy = json.loads(json.dumps(receipt))
                edit(copy)
                with self.assertRaises(ValueError):
                    lean.recheck(copy)


    @unittest.skipUnless(shutil.which("node") and shutil.which("git"), "needs node and git")
    def test_evaluate_cli_writes_receipt_without_touching_saved_state(self):
        rules = (
            "export function createShowdown(seed, {first}) { return {seed, first, result: null}; }\n"
            "export function playTurn(state) { return {state: {...state, result: {winner: state.first, reason: 'x'}}}; }\n"
        )
        with tempfile.TemporaryDirectory(prefix="night-watch-eval-") as directory:
            repo = os.path.join(directory, "repo")
            for name, body in {"package.json": '{"type": "module"}', "src/game/engine.js": "",
                               "prototypes/showdown/rules.js": rules}.items():
                os.makedirs(os.path.dirname(os.path.join(repo, name)), exist_ok=True)
                Path(repo, name).write_text(body)
            git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com"]
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True)
            subprocess.run(git + ["remote", "add", "github", "https://github.com/owner/game.git"], check=True)
            data, out = os.path.join(directory, "data"), os.path.join(directory, "receipt.json")
            with patch.dict(os.environ, {"NIGHT_WATCH_DATA_DIR": data}):
                code = server.main(["evaluate", "--repo", repo, "--candidate", "HEAD", "--count", "40",
                                    "--band-low", "45", "--band-high", "55", "--out", out])
                self.assertEqual(code, 0)
                self.assertEqual(server.load_reports(), [])
                self.assertFalse(os.path.exists(os.path.join(data, "config.json")))
            receipt = json.loads(Path(out).read_text())
        self.assertEqual(receipt["schema"], "night-watch-receipt/1")
        self.assertEqual(receipt["trigger"], "on-demand")
        self.assertEqual(receipt["repository"], "owner/game")
        self.assertTrue(receipt["sameCommit"])
        self.assertEqual(receipt["seeds"], {"from": 0, "to": 39, "deals": 40, "gamesPerDeal": 2, "games": 80})
        self.assertEqual(receipt["samples"]["candidate"]["byStartingSide"]["cowboy"], {"cowboy": 40, "wizard": 0, "draw": 0})
        self.assertEqual(receipt["lean"]["firstPlayer"], {"verdict": "lean", "direction": "first"})
        # 50% over 80 games: the interval is too wide to call it within 45-55.
        self.assertEqual(receipt["lean"]["faction"]["verdict"], "inconclusive")
        self.assertRegex(receipt["candidate"]["rulesBlob"], r"^[0-9a-f]{40}$")
        self.assertEqual(lean.recheck(receipt), receipt["findings"])


class PublishTests(unittest.TestCase):
    def receipt(self, rules="blob-1"):
        counts = showdown_counts(first=1200, second=800, cowboy=1000)
        return lean.build_receipt(record_for(counts, counts, {"low": 45, "high": 55}),
                                  dict(PROVENANCE, candidateRules=rules))

    def fake_gh(self, remote):
        """remote: {number: (state, body)}. Records every argv; refuses anything but view/list/create."""
        calls = []

        def run(argv, **kwargs):
            self.assertIsInstance(argv, list)
            self.assertNotIn("shell", kwargs)
            calls.append(argv)
            verb = argv[1:3]
            if verb == ["issue", "list"]:
                self.assertEqual(argv[argv.index("--state") + 1], "all")
                out = json.dumps([{"number": n, "state": st, "body": b} for n, (st, b) in remote.items()])
            elif verb == ["issue", "view"]:
                number = int(argv[3])
                state, body = remote.get(number, ("CLOSED", ""))
                out = json.dumps({"number": number, "state": state, "body": body})
            elif verb == ["issue", "create"]:
                body = Path(argv[argv.index("--body-file") + 1]).read_text()
                number = 100 + len(remote)
                remote[number] = ("OPEN", body)
                out = "https://github.com/owner/game/issues/%d\n" % number
            else:
                raise AssertionError(argv)
            return subprocess.CompletedProcess(argv, 0, out, "")
        return calls, run

    def test_dry_run_never_calls_gh(self):
        with tempfile.TemporaryDirectory() as folder:
            results = issues.publish(self.receipt(), "owner/game", folder, gh="/bin/gh", dry_run=True,
                                     run=lambda *a, **k: self.fail("dry run called gh"))
        self.assertEqual([r["action"] for r in results], ["would-check-then-create"])
        self.assertIn(issues.marker(results[0]["key"]), results[0]["body"])
        self.assertIn(issues.rules_marker("blob-1"), results[0]["body"])
        self.assertIn("operator-configured", results[0]["body"])

    def test_publish_creates_one_labelled_issue_and_retries_reuse_it(self):
        remote = {7: ("OPEN", "unrelated issue that mentions night-watch-finding")}
        calls, run = self.fake_gh(remote)
        with tempfile.TemporaryDirectory() as folder:
            first = issues.publish(self.receipt(), "owner/game", folder, gh="/bin/gh", dry_run=False, run=run)
            again = issues.publish(self.receipt(), "owner/game", folder, gh="/bin/gh", dry_run=False, run=run)
            # A lost ledger still finds the open issue by its marker.
            os.remove(os.path.join(folder, issues.LEDGER))
            third = issues.publish(self.receipt(), "owner/game", folder, gh="/bin/gh", dry_run=False, run=run)
        self.assertEqual(first[0]["action"], "created")
        self.assertEqual((again[0]["action"], again[0]["number"]), ("exists", first[0]["number"]))
        self.assertEqual((third[0]["action"], third[0]["number"]), ("exists", first[0]["number"]))
        creates = [c for c in calls if c[1:3] == ["issue", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][creates[0].index("--label") + 1], "night-watch")

    def test_closed_finding_is_resolved_until_the_rules_change(self):
        remote = {}
        calls, run = self.fake_gh(remote)
        with tempfile.TemporaryDirectory() as folder:
            first = issues.publish(self.receipt(), "owner/game", folder, gh="/bin/gh", dry_run=False, run=run)
            number = first[0]["number"]
            remote[number] = ("CLOSED", remote[number][1])  # a maintainer resolves or dismisses it
            retry = issues.publish(self.receipt(), "owner/game", folder, gh="/bin/gh", dry_run=False, run=run)
            os.remove(os.path.join(folder, issues.LEDGER))
            lost = issues.publish(self.receipt(), "owner/game", folder, gh="/bin/gh", dry_run=False, run=run)
            changed = issues.publish(self.receipt(rules="blob-2"), "owner/game", folder, gh="/bin/gh", dry_run=False, run=run)
        self.assertEqual((retry[0]["action"], retry[0]["number"]), ("resolved", number))
        self.assertEqual((lost[0]["action"], lost[0]["number"]), ("resolved", number))
        self.assertEqual(changed[0]["action"], "created")
        self.assertEqual(len([c for c in calls if c[1:3] == ["issue", "create"]]), 2)

    def test_issues_never_assign_kiro_web(self):
        calls, run = self.fake_gh({})
        with tempfile.TemporaryDirectory() as folder:
            issues.publish(self.receipt(), "owner/game", folder, gh="/bin/gh", dry_run=False, run=run)
        for argv in calls:
            self.assertNotIn("kiro", [argv[i + 1] for i, a in enumerate(argv) if a == "--label"])
            self.assertNotIn("comment", argv)
            self.assertNotIn("edit", argv)
        for text in ("text\n /kiro please fix\n", "see /kiro", "/kiro fix", "x\t/kiro."):
            with self.assertRaises(issues.PublishError):
                issues._no_web_trigger(text)
        for text in ("/kirocrew", "src/kiro/index.ts", "path/kiro"):
            self.assertEqual(issues._no_web_trigger(text), text)
        self.assertIn("kiro", issues.WEB_TRIGGER_LABELS)
        self.assertNotIn(issues.LABEL, issues.WEB_TRIGGER_LABELS)

    def test_publish_refuses_bad_repo_edited_receipt_and_too_many_findings(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(issues.PublishError):
                issues.publish(self.receipt(), "owner/game; echo", folder, dry_run=True)
            edited = self.receipt()
            edited["samples"]["candidate"]["cowboy"] += 1
            with self.assertRaises(issues.PublishError):
                issues.publish(edited, "owner/game", folder, dry_run=True)
            with self.assertRaises(issues.PublishError):
                issues.publish(self.receipt(), "owner/game", folder, dry_run=True, limit=0)


class ScheduledIssueTests(unittest.TestCase):
    """The scheduled job is read-only unless the saved setting opts in; then review gates every issue."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"NIGHT_WATCH_DATA_DIR": self.folder.name})
        self.env.start()
        counts = showdown_counts(first=1200, second=800, cowboy=1000)
        record = record_for(counts, counts, {"low": 45, "high": 55})
        record.update(id="r1", source="scheduled",
                      receipt=lean.build_receipt(record, dict(PROVENANCE, candidateRules="blob-1")))
        self.record = record
        server.write_json("history.json", [record])
        self.config = {"mode": "showdown", "band": {"low": 45, "high": 55}, "autoIssues": "lean", "issueRepo": ""}

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def test_default_is_read_only(self):
        result = server.scheduled_issues(self.record, dict(self.config, autoIssues="off"),
                                         review=lambda r: self.fail("review ran"), run=lambda *a, **k: self.fail("gh ran"))
        self.assertEqual(result, {"mode": "off"})
        self.assertEqual(balance.DEFAULT_CONFIG["autoIssues"], "off")

    def test_unreproduced_finding_is_not_published(self):
        result = server.scheduled_issues(self.record, self.config, gh="/bin/gh",
                                         review=lambda r: (False, "not reproduced (exit 1)"),
                                         run=lambda *a, **k: self.fail("gh ran"))
        self.assertEqual(result["state"], "not-published")

    def test_reproduced_finding_is_filed_once_across_scheduled_retries(self):
        remote = {}
        calls, run = PublishTests.fake_gh(self, remote)
        review = lambda r: (True, "reproduced all 2000 games")
        first = server.scheduled_issues(self.record, self.config, gh="/bin/gh", review=review, run=run)
        again = server.scheduled_issues(self.record, self.config, gh="/bin/gh", review=review, run=run)
        self.assertEqual([i["action"] for i in first["issues"]], ["created"])
        self.assertEqual([i["action"] for i in again["issues"]], ["exists"])
        self.assertEqual(first["repository"], "owner/game")
        body = next(iter(remote.values()))[1]
        self.assertIn("Independent reproduction (scripts/night-review.mjs check): reproduced all 2000 games", body)
        server.attach_publication(self.record, again)
        self.assertEqual(server.load_reports()[0]["publication"]["issues"][0]["action"], "exists")

    def test_missing_reviewer_fails_closed(self):
        with patch.object(server, "REVIEWER", "/nonexistent/night-review.mjs"):
            self.assertEqual(server.review_finding(self.record, node="/usr/bin/true")[0], False)

    def test_opt_in_needs_showdown_and_band(self):
        repo = git_checkout_fixture(self)
        for values in ({"autoIssues": "lean"}, {"autoIssues": "lean", "band": {"low": 45, "high": 55}, "mode": "lanes"},
                       {"autoIssues": "yes"}, {"issueRepo": "a b"}):
            with self.subTest(values=values):
                with self.assertRaises(balance.ConfigError):
                    balance.validate_config({"repo": repo, **values})
        ok = balance.validate_config({"repo": repo, "mode": "showdown", "band": {"low": 45, "high": 55}, "autoIssues": "lean"})
        self.assertEqual(ok["autoIssues"], "lean")


class HostGuardTests(unittest.TestCase):
    def request(self, port, host, path="/health", extra=None):
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            conn.putheader("Host", host)
        for name, value in (extra or {}).items():
            conn.putheader(name, value)
        conn.endheaders()
        status = conn.getresponse().status
        conn.close()
        return status

    def test_host_guard_uses_actual_port(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {"NIGHT_WATCH_DATA_DIR": data}):
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                for host in ("127.0.0.1:%d" % port, "localhost:%d" % port):
                    for path in ("/health", "/apps/" + server.APP_ID + "/api/health", "/api/state"):
                        with self.subTest(host=host, path=path):
                            self.assertEqual(self.request(port, host, path), 200)
                for host in ("evil.domain", "evil.domain:%d" % port, "127.0.0.1:%d" % (port + 1), "127.0.0.1", None):
                    with self.subTest(host=host):
                        self.assertEqual(self.request(port, host), 403)
                self.assertEqual(self.request(port, "evil.domain:%d" % port, extra={"X-Forwarded-Host": "localhost:%d" % port}), 403)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()

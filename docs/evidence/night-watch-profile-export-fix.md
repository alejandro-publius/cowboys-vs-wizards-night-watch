# Night Watch profile export fix

Observed November 16, 2026 (America/Los_Angeles; November 17 UTC) from Kiro CLI V3 on branch `kiro/crew-verification`. Raw output is in ignored `.artifacts/profile-export-fix/`.

## Bug

The standalone `backend/balance.py` extracts a fixed list of game paths. At game commit `599450a27703fbdb8961d626c3e779512f54f29e`, `tests/game/player-profile.test.js` imports `src/profile/player-profile.js`, which was not extracted. The earlier run (historical, see [crew-manual-verification.md](crew-manual-verification.md) and its [receipt](crew-manual-verification/standalone-backend-smoke.json), unchanged) passed 52 of 53 rules tests and ran no simulation.

## Fix

The final version extracts `src/profile` when the directory exists. Kiro initially added it to `REQUIRED_PATHS` in `489e13b`; Codex review moved it to `OPTIONAL_PATHS` so older game revisions without profiles remain valid baselines. Ref and allowed-repo checks, archive safety checks and the other paths are unchanged. No dependency or API changes.

The following hashes describe Kiro's initial change, before the compatibility correction:

| File | SHA-256 before | SHA-256 after |
| --- | --- | --- |
| `backend/balance.py` | `d4ad1154b8cd37bc842ff86bd6719dfd85e1400f57a9ee6433bfce125d0f4428` | `4c8047e5dc96d891182f7318ba5fb4b2c69863262d49c504cf5d1cba8e9e8bdf` |
| `backend/test_night_watch.py` | `f0efbc3d005fe4bb0581b686827eae7188f67fbb396adce545a0b39932f2e069` | `af411350a0f73c1b3ccaaaca576b6f5789ff29684cd7d7625e41030498c7c28e` |

## Regression test

`test_extracted_snapshot_runs_game_test_that_imports_profile_module` builds a minimal Git repo with a game test that imports `src/profile`, calls `extract_snapshot` and `run_game_tests`, and expects the test to pass. It failed with the old `balance.py` and passes with the fix. `python3 -m unittest test_night_watch` in `backend/`: 5 tests, OK.

## Backend smoke (after)

One run of `python3 backend/server.py run` with an isolated `NIGHT_WATCH_DATA_DIR`, game root `cowboys-vs-wizards-board`, baseline and candidate both `599450a27703fbdb8961d626c3e779512f54f29e`, seeds 0 to 19, threshold 10. Receipt: [standalone-backend-smoke.json](night-watch-profile-export-fix/standalone-backend-smoke.json).

- Exit 0, state `steady`, report `1e6bbc441c79`.
- Rules passed, 64 tests. Simulation completed.
- Baseline and candidate counts are identical: Cowboy 14, Wizard 4, draw 2, first seat 13, second seat 5.

## Scope

This shows the standalone backend passes. It does not show the Crew UI works. The installed Night Watch app remains disabled by the app-scoped execution policy (HTTP 403 `app_not_enabled`), which does not mean the Crew browser is globally disabled. No new Crew GUI run or schedule was made or is claimed. The coordinator will mirror the backend fix into the game app and publish a sanitized standalone diff later. No issue was published.

## Codex compatibility review

The new legacy fixture failed against Kiro's initial required-path change with `CheckError: ... has no src/profile`. Codex made profile extraction optional when absent and added a second real Git-fixture test. Both fixtures call the actual archive extraction and then run a Node test importing the exported module: one imports the profile, the older fixture imports only its engine and has no profile directory. No path-list-only assertion or mocked exporter is used.

`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_night_watch` in `backend/`: **6 tests passed**, including both extraction/import fixtures. Original before/after output is retained in ignored `.artifacts/profile-export-fix/codex-legacy-before.txt` and `codex-review-tests.txt`. The 20-seed smoke above was the actual Kiro run before this compatibility correction; it was not repeated or relabelled as a Codex run.

Final file SHA-256:

- `backend/balance.py`: `930d273bab257cd538515dc838fd17a74e62fdfe362740851ada531eb868a892`
- `backend/test_night_watch.py`: `a73ede86698df6171bbbf5ce9e5a37e873450aef321b07e02e4f998d8f6e7d3d`

# Night Watch

Night Watch runs inside the Kiro Crew dashboard. Compare Cowboys vs. Wizards revisions on the same CPU match seeds, see faction and starting-seat win shares, and draft a GitHub issue when a run crosses the alert threshold or fails a check.

This repository is a standalone mirror of [`apps/night-watch`](https://github.com/alejandro-publius/cowboys-vs-wizards/tree/7fdc234a59bace2a809a77c188fb193e0846dea1/apps/night-watch) in the Cowboys vs. Wizards game repository. The source is game commit `471e3604f954970bf97d79ee93a6362edaaf2755`, a single test-only commit on top of `7fdc234a59bace2a809a77c188fb193e0846dea1`; at the time of this sync it is on the game branch `kiro/night-watch-standalone-tests` and not yet merged, so the links here point to `7fdc234`. The app runtime and tests here are byte-identical to the source commit; [SOURCE.json](SOURCE.json) lists every file's Git blob hash. This README, `SOURCE.json`, `.gitignore` and `docs/evidence/` are specific to the mirror. Night Watch does not contain the game. It reads the committed rules engine from a separate local game checkout.

## Verify the standalone extension

Run the dependency-free Python tests and the JavaScript syntax checks from this **repository root**:

~~~sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s backend -p 'test_*.py' -v
node --check ui/index.mjs
node --check backend/showdown_sim.mjs
~~~

The suite has 29 tests and needs `git` and `node` on PATH. All 29 pass from a standalone clone. The configuration tests build their own tiny temporary Git checkout, so they no longer depend on where the app sits. The same files also pass in the game repository, run from its root with `-s apps/night-watch/backend`.

The checks cover configuration boundaries, alert thresholds, isolated report storage, Showdown parsing and a two-seat runner fixture, lean intervals, receipt rechecks, issue publishing with a mocked `gh`, scheduled-issue gating and the Host header guard. They do not substitute for a full Crew browser installation or a scheduled-job run.

## Install

Use Kiro Crew 0.12.2 or later, Git, Python 3.9 or later, and Node 22.18 or later. Install from this repository root on macOS or Linux.

~~~sh
kirocrew app install "$PWD"
~~~

In Crew’s Library, find **Night Watch**, review its install source and enable it. If Crew asks for execution trust, choose **Trust this app alone**. Open **Night Watch** in Crew. Under **Settings**, enter the absolute path to a local Cowboys vs. Wizards checkout. Choose baseline and candidate branches, tags or commits that contain the playable engine. Use a fixed baseline and a working branch as the candidate to track new commits. Save the settings and choose **Run balance check**.

Choose a **Mode**. **Showdown** plays each deal twice, once with each faction moving first, through the commit's `prototypes/showdown/rules.js` and its level-2 bot, the same loop as `prototypes/showdown/sim.mjs`. Its report shows the first-player share and the Cowboy share over all games (two per deal). **Lane duel** runs `scripts/simulate-matches.mjs`. Lane duel remains the default for manual runs; the nightly job always uses Showdown.

Night Watch resolves the refs to commits, runs the candidate's game-rule tests and replays the chosen seed range for both commits. It tests the committed code from the checkout on this computer. Reports show the exact revisions, seed range, outcome shifts and any card-spec changes. The app keeps the 20 latest reports.

The defaults compare seeds 0–999 (deals in Showdown) and flag a faction win-share shift of at least 10 percentage points or a first-seat advantage that grows by at least that much. Results describe these CPU policies and seeds. A rules failure is recorded with diagnostic output. When the checkout has a GitHub origin, eligible reports include an **Open GitHub issue draft** link with the run details.

## Target band (lean) and receipts

The shift check above is relative: it compares the candidate with the baseline. A lean check is absolute: it compares the candidate's first-player share and Cowboy share (over decisive games) with a **target band** you set, for example 45–55%. It runs even when baseline and candidate are the same commit. No design target has been approved, so the band is empty by default and the lean verdict reads `unconfigured`.

Each share carries a Wilson 95% interval and its sample size. A metric is `within` when its whole interval lies inside the band, `lean` (toward first/second or cowboy/wizard) when its whole interval lies outside, and `inconclusive` otherwise. Games are treated as independent, an approximation, since each Showdown deal is played from both seats. No per-card data is measured.

Every report stores a `night-watch-receipt/1` object under `receipt`: full commit SHAs, the rules blob, seed range, raw two-seat counts, shares and intervals, band, lean and shift verdicts, runner hash and commands that reproduce the counts. For a one-off check that leaves the saved settings and history alone, run this from the repository root:

~~~sh
python3 backend/server.py evaluate --repo /path/to/checkout --candidate origin/main \
  --first-seed 0 --count 1000 --band-low 45 --band-high 55 --out receipt.json
~~~

## Publish findings as GitHub issues (opt-in)

~~~sh
python3 backend/server.py issues --receipt receipt.json            # dry run: prints each draft
python3 backend/server.py issues --receipt receipt.json --publish  # creates issues with gh
~~~

`--report latest` (or a report id) reads a stored report instead of a file. Each finding (a lean metric or a shift alert) becomes at most one issue labelled `night-watch`. Night Watch finds it again by the `<!-- night-watch-finding:KEY -->` marker in any state. An open issue is reused. A closed issue that also holds the same `<!-- night-watch-rules:BLOB -->` marker counts as resolved and is not filed again, so retries and closed findings never duplicate. Only a lean that persists after the rules file changed opens a new issue. The lean key leaves out the commit, so a lean that persists across commits keeps one issue. Night Watch recomputes the verdicts from the receipt's counts before drafting, builds titles and bodies from those numbers only, and runs `gh` with an argument list. The repository must already have a `night-watch` label.

Night Watch never uses the `kiro` label or a `/kiro` line, which assign Kiro Web ([docs](https://kiro.dev/docs/web/github/)). It never reads, comments on or edits an issue after filing it, so a filed issue cannot start an agent or another run.

### Scheduled issues (opt-in project setting)

The scheduled job is read-only by default (`autoIssues: "off"`). Set **Scheduled issues** to *One issue per reproduced out-of-band lean* (`autoIssues: "lean"`, which needs Showdown mode and a target band) to let each scheduled run file issues. Optionally set **Issue repository** (`issueRepo`, owner/name). Then, after each scheduled run:

1. Only lean findings count (the whole 95% interval outside the band). Shift alerts stay as draft links.
2. The Night Shift reviewer re-runs the finding's counts from a fresh `git archive` snapshot with `node scripts/night-review.mjs check --finding <report>` and a 120 s limit. If the reviewer is missing, times out or gets different counts, nothing is filed.
3. At most two issues per run, deduplicated as above. The run's report records the outcome under `publication`.

The reviewer is not part of this mirror. `backend/server.py` looks for it at `../../scripts/night-review.mjs` relative to the app root, which is the game repository's `scripts/` folder when the app is installed from `apps/night-watch` in a game checkout. Installed from a standalone clone, the reviewer is missing, so scheduled issue filing stays off even when opted in; the run's report records `reviewer unavailable` under `publication`. Manual `issues --publish` does not use the reviewer.

Choosing a card and a number, and any change to cards or rules, stays with Fable/Alex through `node scripts/night-review.mjs run …` in the game repository (see its [`docs/evidence/night-shift-review.md`](https://github.com/alejandro-publius/cowboys-vs-wizards/blob/7fdc234a59bace2a809a77c188fb193e0846dea1/docs/evidence/night-shift-review.md)). Nothing is merged automatically.

## Schedule a nightly check

Save the settings and verify one manual run first. From this repository root, register a Kiro Crew command job.

~~~sh
python3 backend/server.py schedule --time 02:00 --timezone America/Los_Angeles
~~~

The job runs `server.py run --scheduled --mode showdown`, so nightly checks play Showdown whatever mode the manual settings hold. It files issues only when Scheduled issues is on and the reviewer is available (see above). Run `python3 backend/server.py run --mode lanes` for a one-off lane comparison.

Crew runs the scheduled command locally. It records a report in the same history as manual runs, with **Scheduled** as the trigger. Manage the job in Crew’s **Schedule** panel. To remove it from the terminal, use this command.

~~~sh
python3 backend/server.py schedule --remove
~~~

The app and job share the local Night Watch data directory. Keep the computer and Crew running for scheduled checks. Reload Night Watch to load reports created by the schedule. The checkout and its Git refs must remain accessible to the computer running Crew. The scheduled command does not fetch remote refs; fetch the checkout when you want origin/main to reflect newer published commits. The NIGHT_WATCH_NODE environment variable can select an absolute Node executable when the launcher uses a different PATH.

## Source and attribution

Synchronized from [Cowboys vs. Wizards](https://github.com/alejandro-publius/cowboys-vs-wizards/tree/7fdc234a59bace2a809a77c188fb193e0846dea1/apps/night-watch), the companion game repository, at commit `471e3604f954970bf97d79ee93a6362edaaf2755` (app version 0.2.0; game commit `7fdc234` plus the layout-independent test fixture). The first standalone export was taken from commit `599450a27703fbdb8961d626c3e779512f54f29e` (version 0.1.x). Keep the game checkout available separately: Night Watch compares its committed rules engine and does not embed game art or models.

Kiro CLI wrote the balance adapter and HTTP backend. Codex wrote the dashboard and schedule integration, fixed a tribute test in the game, and prepared the first standalone package. Showdown mode, lean receipts, issue publishing and the Host header guard were added later in the game repository; see its history for `apps/night-watch`. Kiro CLI synchronized this mirror to the source commit. Actual Crew use is documented in the [game evidence](https://github.com/alejandro-publius/cowboys-vs-wizards/blob/7fdc234a59bace2a809a77c188fb193e0846dea1/docs/evidence/kiro-night-watch.md). Updating this mirror is not a new live Crew run.

This project was built for Kiroween 2026. It is an optional local developer tool, separate from the playable browser game.

## Verification and limits

For this synchronization, every runtime and test file's Git blob hash matches source commit `471e360`. From the root of a fresh clone of this mirror, all 29 tests pass, including the two historical profile-extraction fixtures. The same files at `apps/night-watch` in the game checkout pass all 29. `node --check` passes for `ui/index.mjs` and `backend/showdown_sim.mjs`. No Crew install, UI run, balance run or schedule was performed for this update, and none is claimed.

Issue links open a prefilled GitHub draft. The app files issues only through the opt-in `issues --publish` command or opt-in scheduled issues gated by the reviewer. Scheduling remains opt-in using the documented command after a successful manual run.

## Historical verification (earlier versions)

These records describe earlier mirror versions and were not repeated for 0.2.0.

- **0.2.0 first sync (mirror commit `3d6ce54`, from game `7fdc234`).** Three configuration tests read the folder two levels above the app as the game checkout, so from a standalone clone 26 of 29 passed and 3 errored with `ConfigError ... is not a Git checkout`; all 29 passed inside the game layout. The current test fixture fixes this.

- **0.1.1, November 16, 2026.** The first standalone export passed four Python tests and the UI syntax check.
- **0.1.2.** Kiro found and fixed a missing profile-module export, and Codex kept older baselines without profiles valid. Six Python checks passed, including real archive/import fixtures, and Kiro completed a separate 20-seed backend run at `599450a`. [Fix evidence](docs/evidence/night-watch-profile-export-fix.md). The fix is now part of the upstream app: `balance.py` extracts `src` as an optional path, and both Git fixtures are in the current test file unchanged.
- At that time the installed 0.1.1 copy was disabled by Crew's app execution policy. [Observed Crew status](docs/evidence/crew-manual-verification.md). Later Crew observations are in the game evidence, not in this mirror.

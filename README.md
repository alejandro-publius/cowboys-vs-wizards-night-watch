# Night Watch

Night Watch runs inside the Kiro Crew dashboard. Compare Cowboys vs. Wizards revisions on the same CPU match seeds, see faction and starting-seat win shares, and draft a GitHub issue when a run crosses the alert threshold or fails a check.

## Verify the standalone extension

Run the small dependency-free Python tests from this **app repository root** before integrating changes to Night Watch:

~~~sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s backend -p 'test_*.py' -v
node --check ui/index.mjs
~~~

The checks cover configuration boundaries, alert thresholds and isolated report storage. They do not substitute for a full Crew browser installation or a scheduled-job run.

## Install

Use Kiro Crew 0.12.2 or later, Git, Python 3.9 or later, and Node 22.18 or later. Install from this directory on macOS or Linux.

~~~sh
kirocrew app install "$PWD"
~~~

In Crew’s App Store, find **Night Watch**, review its install source and enable it. If Crew asks for execution trust, choose **Trust this app alone**. Open **Night Watch** in Crew. Under **Settings**, enter the absolute path to a local Cowboys vs. Wizards checkout. Choose baseline and candidate branches, tags or commits that contain the playable engine. Use a fixed baseline and a working branch as the candidate to track new commits. Save the settings and choose **Run balance check**.

Night Watch resolves the refs to commits, runs the candidate's game-rule tests and replays the chosen seed range for both commits. It tests the committed code from the checkout on this computer. Reports show the exact revisions, seed range, outcome shifts and any card-spec changes. The app keeps the 20 latest reports.

The defaults compare seeds 0–999 and flag a faction win-share shift of at least 10 percentage points or a first-seat advantage that grows by at least that much. Results describe these CPU policies and seeds. A rules failure is recorded with diagnostic output. When the checkout has a GitHub origin, eligible reports include an **Open GitHub issue draft** link with the run details.

## Schedule a nightly check

Save the settings and verify one manual run first. From this app directory, register a Kiro Crew command job.

~~~sh
python3 backend/server.py schedule --time 02:00 --timezone America/Los_Angeles
~~~

Crew runs the scheduled command locally. It records a report in the same history as manual runs, with **Scheduled** as the trigger. Manage the job in Crew’s **Schedule** panel. To remove it from the terminal, use this command.

~~~sh
python3 backend/server.py schedule --remove
~~~

The app and job share the local Night Watch data directory. The checkout and its Git refs must remain accessible to the computer running Crew. The NIGHT_WATCH_NODE environment variable can select an absolute Node executable when the launcher uses a different PATH.

## Source and attribution

Extracted from [Cowboys vs. Wizards](https://github.com/alejandro-publius/cowboys-vs-wizards/tree/599450a27703fbdb8961d626c3e779512f54f29e/apps/night-watch), the companion game repository, at commit 599450a. Keep the game checkout available separately: Night Watch compares its committed rules engine and does not embed game art or models.

Kiro CLI wrote the balance adapter and HTTP backend. Codex wrote the dashboard and schedule integration, fixed a tribute test in the game, and prepared this standalone package. Actual Crew use is documented in the [game evidence](https://github.com/alejandro-publius/cowboys-vs-wizards/blob/main/docs/evidence/kiro-night-watch.md). Packaging this repository is not a new live Crew run.

This project was built for Kiroween 2026. It is an optional local developer tool, separate from the playable browser game.

## Verification and limits

The standalone export passed all four dependency-free Python tests and the UI JavaScript syntax check on November 16, 2026. The tests cover configuration bounds, thresholds and isolated report storage. They do not prove current Crew installation, a continuous nightly schedule or human gameplay balance.

Issue links open a prefilled GitHub draft; this app does not silently file public issues. Scheduling remains opt-in using the documented command after a successful manual run.

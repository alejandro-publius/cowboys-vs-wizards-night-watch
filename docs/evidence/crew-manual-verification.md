# Night Watch Crew manual verification

Observed 2026-11-16 (local time) from Kiro CLI 3.3.0 (not the IDE), launched by Codex. Crew is Kiro Crew 0.12.2 (`kirocrew --version`). Codex and Kiro Crew attributions in the README are unchanged. Historical claims are in the [game evidence](https://github.com/alejandro-publius/cowboys-vs-wizards/blob/main/docs/evidence/kiro-night-watch.md) and are not repeated here.

## Historical check before the export fix

This records the initial failure. The [subsequent fix and successful backend run](night-watch-profile-export-fix.md) supersede the backend failure below; the live Crew policy blocker remains.

## Result

**The manual Crew run did not happen.** Crew refused to enable Night Watch, so no report was produced inside Crew and there is no visible-UI proof. A separate backend-only smoke, outside Crew, failed the rule check. This is not a balance result.

## 1. Installed app (new observations)

| Item | Observed |
| --- | --- |
| Gateway | Running, `kirocrew status` uptime 10h 12m, dashboard `localhost:5476` |
| App | `kiroween-night-watch` 0.1.1, status **disabled** (`kirocrew app list`) |
| Install source | `.../cowboys-vs-wizards-board/.worktrees/night-watch/apps/night-watch` (the original game worktree, origin `local`), **not** this standalone export |
| Installed/updated | 2026-11-16T17:02:23Z / 17:20:02Z |

Installed copy (`~/.kiro/crew/apps/kiroween-night-watch`) versus the original game source versus this export, by SHA-256 of each file:

- Installed copy and original game source are identical (`LICENSE`, `README.md`, `app.json`, `backend/*`, `ui/*`). Crew-added files (`installed.json`, approved grants, a secret file, `data/`) were excluded and the secret was never read.
- This export has identical `app.json`, backend source and `ui/*` to the installed copy. It differs only by `LICENSE`/`README.md` (standalone listing) and by the added `backend/test_night_watch.py`. I did not re-hash the installed `LICENSE`/`README.md` against the export.
- The standalone export has **not** been installed. I did not replace the original installation. Hash lists are in ignored `.artifacts/crew-verification/*.sha`.

## 2. Enabling and the visible UI

- `kirocrew app enable kiroween-night-watch` was refused: "blocked by execution policy: third-party app execution is disabled ... trust this app alone from the dashboard's App Store". Trusting an app is a user security decision, so I did not set `agent.apps_allow_third_party` and did not trust it.
- `GET /apps/kiroween-night-watch/api/state` with a dashboard token returned `{"code": "app_not_enabled"}` (HTTP 403). The token is not recorded.
- I had no browser tools. One headless attempt with Crew's bundled Chrome for Testing 154 hung for the 2 minute limit and produced no screenshot, so I stopped. **No initial or final screenshots exist.** No visual UI claim is made.

## 3. Settings (preserved)

Existing app settings, unchanged byte for byte (verified with `cmp` against copies in `.artifacts/crew-verification/pre/`): repo = the game checkout, baseline `2d4867a`, candidate `codex/kiroween-submission-preflight`, seeds 0 to 999, threshold 10. The existing history holds one earlier manual report (`f3d4b55c95af`, steady, 41 rule tests). That is retained history from before this task, not a new run. The installed app's Crew-side data was not written.

## 4. Backend-only smoke (not Crew)

Because Crew was blocked, I ran the standalone export's own CLI once, with an isolated `NIGHT_WATCH_DATA_DIR` under `.artifacts/crew-verification/isolated-data`, seeds 0 to 19, baseline and candidate both `599450a27703fbdb8961d626c3e779512f54f29e`, trigger manual. Receipt: [standalone-backend-smoke.json](crew-manual-verification/standalone-backend-smoke.json).

- Resolved SHAs: both `599450a27703fbdb8961d626c3e779512f54f29e`.
- Rule check: **failed**. 52 of 53 game tests passed. `tests/game/player-profile.test.js` imports `src/profile/player-profile.js`, which exists in the commit but is not extracted. `backend/balance.py` exports only `package.json`, `src/game`, two scripts, `tests/game` and the card specs.
- Because rules failed, no simulation ran: no seed results or win counts.
- Retained history: one entry in the isolated store only.
- Issue draft: a prefilled GitHub link was saved with the failed report. It was not opened or submitted.
- Meaning: Night Watch as packaged does not work against game commit 599450a. The earlier passing run was on commit `2d4867a`/the preflight branch. I made no source change. Fixing it needs an app edit (include `src/profile`, or the packaged test set should match), outside this task.

## 5. Schedule (prepared, not run)

No schedule was registered. The `schedule` command records the path of the `server.py` it is run from. For the installed copy:

```sh
python3 ~/.kiro/crew/apps/kiroween-night-watch/backend/server.py schedule --time 02:00 --timezone America/Los_Angeles
python3 ~/.kiro/crew/apps/kiroween-night-watch/backend/server.py schedule --remove
```

Do not schedule until a manual Crew run passes. The job is `kirocrew cron add ... --managed-by app:kiroween-night-watch/balance`.

## Remaining manual steps

1. In Crew's App Store, trust and enable Night Watch (user decision), or install this export deliberately.
2. Install the fixed 0.1.2 package, then run one manual check in the UI and capture screenshots.
3. Review the schedule separately.

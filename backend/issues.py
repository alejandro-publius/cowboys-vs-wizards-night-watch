"""Opt-in GitHub issue publication for Night Watch findings.

One issue per finding key, labelled `night-watch`. The `kiro` label and `/kiro`
comment command assign Kiro Web, so Night Watch never uses either: an issue it
files cannot start an agent, and Night Watch never reads, comments on or edits an
issue after creating it, so nothing loops. Titles and bodies are built from the
receipt's recomputed numbers and commits only. Text read back from GitHub is
compared with marker strings and never placed in a command. `gh` is run with an
argument list, never through a shell.

Deduplication covers retries and closed issues: an open issue holding the
finding marker is reused; a closed one holding the finding marker and the same
rules-blob marker counts as resolved and is not filed again. Only a lean that
persists after the rules file itself changed can open a new issue.
"""

import json
import os
import re
import subprocess
import tempfile

import lean

LABEL = "night-watch"
# Labels and comment commands that assign Kiro Web (https://kiro.dev/docs/web/github/). Never used.
WEB_TRIGGER_LABELS = frozenset(["kiro"])
WEB_TRIGGER = re.compile(r"(?:^|\s)/kiro(?![A-Za-z0-9_])", re.IGNORECASE)
LEDGER = "published.json"
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")
TIMEOUT = 30


class PublishError(Exception):
    """Publication stopped. Nothing further is posted."""


def marker(key):
    return "<!-- night-watch-finding:%s -->" % key


def rules_marker(blob):
    return "<!-- night-watch-rules:%s -->" % blob


def _no_web_trigger(text):
    """Refuse text Kiro Web would read as a /kiro command: at the start or after whitespace, not followed by [A-Za-z0-9_]."""
    if WEB_TRIGGER.search(text):
        raise PublishError("The draft contains a /kiro command line. Night Watch never assigns Kiro Web.")
    return text


def _pct(value):
    return "n/a" if value is None else "%.2f%%" % value


def _ci(metric):
    return "n/a" if not metric["ci95"] else "%.2f-%.2f%%" % tuple(metric["ci95"])


def issue_body(receipt, finding, review=None):
    cand, base, seeds = receipt["candidate"], receipt["baseline"], receipt["seeds"]
    measured, samples = receipt["measured"]["candidate"], receipt["samples"]["candidate"]
    lines = [
        "Night Watch finding from a fixed-seed CPU vs. CPU run. These are estimates for this bot and seed range,"
        " not human play.",
        "",
        "- Kind: %s" % finding["kind"],
        "- Candidate: %s" % cand["commit"],
        "- Baseline: %s%s" % (base["commit"], " (same commit)" if receipt["sameCommit"] else ""),
        "- Mode: %s; bot: %s" % (receipt["mode"], receipt["bot"]),
        "- Seeds: %d-%d, %d deals x %d = %d games" % (
            seeds["from"], seeds["to"], seeds["deals"], seeds["gamesPerDeal"], seeds["games"]),
        "- Candidate counts: %d Cowboy, %d Wizard, %d draws; %d first-player wins, %d second-player wins" % (
            samples["cowboy"], samples["wizard"], samples["draw"], samples["first"], samples["second"]),
        "- First-player share: %s (95%% CI %s, n=%d decisive)" % (
            _pct(measured["firstPlayer"]["share"]), _ci(measured["firstPlayer"]), measured["firstPlayer"]["decisive"]),
        "- Cowboy share: %s (95%% CI %s, n=%d decisive)" % (
            _pct(measured["cowboy"]["share"]), _ci(measured["cowboy"]), measured["cowboy"]["decisive"]),
    ]
    if receipt["band"]:
        lines.append("- Target band: %g-%g%% (operator-configured, not an approved design target)" % (
            receipt["band"]["low"], receipt["band"]["high"]))
    lines.append("- Lean verdict: %s; shift verdict: %s (threshold %g pp)" % (
        receipt["lean"]["verdict"], receipt["shift"]["state"], receipt["shift"]["threshold"]))
    lines.append("- Per-card data: not measured")
    if review:
        lines.append("- Independent reproduction (scripts/night-review.mjs check): %s" % review)
    lines += ["", "Reproduce:", ""]
    lines += ["    " + command for command in receipt["commands"]]
    lines += ["", "Night Shift review (local CLI, by hand; Fable/Alex choose the card and number, nothing is merged):", "",
              "    node scripts/night-review.mjs run --finding <night-watch report.json> --card <card> --field <attack|health|stars> --to <n>",
              "", "Night Watch does not read, comment on or edit this issue after filing it.",
              "", marker(finding["key"]), rules_marker(receipt["candidate"].get("rulesBlob") or "unknown")]
    return _no_web_trigger("\n".join(lines) + "\n")


def plan(receipt, kinds=None, review=None):
    """Recompute the findings from the counts, then draft one issue per finding (optionally only some kinds)."""
    try:
        found = lean.recheck(receipt)
    except (KeyError, TypeError, ValueError) as error:
        raise PublishError("The receipt was not accepted: %s" % error)
    return [{"key": f["key"], "kind": f["kind"], "title": _no_web_trigger(f["title"]), "body": issue_body(receipt, f, review)}
            for f in found if kinds is None or f["kind"] in kinds]


def read_ledger(folder):
    try:
        with open(os.path.join(folder, LEDGER), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except ValueError:
        raise PublishError("The publication ledger %s is damaged. Move it aside first." % os.path.join(folder, LEDGER))


def write_ledger(folder, ledger):
    handle, temp = tempfile.mkstemp(dir=folder, prefix=LEDGER + ".", suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(ledger, out, indent=2, sort_keys=True)
        out.write("\n")
    os.replace(temp, os.path.join(folder, LEDGER))


def gh_env():
    env = {"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "LC_ALL": "C.UTF-8"}
    for key in ("PATH", "HOME", "TMPDIR", "GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def run_gh(gh, args, run=subprocess.run):
    try:
        result = run([gh] + args, env=gh_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublishError("gh could not complete %s (%s)." % (args[:2], type(error).__name__))
    if result.returncode != 0:
        raise PublishError("gh %s failed: %s" % (" ".join(args[:2]), (result.stderr or "").strip()[-400:]))
    return result.stdout


def existing_issue(gh, slug, key, rules, number, run):
    """("open", n) for an open issue with this finding's marker; ("resolved", n) for a closed one that also
    holds this rules blob's marker; None otherwise. Searches every state, not only the label."""
    wanted, same_rules = marker(key), rules_marker(rules)
    candidates = []
    if number is not None:
        candidates.append(json.loads(run_gh(gh, ["issue", "view", str(number), "--repo", slug, "--json", "number,state,body"], run)))
    candidates += json.loads(run_gh(gh, ["issue", "list", "--repo", slug, "--state", "all",
                                         "--search", key + " in:body", "--json", "number,state,body", "--limit", "50"], run))
    resolved = None
    for item in candidates:
        body = item.get("body") or ""
        if wanted not in body:
            continue
        if item.get("state") == "OPEN":
            return "open", item["number"]
        if same_rules in body and resolved is None:
            resolved = item["number"]
    return ("resolved", resolved) if resolved is not None else None


def publish(receipt, slug, folder, gh=None, dry_run=True, run=subprocess.run, kinds=None, review=None, limit=None):
    """Dry run (default) or create one labelled issue per new finding. Returns one result per finding."""
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise PublishError("Name the GitHub repository as owner/name.")
    if LABEL in WEB_TRIGGER_LABELS:
        raise PublishError("The issue label would assign Kiro Web.")
    drafts = plan(receipt, kinds, review)
    if limit is not None and len(drafts) > limit:
        raise PublishError("%d findings exceed the limit of %d issues per run. Nothing was published." % (len(drafts), limit))
    rules = receipt["candidate"].get("rulesBlob") or "unknown"
    ledger = read_ledger(folder)
    results = []
    for draft in drafts:
        entry = ledger.get(slug + "#" + draft["key"])
        known = entry.get("number") if isinstance(entry, dict) else None
        if dry_run:
            results.append({"key": draft["key"], "title": draft["title"], "body": draft["body"],
                            "action": "would-check-then-create", "ledgerIssue": known})
            continue
        if not gh:
            raise PublishError("Install the GitHub CLI (gh) and sign in to publish.")
        found = existing_issue(gh, slug, draft["key"], rules, known, run)
        if found is not None:
            state, number = found
            action = "exists" if state == "open" else "resolved"
            results.append({"key": draft["key"], "title": draft["title"], "action": action, "number": number})
            ledger[slug + "#" + draft["key"]] = {"number": number, "state": state, "rules": rules}
            write_ledger(folder, ledger)
            continue
        handle, body_file = tempfile.mkstemp(prefix="night-watch-issue-", suffix=".md")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(draft["body"])
            output = run_gh(gh, ["issue", "create", "--repo", slug, "--title", draft["title"],
                                 "--label", LABEL, "--body-file", body_file], run)
        finally:
            os.remove(body_file)
        created = re.search(r"https://github\.com/" + re.escape(slug) + r"/issues/(\d+)", output)
        if not created:
            raise PublishError("gh did not report the new issue's address. Check the repository before retrying.")
        number = int(created.group(1))
        ledger[slug + "#" + draft["key"]] = {"number": number, "state": "open", "rules": rules}
        write_ledger(folder, ledger)
        results.append({"key": draft["key"], "title": draft["title"], "action": "created", "number": number})
    return results

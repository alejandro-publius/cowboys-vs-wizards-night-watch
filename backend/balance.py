"""Paired balance comparison of two Git refs of the Cowboys vs Wizards repository.

Only git, Node and the selected local repo's own engine are executed. There is
no network access and no credential handling.
"""

import json
import math
import os
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

DEFAULT_CONFIG = {
    "repo": "",
    "baseline": "HEAD",
    "candidate": "HEAD",
    "firstSeed": 0,
    "count": 1000,
    "threshold": 10,
}

MAX_COUNT = 10000
MAX_SEED = 2**32 - 1
GIT_TIMEOUT = 15
TEST_TIMEOUT = 45
SIM_TIMEOUT = 55
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_CHANGES = 50
CARDS_DIR = ".kiro/specs/duel-card-pipeline/cards"

REQUIRED_PATHS = ["package.json", "src/game", "scripts/play-match.mjs", "scripts/simulate-matches.mjs"]
OPTIONAL_PATHS = ["tests/game", "src/profile", CARDS_DIR]


class ConfigError(Exception):
    """The settings or refs are unusable. Nothing is recorded."""


class CheckError(Exception):
    """A check or simulation failed. The failure is recorded as a report."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail


# ---------------------------------------------------------------- settings

def _whole_number(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s must be a whole number." % name)
    return value


def validate_config(raw):
    """Return a clean config dict or raise ConfigError."""
    if not isinstance(raw, dict):
        raise ConfigError("Settings must be a JSON object.")
    unknown = sorted(set(raw) - set(DEFAULT_CONFIG))
    if unknown:
        raise ConfigError("Unknown setting names were sent. Remove %s." % ", ".join(unknown))
    merged = dict(DEFAULT_CONFIG)
    merged.update(raw)

    repo = merged["repo"]
    if not isinstance(repo, str) or not repo.strip():
        raise ConfigError("Choose the local Cowboys vs Wizards checkout by entering its absolute path.")
    repo = repo_root(repo.strip())

    count = _whole_number(merged["count"], "Match count")
    if not 1 <= count <= MAX_COUNT:
        raise ConfigError("Match count must be between 1 and %d." % MAX_COUNT)
    first_seed = _whole_number(merged["firstSeed"], "First seed")
    if not 0 <= first_seed <= MAX_SEED or first_seed + count - 1 > MAX_SEED:
        raise ConfigError("First seed must be 0 or more, and the last seed must not pass %d." % MAX_SEED)

    threshold = merged["threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold):
        raise ConfigError("Threshold must be a number of percentage points.")
    if not 1 <= threshold <= 100:
        raise ConfigError("Threshold must be between 1 and 100 percentage points.")

    config = {"repo": repo, "firstSeed": first_seed, "count": count, "threshold": threshold}
    for name in ("baseline", "candidate"):
        ref = merged[name]
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 200 or re.search(r"[\x00-\x1f\x7f]", ref):
            raise ConfigError("The %s ref must be a short Git branch, tag or commit name." % name)
        config[name] = ref.strip()
    # Fail now when a ref does not resolve, so a bad setting is never saved.
    for name in ("baseline", "candidate"):
        resolve_ref(repo, config[name], name)
    return {key: config[key] for key in DEFAULT_CONFIG}


# ---------------------------------------------------------------- processes

def child_env():
    """A small environment for children, so no unrelated variables are passed on."""
    env = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8"}
    for key in ("PATH", "HOME", "TMPDIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def run_command(argv, cwd, timeout, label, stdout=None):
    try:
        return subprocess.run(
            argv, cwd=cwd, env=child_env(), timeout=timeout, stdin=subprocess.DEVNULL,
            stdout=stdout if stdout is not None else subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired:
        raise CheckError("%s did not finish within %d seconds. Try a smaller match count." % (label, timeout))
    except OSError:
        raise CheckError("%s could not be started. Check that %s is installed and on PATH." % (label, argv[0]))


def text_of(data):
    return (data or b"").decode("utf-8", errors="replace")


def tail(text, limit=2000):
    text = text.strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def git(repo, args, label, timeout=GIT_TIMEOUT):
    result = run_command(["git", "-C", repo] + args, None, timeout, label)
    return result.returncode, text_of(result.stdout).strip(), text_of(result.stderr).strip()


def repo_root(path):
    if not os.path.isabs(path):
        raise ConfigError("The repository path must be absolute, for example /Users/you/cowboys-vs-wizards-board.")
    if not os.path.isdir(path):
        raise ConfigError("The repository folder %s does not exist." % path)
    try:
        code, top, _ = git(path, ["rev-parse", "--show-toplevel"], "git")
    except CheckError as error:
        raise ConfigError(str(error))
    if code != 0 or not top:
        raise ConfigError("%s is not a Git checkout. Enter the top folder of a local clone." % path)
    if os.path.realpath(top) != os.path.realpath(path):
        raise ConfigError("%s is inside a repository. Enter its top folder %s instead." % (path, top))
    return os.path.realpath(path)


def resolve_ref(repo, ref, name="ref"):
    try:
        code, out, _ = git(repo, ["rev-parse", "--verify", "--end-of-options", ref + "^{commit}"], "git")
    except CheckError as error:
        raise ConfigError(str(error))
    if code != 0 or not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", out):
        raise ConfigError("The %s ref '%s' is not a commit in this repository. Fetch it locally or choose another ref." % (name, ref))
    return out


def node_command():
    override = os.environ.get("NIGHT_WATCH_NODE")
    if override:
        return override
    for location in (shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if location and os.access(location, os.X_OK):
            return location
    raise CheckError("Node is required to run Night Watch. Install Node 22.18 or later.")


# ---------------------------------------------------------------- snapshots

def extract_snapshot(repo, commit, dest):
    """Write only the needed files of one commit into dest, using git archive."""
    paths = []
    for path in REQUIRED_PATHS + OPTIONAL_PATHS:
        code, out, _ = git(repo, ["ls-tree", "--name-only", commit, "--", path], "git")
        if code == 0 and out:
            paths.append(path)
        elif path in REQUIRED_PATHS:
            raise CheckError("Commit %s has no %s. Choose a ref that contains the game engine and simulator." % (commit[:10], path))
    tar_path = dest + ".tar"
    with open(tar_path, "wb") as handle:
        result = run_command(["git", "-C", repo, "archive", "--format=tar", commit, "--"] + paths, None, GIT_TIMEOUT, "git archive", stdout=handle)
    if result.returncode != 0:
        raise CheckError("git archive failed for commit %s." % commit[:10], tail(text_of(result.stderr)))
    os.makedirs(dest)
    root = os.path.realpath(dest)
    total = 0
    with tarfile.open(tar_path) as archive:
        for member in archive.getmembers():
            name = posixpath.normpath(member.name)
            if name.startswith("/") or name == ".." or name.startswith("../"):
                raise CheckError("The archive for commit %s contains an unsafe path and was rejected." % commit[:10])
            target = os.path.realpath(os.path.join(root, name))
            if target != root and not target.startswith(root + os.sep):
                raise CheckError("The archive for commit %s contains an unsafe path and was rejected." % commit[:10])
            if member.isdir():
                os.makedirs(target, exist_ok=True)
            elif member.isreg():
                total += member.size
                if total > MAX_ARCHIVE_BYTES:
                    raise CheckError("The files needed from commit %s are larger than %d MB." % (commit[:10], MAX_ARCHIVE_BYTES // 2**20))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with archive.extractfile(member) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
            else:
                raise CheckError("The archive for commit %s contains a link or special file and was rejected." % commit[:10])
    os.remove(tar_path)


def run_game_tests(snapshot, node):
    """Run the pure game tests of a snapshot. Returns (state, test count)."""
    folder = os.path.join(snapshot, "tests", "game")
    files = []
    for base, _, names in os.walk(folder):
        files += [os.path.relpath(os.path.join(base, n), snapshot) for n in sorted(names) if n.endswith((".js", ".mjs", ".cjs"))]
    if not files:
        return "unavailable", None
    result = run_command([node, "--test"] + sorted(files), snapshot, TEST_TIMEOUT, "The game tests")
    output = text_of(result.stdout) + text_of(result.stderr)
    found = re.search(r"^\D*\btests (\d+)\s*$", output, re.MULTILINE)
    count = int(found.group(1)) if found else None
    if result.returncode != 0:
        raise CheckError("The candidate's game tests failed, so no simulation was run.", tail(output))
    return "passed", count


def simulate(snapshot, node, config):
    out_file = os.path.join(os.path.dirname(snapshot), os.path.basename(snapshot) + "-report.json")
    argv = [
        node, "scripts/simulate-matches.mjs", "--from", str(config["firstSeed"]), "--count", str(config["count"]),
        "--cowboy", "cpu", "--wizard", "cpu", "--out", out_file,
    ]
    result = run_command(argv, snapshot, SIM_TIMEOUT, "The simulation")
    if result.returncode != 0:
        raise CheckError("The simulation failed.", tail(text_of(result.stderr) + text_of(result.stdout)))
    try:
        with open(out_file, encoding="utf-8") as handle:
            return parse_simulation(json.load(handle), config)
    except (OSError, ValueError, KeyError, TypeError):
        raise CheckError("The simulation report could not be read. Check that the simulator writes the expected JSON.")


def parse_simulation(data, config):
    count = config["count"]
    seeds = data["seeds"]
    if seeds["matches"] != count or seeds["from"] != config["firstSeed"] or seeds["to"] != config["firstSeed"] + count - 1:
        raise CheckError("The simulation covered different seeds than requested.", json.dumps(seeds))
    if data["bots"] != {"cowboy": "cpu", "wizard": "cpu"}:
        raise CheckError("The simulation did not report the requested CPU policies.")
    first = second = 0
    for starter, other in (("cowboy", "wizard"), ("wizard", "cowboy")):
        entry = data["byStartingSide"][starter]
        first += entry[starter]
        second += entry[other]
    counts = {
        "cowboy": data["results"]["cowboy"]["wins"],
        "wizard": data["results"]["wizard"]["wins"],
        "draw": data["results"]["cowboy"]["draws"],
        "first": first,
        "second": second,
    }
    values = list(counts.values())
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
        raise CheckError("The simulation report has counts that are not whole numbers.")
    if counts["cowboy"] + counts["wizard"] + counts["draw"] != count:
        raise CheckError("The simulation outcomes do not add up to %d matches." % count)
    if counts["first"] + counts["second"] != counts["cowboy"] + counts["wizard"]:
        raise CheckError("The seat and faction results disagree on the number of wins.")
    return counts


# ---------------------------------------------------------------- comparison

def flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out.update(flatten(item, prefix + "." + key if prefix else key))
        return out
    if value is None or isinstance(value, (str, int, float, bool)):
        return {prefix: value}
    return {}


def card_changes(base_dir, cand_dir):
    base_cards = os.path.join(base_dir, *CARDS_DIR.split("/"))
    cand_cards = os.path.join(cand_dir, *CARDS_DIR.split("/"))
    if not (os.path.isdir(base_cards) and os.path.isdir(cand_cards)):
        return []
    changes = []
    for filename in sorted(set(os.listdir(base_cards)) & set(os.listdir(cand_cards))):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(base_cards, filename), encoding="utf-8") as handle:
                old = flatten(json.load(handle))
            with open(os.path.join(cand_cards, filename), encoding="utf-8") as handle:
                new = flatten(json.load(handle))
        except (OSError, ValueError):
            continue
        for field in sorted(set(old) & set(new)):
            if old[field] != new[field]:
                changes.append({
                    "card": new.get("name") or old.get("name") or filename[:-5],
                    "field": field, "old": old[field], "new": new[field],
                })
    return changes[:MAX_CHANGES]


def github_repository(origin):
    if origin.startswith("git@github.com:"):
        slug = origin.removeprefix("git@github.com:")
    else:
        url = urlsplit(origin)
        if url.scheme not in ("https", "ssh") or url.hostname != "github.com":
            return None
        slug = url.path.removeprefix("/")
    slug = slug.rstrip("/").removesuffix(".git")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", slug):
        return slug
    return None


def issue_url(repo, record):
    code, origin, _ = git(repo, ["config", "--get", "remote.origin.url"], "git")
    try:
        slug = github_repository(origin) if code == 0 else None
    except ValueError:
        return None
    if not slug:
        return None
    base, cand = record["baseline"], record["candidate"]
    title = "Night Watch %s on %s" % (record["state"], cand["commit"][:10])
    lines = [
        record["message"],
        "",
        "Baseline: %s" % base["commit"],
        "Candidate: %s" % cand["commit"],
        "Shared seeds: %d–%d (%d matches per revision, CPU vs. CPU)" % (
            record["firstSeed"], record["firstSeed"] + record["count"] - 1, record["count"],
        ),
    ]
    for label, side in (("Baseline", base), ("Candidate", cand)):
        if side["counts"] is not None:
            counts = side["counts"]
            lines.append("%s: %d Cowboy wins, %d Wizard wins, %d draws." % (
                label, counts["cowboy"], counts["wizard"], counts["draw"],
            ))
    for change in record["changes"][:10]:
        lines.append("- %s %s: %s → %s" % (change["card"], change["field"], change["old"], change["new"]))
    if record.get("detail"):
        lines.extend(("", "Check output", tail(record["detail"], 800)))
    return "https://github.com/" + slug + "/issues/new?" + urlencode({"title": title, "body": "\n".join(lines)})


def judge(config, base, cand):
    """Return a status and the exact outcome shifts that crossed the threshold."""
    count, limit = config["count"], config["threshold"]
    hits = []
    for name, key in (("Cowboy", "cowboy"), ("Wizard", "wizard")):
        shift = 100 * (cand[key] - base[key]) / count
        if abs(shift) >= limit:
            verb = "rose" if shift > 0 else "fell"
            hits.append("%s win share %s %.1f pp." % (name, verb, abs(shift)))
    base_advantage = max(0, base["first"] - base["second"])
    cand_advantage = max(0, cand["first"] - cand["second"])
    seat_shift = 100 * (cand_advantage - base_advantage) / count
    if seat_shift >= limit:
        hits.append("First-seat advantage grew %.1f pp." % seat_shift)
    if hits:
        return "alert", " ".join(hits)
    return "steady", "Faction results and first-seat growth stayed within the %g pp alert threshold." % limit


def build_report(config, source):
    """Run the paired comparison and return a report record. Raises ConfigError when settings are unusable."""
    if not config.get("repo"):
        raise ConfigError("No repository is configured. Save the local checkout path in the settings first.")
    repo = repo_root(config["repo"])
    base_commit = resolve_ref(repo, config["baseline"], "baseline")
    cand_commit = resolve_ref(repo, config["candidate"], "candidate")
    record = {
        "id": uuid.uuid4().hex[:12],
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "firstSeed": config["firstSeed"],
        "count": config["count"],
        "threshold": config["threshold"],
        "state": "failed",
        "message": "",
        "baseline": {"ref": config["baseline"], "commit": base_commit, "counts": None},
        "candidate": {"ref": config["candidate"], "commit": cand_commit, "counts": None},
        "rules": {"state": "skipped", "tests": None},
        "changes": [],
    }
    work = tempfile.mkdtemp(prefix="night-watch-")
    try:
        compare(config, record, repo, work)
    except CheckError as error:
        record["state"] = "failed"
        record["message"] = str(error)
        if error.detail:
            detail = error.detail
            for prefix in {work, os.path.realpath(work)}:
                detail = detail.replace("file://" + prefix + "/", "").replace(prefix + "/", "")
            record["detail"] = detail
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if record["state"] in ("alert", "failed"):
        try:
            url = issue_url(repo, record)
        except CheckError:
            url = None
        if url:
            record["issueUrl"] = url
    return record


def compare(config, record, repo, work):
    node = node_command()
    base_commit, cand_commit = record["baseline"]["commit"], record["candidate"]["commit"]
    cand_dir = os.path.join(work, "candidate")
    extract_snapshot(repo, cand_commit, cand_dir)
    try:
        record["rules"]["state"], record["rules"]["tests"] = run_game_tests(cand_dir, node)
    except CheckError:
        record["rules"]["state"] = "failed"
        raise
    cand_counts = simulate(cand_dir, node, config)
    record["candidate"]["counts"] = cand_counts
    if base_commit == cand_commit:
        base_dir, base_counts = cand_dir, cand_counts
    else:
        base_dir = os.path.join(work, "baseline")
        extract_snapshot(repo, base_commit, base_dir)
        base_counts = simulate(base_dir, node, config)
    record["baseline"]["counts"] = base_counts
    record["changes"] = card_changes(base_dir, cand_dir)
    record["state"], record["message"] = judge(config, base_counts, cand_counts)

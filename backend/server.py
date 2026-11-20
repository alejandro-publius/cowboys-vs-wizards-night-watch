"""Night Watch Kiro Crew backend.

Run without arguments to serve HTTP on 127.0.0.1 (port from the PORT variable).
Run `python3 backend/server.py run --scheduled --mode showdown` for one check, as a cron command.
"""

import argparse
import contextlib
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import balance
import issues

APP_ID = "kiroween-night-watch"
SCHEDULE_OWNER = "app:kiroween-night-watch/balance"
API_PREFIXES = ("/apps/" + APP_ID + "/api", "/api/apps/" + APP_ID, "/api")
MAX_BODY = 64 * 1024
MAX_REPORTS = 20
NIGHTLY_MODE = "showdown"
# The Night Shift reviewer in this app's own checkout (scripts/night-review.mjs), and its bounds.
REVIEWER = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "scripts", "night-review.mjs"))
REVIEW_TIMEOUT = 120
MAX_ISSUES_PER_RUN = 2


class Busy(Exception):
    """Another check is already running."""


class StoreError(Exception):
    """A saved file cannot be read."""


# ---------------------------------------------------------------- storage

def data_dir():
    crew_home = os.environ.get("KIROCREW_HOME") or os.path.join(os.path.expanduser("~"), ".kiro", "crew")
    path = os.environ.get("NIGHT_WATCH_DATA_DIR") or os.path.join(crew_home, "app-data", APP_ID)
    os.makedirs(path, exist_ok=True)
    return path


def read_json(name, default):
    path = os.path.join(data_dir(), name)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except ValueError:
        raise StoreError("The saved file %s is damaged. Move it aside to start a new history." % path)


def write_json(name, value):
    folder = data_dir()
    handle, temp = tempfile.mkstemp(dir=folder, prefix=name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, os.path.join(folder, name))
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temp)
        raise


@contextlib.contextmanager
def file_lock(name, wait):
    with open(os.path.join(data_dir(), name), "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except OSError:
            raise Busy("A balance check is already running. Wait for it to finish, then try again.")
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load_config():
    config = dict(balance.DEFAULT_CONFIG)
    saved = read_json("config.json", {})
    if isinstance(saved, dict):
        config.update({key: saved[key] for key in config if key in saved})
    return config


def load_reports():
    reports = read_json("history.json", [])
    return reports if isinstance(reports, list) else []


def get_state():
    return {"config": load_config(), "reports": load_reports()}


def save_config(raw):
    config = balance.validate_config(raw)
    with file_lock("state.lock", wait=True):
        write_json("config.json", config)
    return get_state()


def run_report(source, mode=None):
    """Run one comparison, store it, and return the record. A mode overrides the saved one for this run."""
    with file_lock("run.lock", wait=False):
        config = load_config()
        if mode is not None:
            if mode not in balance.MODES:
                raise balance.ConfigError("Mode must be one of %s." % ", ".join(balance.MODES))
            config["mode"] = mode
        record = balance.build_report(config, source)
        with file_lock("state.lock", wait=True):
            write_json("history.json", ([record] + load_reports())[:MAX_REPORTS])
    return record


# ---------------------------------------------------------------- HTTP

def host_allowed(host, port):
    return host is not None and host.strip().lower() in ("127.0.0.1:%d" % port, "localhost:%d" % port)


class Handler(BaseHTTPRequestHandler):
    server_version = "NightWatch"

    def log_message(self, fmt, *args):
        pass

    def parse_request(self):
        # Refuse DNS-rebinding style requests; X-Forwarded-Host is never consulted.
        if not super().parse_request():
            return False
        if host_allowed(self.headers.get("Host"), self.server.server_address[1]):
            return True
        self.close_connection = True
        self.send_json(403, {"error": "Host header must be 127.0.0.1 or localhost on the listening port."})
        return False

    def send_json(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        path = self.path.split("?", 1)[0]
        for prefix in API_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                return path[len(prefix):] or "/"
        return path

    def read_body(self):
        if not (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() == "application/json":
            raise ValueError("Send the request with Content-Type application/json.")
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            raise ValueError("Send a Content-Length header with the JSON body.")
        if length < 0 or length > MAX_BODY:
            raise ValueError("The request body must be at most %d bytes." % MAX_BODY)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("The request body is not valid JSON.")

    def do_GET(self):
        path = self.route()
        try:
            if path == "/health":
                self.send_json(200, {"status": "ok"})
            elif path == "/state":
                self.send_json(200, get_state())
            else:
                self.send_json(404, {"error": "Unknown path. Use /state, /config, /run or /health."})
        except StoreError as error:
            self.send_json(500, {"error": str(error)})

        except OSError as error:
            self.send_json(500, {"error": "The data folder could not be read (%s)." % error.strerror})

    def do_POST(self):
        path = self.route()
        if path not in ("/config", "/run"):
            self.send_json(404, {"error": "Unknown path. Use /state, /config, /run or /health."})
            return
        try:
            body = self.read_body()
            if path == "/config":
                self.send_json(200, save_config(body))
            elif not isinstance(body, dict):
                raise ValueError("The run request body must be a JSON object, for example {}.")
            else:
                self.send_json(200, {"report": run_report("manual")})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except balance.ConfigError as error:
            self.send_json(400, {"error": str(error)})
        except Busy as error:
            self.send_json(409, {"error": str(error)})
        except StoreError as error:
            self.send_json(500, {"error": str(error)})
        except OSError as error:
            self.send_json(500, {"error": "The data folder could not be written (%s)." % error.strerror})

    def do_PUT(self):
        self.send_json(405, {"error": "Use GET or POST."})

    do_DELETE = do_PATCH = do_PUT


def serve():
    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError:
        sys.exit("PORT must be a number.")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print("Night Watch is listening on http://127.0.0.1:%d" % port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# ---------------------------------------------------------------- scheduled issues (opt-in)

def review_finding(record, node=None, run=subprocess.run):
    """Bounded independent check: the Night Shift reviewer re-runs the record's counts from a fresh snapshot.

    Returns (reproduced, summary). Any failure, timeout or missing reviewer counts as not reproduced.
    """
    node = node or os.environ.get("NIGHT_WATCH_NODE") or shutil.which("node")
    if not node or not os.path.isfile(REVIEWER):
        return False, "reviewer unavailable (%s)" % ("node not found" if not node else "scripts/night-review.mjs missing")
    handle, path = tempfile.mkstemp(prefix="night-watch-finding-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(record, out)
        try:
            result = run([node, REVIEWER, "check", "--finding", path], cwd=os.path.dirname(os.path.dirname(REVIEWER)),
                         env=balance.child_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         timeout=REVIEW_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, "reviewer did not finish (%s)" % type(error).__name__
    finally:
        os.remove(path)
    try:
        line = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        line = {}
    if result.returncode == 0 and line.get("reproduced") is True:
        return True, "reproduced all %d games for %s seeds %d-%d" % (
            line["observed"]["games"], line["commit"][:12], line["seeds"][0], line["seeds"][1])
    return False, "not reproduced (exit %d%s)" % (result.returncode,
                                                  ", mismatched " + ", ".join(line["mismatched"]) if line.get("mismatched") else "")


def scheduled_issues(record, config, gh=None, review=review_finding, run=subprocess.run):
    """After a scheduled run: off by default; with autoIssues "lean", one deduplicated issue per reproduced lean finding."""
    if config.get("autoIssues", "off") == "off":
        return {"mode": "off"}
    receipt = record.get("receipt")
    if config.get("autoIssues") != "lean" or not config.get("band") or record.get("mode") != "showdown" or not receipt:
        return {"mode": config.get("autoIssues"), "state": "skipped", "reason": "needs Showdown mode, a band and a receipt"}
    if not [f for f in receipt["findings"] if f["kind"] == "lean"]:
        return {"mode": "lean", "state": "no-finding"}
    reproduced, summary = review(record)
    if not reproduced:
        return {"mode": "lean", "state": "not-published", "review": summary}
    slug = config.get("issueRepo") or receipt.get("repository")
    with file_lock("publish.lock", wait=False):
        results = issues.publish(receipt, slug, data_dir(), gh=gh or shutil.which("gh"), dry_run=False, run=run,
                                 kinds={"lean"}, review=summary, limit=MAX_ISSUES_PER_RUN)
    return {"mode": "lean", "state": "published", "review": summary, "repository": slug,
            "issues": [{"key": r["key"], "action": r["action"], "number": r["number"]} for r in results]}


def attach_publication(record, publication):
    record["publication"] = publication
    with file_lock("state.lock", wait=True):
        write_json("history.json", [record if r.get("id") == record["id"] else r for r in load_reports()])


# ---------------------------------------------------------------- CLI

def run_cli(source, mode=None):
    try:
        record = run_report(source, mode)
    except (balance.ConfigError, Busy, StoreError) as error:
        print("Night Watch could not run. %s" % error, file=sys.stderr)
        return 1
    except OSError as error:
        print("Night Watch could not write its data folder (%s)." % error.strerror, file=sys.stderr)
        return 1
    print("Night Watch %s (%s). %s" % (record["state"], record["id"], record["message"]))
    if record.get("issueUrl"):
        print("Issue draft link is saved with the report.")
    if source != "scheduled" or record["state"] == "failed":
        return 1 if record["state"] == "failed" else 0
    try:
        publication = scheduled_issues(record, load_config())
    except (issues.PublishError, Busy, StoreError, OSError, ValueError) as error:
        publication = {"mode": "lean", "state": "error", "reason": str(error)}
    attach_publication(record, publication)
    if publication["mode"] != "off":
        print("Scheduled issues: %s%s" % (publication["state"], "".join(
            "; %s #%d" % (i["action"], i["number"]) for i in publication.get("issues", []))))
    return 1 if publication.get("state") == "error" else 0


def evaluate_cli(args):
    """On-demand check: writes a receipt file; leaves saved settings and history untouched."""
    band = None
    if args.band_low is not None or args.band_high is not None:
        band = {"low": args.band_low, "high": args.band_high}
    raw = {"repo": args.repo, "baseline": args.baseline or args.candidate, "candidate": args.candidate,
           "firstSeed": args.first_seed, "count": args.count, "threshold": args.threshold,
           "mode": args.mode, "band": band}
    try:
        config = balance.validate_config(raw)
        record = balance.build_report(config, "on-demand")
    except balance.ConfigError as error:
        print("Night Watch could not run. %s" % error, file=sys.stderr)
        return 1
    receipt = record["receipt"]
    text = json.dumps(receipt, indent=2) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as out:
            out.write(text)
    else:
        sys.stdout.write(text)
    summary = sys.stderr if not args.out else sys.stdout
    print("Night Watch on-demand: shift %s, lean %s. %s" % (
        record["state"], receipt["lean"]["verdict"], record["message"]), file=summary)
    return 1 if record["state"] == "failed" else 0


def load_receipt(args):
    if args.receipt:
        with open(args.receipt, encoding="utf-8") as handle:
            return json.load(handle)
    reports = load_reports()
    wanted = [r for r in reports if args.report in ("latest", r.get("id"))]
    if not wanted or "receipt" not in wanted[0]:
        raise issues.PublishError("No stored report %s has a receipt." % args.report)
    return wanted[0]["receipt"]


def issues_cli(args):
    """Dry run unless --publish is given. The scheduled job uses scheduled_issues() instead, and only when opted in."""
    try:
        receipt = load_receipt(args)
        slug = args.repo or receipt.get("repository")
        with file_lock("publish.lock", wait=False):
            results = issues.publish(receipt, slug, data_dir(), gh=shutil.which("gh"), dry_run=not args.publish)
    except (issues.PublishError, Busy, StoreError, OSError, ValueError) as error:
        print("Night Watch did not publish. %s" % error, file=sys.stderr)
        return 1
    if not results:
        print("No findings in this receipt. Nothing to publish.")
    for result in results:
        if result["action"] == "would-check-then-create":
            print("DRY RUN %s: %s" % (result["key"], result["title"]))
            print("  would search open '%s' issues in %s for its marker, then create one if none exists%s" % (
                issues.LABEL, slug, "; ledger lists #%d" % result["ledgerIssue"] if result["ledgerIssue"] else ""))
            print("  " + result["body"].replace("\n", "\n  ").rstrip())
        else:
            print("%s %s: #%d %s" % (result["action"].upper(), result["key"], result["number"], result["title"]))
    return 0


def schedule_cli(args):
    crew = shutil.which("kirocrew")
    if not crew:
        print("Install Kiro Crew and put kirocrew on PATH.", file=sys.stderr)
        return 1
    if args.remove:
        return subprocess.run([crew, "cron", "remove", "--managed-by", SCHEDULE_OWNER]).returncode
    try:
        time = datetime.strptime(args.time, "%H:%M")
    except ValueError:
        print("Enter the nightly time as HH:MM.", file=sys.stderr)
        return 1
    if not load_config().get("repo"):
        print("Save the checkout and revisions in Night Watch before scheduling checks.", file=sys.stderr)
        return 1
    # The nightly job plays the Showdown fight, whatever mode the manual settings hold.
    command = shlex.join([sys.executable, os.path.realpath(__file__), "run", "--scheduled", "--mode", NIGHTLY_MODE])
    return subprocess.run([
        crew, "cron", "add", "Night Watch", "",
        "--cron", "%d %d * * *" % (time.minute, time.hour), "--timezone", args.timezone,
        "--command", command, "--managed-by", SCHEDULE_OWNER,
        "--timeout", "300", "--timeout-secs", "315",
        "--no-persistent-session", "--minimal-context", "--silent",
    ]).returncode


def main(argv):
    parser = argparse.ArgumentParser(description="Night Watch balance backend")
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="run one balance check and store the report")
    run.add_argument("--scheduled", action="store_true", help="record the check as scheduled")
    run.add_argument("--mode", choices=balance.MODES, help="showdown (paired seat-swapped deals) or lanes; defaults to the saved setting")
    schedule = commands.add_parser("schedule", help="register or remove the Kiro Crew job")
    schedule.add_argument("--time", default="02:00", help="local nightly time as HH:MM")
    schedule.add_argument("--timezone", default="America/Los_Angeles", help="IANA timezone for the schedule")
    schedule.add_argument("--remove", action="store_true", help="remove the Night Watch job")
    evaluate = commands.add_parser("evaluate", help="on-demand check that writes a receipt and keeps saved state")
    evaluate.add_argument("--repo", required=True, help="absolute path of a local checkout")
    evaluate.add_argument("--candidate", required=True, help="candidate Git ref")
    evaluate.add_argument("--baseline", help="baseline Git ref (defaults to the candidate)")
    evaluate.add_argument("--mode", choices=balance.MODES, default="showdown")
    evaluate.add_argument("--first-seed", type=int, default=0)
    evaluate.add_argument("--count", type=int, default=1000, help="deals (Showdown) or matches (lanes)")
    evaluate.add_argument("--threshold", type=float, default=10, help="shift alert threshold in pp")
    evaluate.add_argument("--band-low", type=float, help="operator target band low edge, percent")
    evaluate.add_argument("--band-high", type=float, help="operator target band high edge, percent")
    evaluate.add_argument("--out", help="receipt file (default: stdout)")
    publish = commands.add_parser("issues", help="dry-run or publish one 'night-watch' issue per finding")
    source = publish.add_mutually_exclusive_group()
    source.add_argument("--receipt", help="receipt JSON file")
    source.add_argument("--report", default="latest", help="stored report id, or latest")
    publish.add_argument("--repo", help="GitHub owner/name (defaults to the receipt's repository)")
    publish.add_argument("--publish", action="store_true", help="really create issues; without it this is a dry run")
    args = parser.parse_args(argv)
    if args.command == "evaluate":
        return evaluate_cli(args)
    if args.command == "issues":
        return issues_cli(args)
    if args.command == "schedule":
        return schedule_cli(args)
    if args.command == "run":
        return run_cli("scheduled" if args.scheduled else "manual", args.mode)
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

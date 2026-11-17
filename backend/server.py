"""Night Watch Kiro Crew backend.

Run without arguments to serve HTTP on 127.0.0.1 (port from the PORT variable).
Run `python3 backend/server.py run --scheduled` for one check, as a cron command.
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

APP_ID = "kiroween-night-watch"
SCHEDULE_OWNER = "app:kiroween-night-watch/balance"
API_PREFIXES = ("/apps/" + APP_ID + "/api", "/api/apps/" + APP_ID, "/api")
MAX_BODY = 64 * 1024
MAX_REPORTS = 20


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


def run_report(source):
    """Run one comparison, store it, and return the record."""
    with file_lock("run.lock", wait=False):
        record = balance.build_report(load_config(), source)
        with file_lock("state.lock", wait=True):
            write_json("history.json", ([record] + load_reports())[:MAX_REPORTS])
    return record


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "NightWatch"

    def log_message(self, fmt, *args):
        pass

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


# ---------------------------------------------------------------- CLI

def run_cli(source):
    try:
        record = run_report(source)
    except (balance.ConfigError, Busy, StoreError) as error:
        print("Night Watch could not run. %s" % error, file=sys.stderr)
        return 1
    except OSError as error:
        print("Night Watch could not write its data folder (%s)." % error.strerror, file=sys.stderr)
        return 1
    print("Night Watch %s (%s). %s" % (record["state"], record["id"], record["message"]))
    if record.get("issueUrl"):
        print("Issue draft link is saved with the report.")
    return 1 if record["state"] == "failed" else 0


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
    command = shlex.join([sys.executable, os.path.realpath(__file__), "run", "--scheduled"])
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
    schedule = commands.add_parser("schedule", help="register or remove the Kiro Crew job")
    schedule.add_argument("--time", default="02:00", help="local nightly time as HH:MM")
    schedule.add_argument("--timezone", default="America/Los_Angeles", help="IANA timezone for the schedule")
    schedule.add_argument("--remove", action="store_true", help="remove the Night Watch job")
    args = parser.parse_args(argv)
    if args.command == "schedule":
        return schedule_cli(args)
    if args.command == "run":
        return run_cli("scheduled" if args.scheduled else "manual")
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""
svc.py - a tiny cross-platform background-service manager for AI agents.

Why this exists
---------------
Agents frequently need to start a long-running service (dev server, API, DB,
watcher...). The trap is starting it *blocking*: the shell never returns and the
agent freezes behind its own service. This tool always starts services
DETACHED and NON-BLOCKING, captures their output to a log file, and keeps a
registry so any later agent (even in a new session) can see what is running,
read the output, and stop it cleanly.

State lives in  ~/.agent-bg/
    registry.json        one record per service (name, pid, cmd, cwd, port, log)
    logs/<name>.log      merged stdout+stderr of that service

Commands
--------
  start <name> [--cwd DIR] [--port N] [--wait-port N] [--wait-log REGEX]
               [--wait-timeout S] [--shell MODE] [--env K=V] [--restart] -- <command...>
  restart <name>                    restart, replaying the stored launch config
  list                              show all services + alive/dead
  status <name>                     details for one service
  logs <name> [-n N] [--follow]     show last N lines (default 40); -f to stream
  grep <name> REGEX [-C N]          regex-search the log with context lines
  stop <name> | --all               kill the service and its whole child tree
  clean                             drop dead entries from the registry

Examples
--------
  python svc.py start web --cwd D:/app --wait-port 3000 -- npm run dev
  python svc.py start api --port 8000 --wait-log "Uvicorn running" -- python -m uvicorn main:app --port 8000
  python svc.py list
  python svc.py logs web -n 50
  python svc.py logs web --follow
  python svc.py stop web
  python svc.py stop --all
"""

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

try:
    import psutil  # cross-platform process tree handling
except Exception:  # pragma: no cover - psutil is strongly recommended
    psutil = None

IS_WIN = os.name == "nt"
STATE_DIR = Path(os.environ.get("AGENT_BG_HOME", str(Path.home() / ".agent-bg")))
LOG_DIR = STATE_DIR / "logs"
REGISTRY = STATE_DIR / "registry.json"


# --------------------------------------------------------------------------- #
# registry helpers
# --------------------------------------------------------------------------- #
def _ensure_dirs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(reg: dict):
    _ensure_dirs()
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(REGISTRY)


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _alive(pid) -> bool:
    if not pid:
        return False
    if psutil:
        try:
            p = psutil.Process(pid)
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    # fallback
    try:
        if IS_WIN:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True,
            ).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# launch
# --------------------------------------------------------------------------- #
def _build_argv(cmd_str: str, shell: str, tokens=None):
    """Turn a command string into an argv list + a human label, per shell mode.

    shell: auto | cmd | powershell | pwsh | sh | bash | none
    We always launch with shell=False and construct the wrapper ourselves so
    behaviour is identical no matter which agent/host shell invoked svc.py.
    """
    shell = (shell or "auto").lower()
    if shell == "auto":
        shell = "cmd" if IS_WIN else "sh"
    if shell == "cmd":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/d", "/s", "/c", cmd_str], f"cmd: {cmd_str}"
    if shell in ("powershell", "pwsh"):
        exe = "pwsh" if shell == "pwsh" else "powershell"
        exe = shutil.which(exe) or exe
        return [exe, "-NoProfile", "-NonInteractive", "-Command", cmd_str], f"{shell}: {cmd_str}"
    if shell in ("sh", "bash"):
        exe = shutil.which(shell) or ("/bin/" + shell)
        # `exec` replaces the shell with the real process, so the pid we record
        # IS the service (no extra shell layer) -- borrowed from mcproc. Skip it
        # when the command needs the shell itself (pipes/&&/;/redirects/globs).
        prefix = "" if re.search(r"[|&;<>*]", cmd_str) else "exec "
        return [exe, "-lc", prefix + cmd_str], f"{shell}: {cmd_str}"
    if shell == "none":
        if tokens:
            return list(tokens), f"exec: {cmd_str}"
        import shlex
        return shlex.split(cmd_str, posix=not IS_WIN), f"exec: {cmd_str}"
    raise SystemExit(f"error: unknown --shell '{shell}'")


def _spawn(cmd_str: str, cwd: str, log_path: Path, shell: str, env_extra, tokens=None) -> int:
    """Start the command detached/non-blocking, output -> log_path. Return pid."""
    _ensure_dirs()
    argv, label = _build_argv(cmd_str, shell, tokens)

    env = os.environ.copy()
    for kv in env_extra or []:
        if "=" not in kv:
            raise SystemExit(f"error: --env expects KEY=VALUE, got '{kv}'")
        k, v = kv.split("=", 1)
        env[k] = v

    # Rotate any previous run's log to <name>.log.prev so a reused service name
    # never mixes unrelated runs in one file (that would make logs/grep surface
    # stale errors from a different service). One level of history is kept.
    if log_path.exists() and log_path.stat().st_size > 0:
        try:
            log_path.replace(log_path.with_suffix(log_path.suffix + ".prev"))
        except Exception:
            pass
    logf = open(log_path, "wb", buffering=0)
    logf.write(f"===== svc start {_now()} :: {label} =====\n".encode())
    logf.flush()

    kwargs = dict(
        cwd=cwd or None,
        stdout=logf,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        close_fds=True,
    )
    if IS_WIN:
        # Headless (no console window) + own process group so a Ctrl-C in our
        # console doesn't reach it. The child still outlives this launcher.
        # NOTE: do NOT add DETACHED_PROCESS (0x8) -- it silently breaks the
        # inherited stdout/stderr handle so the log would stay empty.
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        # setsid -> own session/process group, so the service survives this
        # launcher exiting. NOTE: deliberately NOT using prctl(PR_SET_PDEATHSIG)
        # like mcproc does -- mcproc has a long-lived daemon as the parent, but
        # our launcher exits immediately by design, so pdeathsig would kill the
        # service the instant svc.py returns. Detached survival is what we want.
        kwargs["start_new_session"] = True

    p = subprocess.Popen(argv, **kwargs)
    return p.pid


def _wait_health(pid, port, log_path: Path, log_regex, timeout: float) -> str:
    """Block up to `timeout` s for a readiness signal. Returns a status string."""
    if not (port or log_regex):
        return "started"
    pat = re.compile(log_regex) if log_regex else None
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _alive(pid):
            return "exited-early"
        if port and _port_open(int(port)):
            return "ready"
        if pat and log_path.exists():
            try:
                txt = log_path.read_text(encoding="utf-8", errors="replace")
                if pat.search(txt):
                    return "ready"
            except Exception:
                pass
        time.sleep(0.3)
    return "timeout"


def _do_launch(name, cmd_str, cwd, *, port, shell, env, wait_port,
               wait_log, wait_timeout, tokens, verb="started"):
    """Spawn + register + readiness-wait + report. Shared by start/restart."""
    reg = _load()
    log_path = LOG_DIR / f"{name}.log"
    pid = _spawn(cmd_str, cwd, log_path, shell, env, tokens)

    reg[name] = {
        "name": name,
        "pid": pid,
        "cmd": cmd_str,
        "cwd": cwd,
        "port": port,
        "shell": shell,
        "env": env or [],
        # remember readiness config so `restart` can replay it verbatim
        "wait_port": wait_port,
        "wait_log": wait_log,
        "wait_timeout": wait_timeout,
        "log": str(log_path),
        "started_at": _now(),
    }
    _save(reg)

    status = _wait_health(pid, wait_port, log_path, wait_log, wait_timeout)
    print(f"[svc] {verb} '{name}' pid={pid} status={status}")
    print(f"      cwd : {cwd}")
    print(f"      cmd : {cmd_str}")
    print(f"      log : {log_path}")
    if status in ("exited-early", "timeout"):
        print(f"[svc] WARNING: not confirmed healthy ({status}). Check: "
              f"python svc.py logs {name} -n 60")
        _tail(log_path, 25)


def cmd_start(args):
    reg = _load()
    name = args.name
    if not args.command:
        sys.exit("error: no command given. Put the command after `--`, e.g. "
                 "`svc.py start web -- npm run dev`")
    cmd_str = " ".join(args.command)

    existing = reg.get(name)
    if existing and _alive(existing.get("pid")):
        if not args.restart:
            print(f"[svc] '{name}' already running (pid {existing['pid']}). "
                  f"Use --restart to replace it, or `logs {name}` to inspect.")
            return
        _kill_tree(existing["pid"])
        time.sleep(0.5)

    cwd = os.path.abspath(args.cwd) if args.cwd else os.getcwd()
    # For --shell none we keep the exact tokens (no re-quoting); other shells
    # re-parse the joined string themselves.
    tokens = list(args.command) if (args.shell or "auto").lower() == "none" else None
    _do_launch(name, cmd_str, cwd, port=args.port, shell=args.shell,
               env=args.env, wait_port=args.wait_port, wait_log=args.wait_log,
               wait_timeout=args.wait_timeout, tokens=tokens)


def cmd_restart(args):
    """Restart a known service by replaying its stored launch config.

    Handy after a code change: `svc restart web` -- no need to retype the
    command, cwd, env or readiness probe.
    """
    reg = _load()
    rec = reg.get(args.name)
    if not rec:
        sys.exit(f"[svc] no such service '{args.name}' to restart "
                 f"(use `start` first). Known: {', '.join(reg) or '(none)'}")
    if _alive(rec.get("pid")):
        _kill_tree(rec["pid"])
        time.sleep(0.5)
    shell = rec.get("shell", "auto")
    cmd_str = rec["cmd"]
    tokens = cmd_str.split() if str(shell).lower() == "none" else None
    _do_launch(args.name, cmd_str, rec.get("cwd") or os.getcwd(),
               port=rec.get("port"), shell=shell, env=rec.get("env") or [],
               wait_port=rec.get("wait_port"), wait_log=rec.get("wait_log"),
               wait_timeout=rec.get("wait_timeout") or 20.0,
               tokens=tokens, verb="restarted")


# --------------------------------------------------------------------------- #
# stop / kill tree
# --------------------------------------------------------------------------- #
def _kill_tree(pid) -> bool:
    if not pid:
        return False
    ok = False
    if psutil:
        try:
            parent = psutil.Process(pid)
            procs = parent.children(recursive=True) + [parent]
            for p in procs:
                try:
                    p.terminate()
                except Exception:
                    pass
            _, alive = psutil.wait_procs(procs, timeout=4)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
            ok = True
        except psutil.NoSuchProcess:
            ok = True
        except Exception:
            ok = False
    if not ok or not psutil:
        # fallback
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True)
            else:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    time.sleep(1)
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    os.kill(pid, signal.SIGKILL)
            ok = True
        except Exception:
            ok = False
    return ok


def _match_project(rec, project) -> bool:
    """True if a service belongs to `project` (matched against its cwd).

    `project` may be a path (matches that dir or any subdir) or a bare name
    (matches the cwd's basename). None matches everything.
    """
    if not project:
        return True
    cwd = (rec.get("cwd") or "").replace("\\", "/").rstrip("/")
    proj = str(project).replace("\\", "/").rstrip("/")
    if "/" in proj or proj in (".", ".."):
        proj_abs = os.path.abspath(project).replace("\\", "/").rstrip("/")
        return cwd == proj_abs or cwd.startswith(proj_abs + "/")
    return os.path.basename(cwd) == proj


def cmd_stop(args):
    reg = _load()
    if args.all:
        # SAFETY: ~/.agent-bg is a shared, global registry. Without --project,
        # --all stops EVERY service from EVERY project (incl. ones started by
        # other agents or the user). Scope it with --project to be safe.
        names = [n for n, r in reg.items() if _match_project(r, args.project)]
        scope = f"project '{args.project}'" if args.project else "ALL projects (global)"
        if not names:
            print(f"[svc] nothing to stop in {scope}")
            return
        print(f"[svc] stopping {len(names)} service(s) in {scope}: {', '.join(names)}")
    elif args.name:
        names = [args.name]
    else:
        sys.exit("error: give a <name>, or --all (optionally with --project DIR)")

    for name in names:
        rec = reg.get(name)
        if not rec:
            print(f"[svc] no such service '{name}'")
            continue
        pid = rec.get("pid")
        if _alive(pid):
            ok = _kill_tree(pid)
            print(f"[svc] stopped '{name}' (pid {pid}): {'ok' if ok else 'FAILED'}")
        else:
            print(f"[svc] '{name}' was not running")
        reg.pop(name, None)
    _save(reg)


# --------------------------------------------------------------------------- #
# list / status / logs / clean
# --------------------------------------------------------------------------- #
def cmd_list(args):
    reg = _load()
    project = getattr(args, "project", None)
    items = [(n, r) for n, r in reg.items() if _match_project(r, project)]
    if not items:
        where = f" in project '{project}'" if project else ""
        print(f"[svc] no services registered{where}")
        return
    print(f"{'NAME':<14}{'PID':<8}{'STATE':<9}{'PORT':<7}CWD / CMD")
    for name, rec in items:
        state = "alive" if _alive(rec.get("pid")) else "dead"
        port = str(rec.get("port") or "")
        proj = os.path.basename((rec.get("cwd") or "").rstrip("/\\")) or "?"
        print(f"{name:<14}{str(rec.get('pid')):<8}{state:<9}{port:<7}[{proj}] {rec.get('cmd','')}")


def cmd_status(args):
    reg = _load()
    rec = reg.get(args.name)
    if not rec:
        sys.exit(f"[svc] no such service '{args.name}'")
    rec = dict(rec)
    rec["alive"] = _alive(rec.get("pid"))
    if rec.get("port"):
        rec["port_open"] = _port_open(int(rec["port"]))
    print(json.dumps(rec, indent=2, ensure_ascii=False))


def _tail(log_path: Path, n: int):
    if not log_path.exists():
        print(f"[svc] no log yet at {log_path}")
        return
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        print(f"[svc] cannot read log: {e}")
        return
    for ln in lines[-n:]:
        print(ln)


def cmd_logs(args):
    reg = _load()
    rec = reg.get(args.name)
    log_path = Path(rec["log"]) if rec else (LOG_DIR / f"{args.name}.log")
    if not args.follow:
        _tail(log_path, args.n)
        return
    # follow mode
    _tail(log_path, args.n)
    print(f"--- following {log_path} (Ctrl-C to stop) ---")
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    except FileNotFoundError:
        print(f"[svc] no log yet at {log_path}")


def cmd_grep(args):
    """Regex-search a service log and print matches with context lines.

    Borrowed from mcproc: an agent debugging a service usually wants 'the error
    line plus a few lines around it', not the whole tail.
    """
    reg = _load()
    rec = reg.get(args.name)
    log_path = Path(rec["log"]) if rec else (LOG_DIR / f"{args.name}.log")
    if not log_path.exists():
        print(f"[svc] no log yet at {log_path}")
        return
    before = args.before if args.before is not None else args.context
    after = args.after if args.after is not None else args.context
    try:
        pat = re.compile(args.pattern, 0 if args.case_sensitive else re.IGNORECASE)
    except re.error as e:
        sys.exit(f"[svc] bad regex: {e}")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [i for i, ln in enumerate(lines) if pat.search(ln)]
    if not hits:
        print(f"[svc] no matches for /{args.pattern}/ in {args.name}")
        return
    shown, last = set(), -1
    print(f"[svc] {len(hits)} match(es) for /{args.pattern}/ in {args.name}:")
    for h in hits:
        lo, hi = max(0, h - before), min(len(lines), h + after + 1)
        if lo > last + 1 and last != -1:
            print("--")
        for i in range(lo, hi):
            if i in shown:
                continue
            shown.add(i)
            marker = ">" if i == h else " "
            print(f"{marker}{i+1:>6}: {lines[i]}")
        last = hi - 1


def cmd_clean(args):
    reg = _load()
    dead = [n for n, r in reg.items() if not _alive(r.get("pid"))]
    for n in dead:
        reg.pop(n, None)
    _save(reg)
    print(f"[svc] removed {len(dead)} dead entr{'y' if len(dead)==1 else 'ies'}: "
          f"{', '.join(dead) if dead else '(none)'}")


# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(prog="svc.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="start a service detached/non-blocking")
    s.add_argument("name")
    s.add_argument("--cwd", help="working directory (default: current dir)")
    s.add_argument("--port", type=int, help="port this service listens on (metadata)")
    s.add_argument("--wait-port", type=int,
                   help="block until this TCP port accepts connections")
    s.add_argument("--wait-log", help="block until this regex appears in the log")
    s.add_argument("--wait-timeout", type=float, default=20.0,
                   help="max seconds to wait for readiness (default 20)")
    s.add_argument("--restart", action="store_true",
                   help="if already running, kill and restart")
    s.add_argument("--shell", default="auto",
                   choices=["auto", "cmd", "powershell", "pwsh", "sh", "bash", "none"],
                   help="how to run the command (default auto: cmd on Windows, sh on POSIX). "
                        "'none' = exec argv directly with no shell parsing.")
    s.add_argument("--env", action="append", metavar="KEY=VALUE",
                   help="extra environment variable (repeatable), e.g. --env PORT=3000")
    s.add_argument("command", nargs="*",
                   help="the command to run, after `--`")
    s.set_defaults(func=cmd_start)

    l = sub.add_parser("list", help="list all services")
    l.add_argument("-p", "--project", help="only show services whose cwd matches "
                   "this dir (or basename)")
    l.set_defaults(func=cmd_list)

    st = sub.add_parser("status", help="show one service's details")
    st.add_argument("name")
    st.set_defaults(func=cmd_status)

    lg = sub.add_parser("logs", help="show / follow a service log")
    lg.add_argument("name")
    lg.add_argument("-n", type=int, default=40, help="lines to show (default 40)")
    lg.add_argument("-f", "--follow", action="store_true", help="stream new output")
    lg.set_defaults(func=cmd_logs)

    g = sub.add_parser("grep", help="regex-search a service log with context")
    g.add_argument("name")
    g.add_argument("pattern", help="regex to search for in the log")
    g.add_argument("-C", "--context", type=int, default=2,
                   help="lines of context before AND after each match (default 2)")
    g.add_argument("-B", "--before", type=int, help="lines before match (overrides -C)")
    g.add_argument("-A", "--after", type=int, help="lines after match (overrides -C)")
    g.add_argument("-s", "--case-sensitive", action="store_true",
                   help="case-sensitive match (default: insensitive)")
    g.set_defaults(func=cmd_grep)

    r = sub.add_parser("restart", help="restart a service, replaying its stored config")
    r.add_argument("name")
    r.set_defaults(func=cmd_restart)

    sp = sub.add_parser("stop", help="stop a service (kills its child tree)")
    sp.add_argument("name", nargs="?")
    sp.add_argument("--all", action="store_true",
                    help="stop every service (GLOBAL unless you add --project)")
    sp.add_argument("-p", "--project",
                    help="with --all, only stop services whose cwd matches this "
                         "dir (or basename) -- use this to avoid nuking other projects")
    sp.set_defaults(func=cmd_stop)

    c = sub.add_parser("clean", help="remove dead entries from the registry")
    c.set_defaults(func=cmd_clean)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Split off the service command at the first standalone `--` so that the
    # service's own flags (e.g. `--port` for uvicorn) are never parsed by us.
    trailing_cmd = None
    if "--" in argv:
        i = argv.index("--")
        argv, trailing_cmd = argv[:i], argv[i + 1:]
    args = build_parser().parse_args(argv)
    if trailing_cmd is not None:
        args.command = trailing_cmd
    args.func(args)


if __name__ == "__main__":
    main()

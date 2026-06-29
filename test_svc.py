#!/usr/bin/env python3
"""Self-contained tests for svc.py. Runs against an ISOLATED registry
(a temp AGENT_BG_HOME), so it never touches your real ~/.agent-bg.

    python test_svc.py

Exit code 0 = all passed. Uses only the stdlib + psutil + this Python as the
demo service (`python -m http.server`), so it works on any OS.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVC = str(HERE / "svc.py")
PY = sys.executable
passed = failed = 0


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def svc(*args, env):
    return subprocess.run([PY, SVC, *args], capture_output=True, text=True, env=env)


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  - {name}")
    else:
        failed += 1
        print(f" FAIL - {name}   {extra}")


def port_open(p):
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=0.5):
            return True
    except OSError:
        return False


def main():
    home = Path(tempfile.mkdtemp(prefix="svc-test-"))
    env = dict(os.environ, AGENT_BG_HOME=str(home))
    projA = Path(tempfile.mkdtemp(prefix="projA-"))
    projB = Path(tempfile.mkdtemp(prefix="projB-"))
    p1, p2 = free_port(), free_port()
    print(f"isolated AGENT_BG_HOME={home}")

    try:
        # 1. non-blocking start + readiness via --wait-port
        t0 = time.time()
        r = svc("start", "web", "--cwd", str(projA), "--port", str(p1),
                "--wait-port", str(p1), "--wait-timeout", "15",
                "--", PY, "-u", "-m", "http.server", str(p1), env=env)
        dt = time.time() - t0
        check("start returns (non-blocking, < 15s)", dt < 15, f"took {dt:.1f}s")
        check("status=ready reported", "status=ready" in r.stdout, r.stdout)
        check("service actually serves", port_open(p1))

        # 2. list shows it alive with project tag
        r = svc("list", env=env)
        check("list shows web alive", "web" in r.stdout and "alive" in r.stdout)
        check("list shows project tag", f"[{projA.name}]" in r.stdout, r.stdout)

        # 3. status JSON has port_open + stored config
        r = svc("status", "web", env=env)
        st = json.loads(r.stdout)
        check("status port_open true", st.get("port_open") is True)
        check("status stored wait_port", st.get("wait_port") == p1)

        # 4. logs capture output
        socket.create_connection(("127.0.0.1", p1), timeout=1).close()
        time.sleep(0.4)
        r = svc("logs", "web", "-n", "50", env=env)
        check("logs non-empty", "svc start" in r.stdout)

        # 5. grep finds the start header with context
        r = svc("grep", "web", "svc start", env=env)
        check("grep finds match", "match(es)" in r.stdout and ">" in r.stdout)

        # 6. dedup: starting same name refuses
        r = svc("start", "web", "--", PY, "-m", "http.server", str(p1), env=env)
        check("dedup refuses duplicate", "already running" in r.stdout)

        # 7. log rotation: restart creates <name>.log.prev, fresh log
        old_pid = st["pid"]
        r = svc("restart", "web", env=env)
        check("restart reports restarted", "restarted 'web'" in r.stdout)
        check("restart still serves", port_open(p1))
        r2 = svc("status", "web", env=env)
        new_pid = json.loads(r2.stdout)["pid"]
        check("restart changed pid", new_pid != old_pid, f"{old_pid}->{new_pid}")
        check("rotation made .log.prev", (home / "logs" / "web.log.prev").exists())

        # 8. project scoping: start b in projB, scoped stop hits only A
        svc("start", "api", "--cwd", str(projB), "--port", str(p2),
            "--wait-port", str(p2), "--", PY, "-u", "-m", "http.server", str(p2),
            env=env)
        check("second service serves", port_open(p2))
        r = svc("stop", "--all", "--project", str(projA), env=env)
        check("scoped stop names project", "web" in r.stdout and "api" not in r.stdout)
        time.sleep(1)
        check("projA service stopped (port freed)", not port_open(p1))
        check("projB service untouched (still up)", port_open(p2))

        # 9. tree kill: stop the wrapper-shell tree frees the port
        svc("stop", "--all", env=env)
        time.sleep(1)
        check("stop --all frees remaining port", not port_open(p2))

        # 10. registry empty after stopping all
        r = svc("list", env=env)
        check("registry empty at end", "no services" in r.stdout)

    finally:
        svc("stop", "--all", env=env)
        # best-effort cleanup of temp dirs
        import shutil
        for d in (home, projA, projB):
            shutil.rmtree(d, ignore_errors=True)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

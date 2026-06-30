# Design notes

Implementation rationale for `svc.py` — the "why", not the "how to use". These are the traps
this tool handles for you, discovered the hard way and verified on Windows. They live here (and as
comments next to the relevant code) so the README can stay short and user-facing.

## 1. Windows: never use `DETACHED_PROCESS` (0x8)

To start a service detached we set creation flags on Windows. The intuitive choice,
`DETACHED_PROCESS`, **silently breaks inherited stdout/stderr handles** — the child runs fine but
the log file stays completely empty. This was confirmed by A/B testing four flag combinations.

The working combination is:

```text
CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
```

- `CREATE_NO_WINDOW` → headless, no console window pops up.
- `CREATE_NEW_PROCESS_GROUP` → a Ctrl-C in our console won't reach the service.
- The child still **outlives** the launcher (detachment isn't what keeps it alive; the launcher
  simply exiting does).

On POSIX the equivalent is `start_new_session=True` (i.e. `setsid` → its own session/process group).

## 2. Always kill the whole process tree

`npm run dev` is really `shell → node` (and frameworks may spawn more children). Killing only the
PID we recorded leaves an **orphaned `node` still holding the port**, so the next start fails with
`EADDRINUSE`.

We terminate the **entire tree**: `psutil` enumerates `children(recursive=True)`, sends `terminate()`,
waits, then `kill()`s survivors. Fallbacks if `psutil` is unavailable: `taskkill /PID <pid> /T /F`
on Windows, `killpg` (SIGTERM → SIGKILL) on POSIX.

## 3. Inline quotes are fragile across layers

A command typed by an agent passes through agent → Python `argv` → the OS shell. Inline quoting
(e.g. `node -e "..."` with nested quotes) frequently gets mangled and the service fails to start.

Mitigations:

- Put non-trivial logic in a **script file** and run that.
- Use `--shell none` to **exec the argv verbatim** with no shell parsing at all.
- The default path deliberately routes through the platform shell because `npm`/`pnpm`/`mvnw` are
  `.cmd` shims on Windows and *need* a shell to be found.

## 4. Rotate logs per run

Logs are keyed by service **name** (`logs/<name>.log`). If we appended, reusing a name (across
restarts, or a different service that happens to share the name) would **mix unrelated runs into one
file** — and `logs`/`grep` would surface stale errors from a previous run. This actually happened
during development: a fresh `python -m http.server` run was grepped and returned a *previous*
project's Next.js errors.

So every launch **rotates**: the prior `<name>.log` is moved to `<name>.log.prev` (one level of
history kept for post-mortem) and the new run starts from a clean file.

## 5. Don't tie the service's lifetime to the launcher

`mcproc` (a similar Rust tool) uses Linux `prctl(PR_SET_PDEATHSIG)` so children die with their
parent. That's correct *for mcproc* because its parent is a long-lived **daemon**.

This tool is **daemon-less**: the launcher (`svc.py`) starts the service and **exits immediately**.
If we copied `PR_SET_PDEATHSIG`, the service would be killed the instant `svc.py` returns — the exact
opposite of what we want. So we deliberately don't use it; detached survival is the goal.

## 6. The registry is shared and global — scope destructive ops

State lives in one place (`~/.agent-bg/`, overridable via `AGENT_BG_HOME`) so any later session or
agent can see and control what's running. The flip side: it is **shared across all projects**. A
blind `stop --all` would kill another project's (or the user's) services. So `--all` accepts
`--project <dir>` to scope by working directory, and the docs steer agents to always use it when
cleaning up. (Also learned the hard way — a test `stop --all` ran against the shared registry.)

---

These are verified on Windows. The POSIX branches use standard idioms (`setsid` via
`start_new_session`, `psutil`/`killpg`); see the cross-platform note in the README. Run
`python test_svc.py` on any OS to confirm — 20 checks against an isolated temp registry.

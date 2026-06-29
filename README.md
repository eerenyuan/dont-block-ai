# don't-block-ai

**Start a long-running service without blocking your AI agent.**

A tiny, zero-build, cross-platform background-service manager for AI coding agents
(Claude Code, opencode, Cursor, Aider, …) — and for the humans pairing with them.

---

## The problem

Your agent needs a dev server, API, database, or watcher. It runs:

```bash
npm run dev
```

…and **the shell never returns**. The command stays in the foreground forever, the
agent is stuck waiting behind its own service, and the whole session freezes. Every
agent hits this. Some recover quickly; some flail for a long time.

The fix is always the same idea: **start the service detached / non-blocking**, capture
its output, remember what you started, and be able to stop it cleanly later. `svc.py`
does exactly that — reliably, on Windows too.

## The one rule

> **Will this command exit on its own?**
>
> - **Yes** (`npm test`, `go build`, `ls`) → run it normally, wait for the result.
> - **No / it's a long-running service** → **never** run it blocking. Use `svc`.

## Quick start

Requires Python 3.8+ and `psutil` (`pip install psutil`). No install step — it's one file.

```bash
# start a dev server, block only until its port is accepting connections, then return
python svc.py start web --wait-port 5173 -- npm run dev

python svc.py list                 # what's running, alive/dead, port, project
python svc.py logs web -n 80       # see captured output
python svc.py grep web "error" -C 3 # search the log with context
python svc.py restart web          # replay the exact launch config after a code change
python svc.py stop web             # stop it AND its whole child process tree
```

Optional convenience wrappers so you can type `svc` instead of `python svc.py`:
`svc` (bash), `svc.cmd` (cmd), `svc.ps1` (PowerShell) — put this dir on your `PATH`.

## What it gives you

| Need | Command | Notes |
|---|---|---|
| **Start safely** (non-blocking) | `svc start <name> --wait-port N -- <cmd>` | returns instantly; `--wait-port` / `--wait-log` confirm readiness without hanging |
| **See output** | `svc logs <name> -n 80` / `svc grep <name> "re" -C 3` | merged stdout+stderr per service; regex search with context |
| **Know what's running** | `svc list` / `svc status <name>` | file-backed registry — **visible across sessions / new agents** |
| **Stop cleanly** | `svc stop <name>` / `svc stop --all --project DIR` | kills the **whole process tree**, frees the port |
| **Restart after edits** | `svc restart <name>` | replays stored command, cwd, env, readiness probe |

State lives in `~/.agent-bg/` (`registry.json` + `logs/<name>.log`). Override with the
`AGENT_BG_HOME` env var.

## Cross-everything

- **Cross-platform:** Windows / macOS / Linux (tested on Windows, where most tools fall short).
- **Cross-agent:** it's just a CLI — any agent can call it. See `SKILL.md` (Claude Code skill)
  and the snippet below for others.
- **Cross-shell:** `--shell auto|cmd|powershell|pwsh|bash|none`. `none` execs argv directly
  (no quoting surprises); the default routes through the platform shell so `npm`/`pnpm`/`mvnw`
  (which are `.cmd` on Windows) just work.
- **Cross-language:** Node, Python, Java, Go, .NET, docker… copy-paste recipes in `RECIPES.md`.

### Use it from a non-Claude agent

Add this to the agent's rules file (opencode `AGENTS.md`, Cursor rules, …):

> To run any long-lived service (dev server / API / DB / watcher), never run it blocking.
> Use `python /path/to/svc.py start <name> --wait-port <port> -- <command>`, then
> `svc.py list/logs/grep/stop` to inspect and shut it down.

## ⚠️ One safety note

`~/.agent-bg` is a **single, shared** registry. `svc stop --all` with no scope stops
**every** service from **every** project (including ones another agent or you started).
When an agent cleans up, it should scope to its project:

```bash
svc stop --all --project /path/to/this/project   # only this project's services
```

## Hard-won lessons (baked into the code)

These are the traps this tool already handles so you don't have to rediscover them:

- **Windows: do NOT use `DETACHED_PROCESS` (0x8).** It silently breaks stdout/stderr handle
  inheritance and your log file stays empty. The right combo is
  `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`.
- **Always kill the whole process tree.** `npm run dev` is really `shell → node`; killing only
  the parent leaves an orphaned `node` holding the port. Uses `psutil` (≈ `taskkill /T /F`).
- **Inline quotes are fragile** across the agent → Python → shell layers. Put complex logic in a
  script file, or use `--shell none` to pass argv verbatim.
- **Rotate logs per run.** Reusing a service name would otherwise mix unrelated runs in one file
  (and surface stale errors). Each launch starts a fresh log; the previous run is kept as
  `<name>.log.prev`.
- **Don't tie the service's lifetime to the launcher.** This tool is daemon-less and the launcher
  exits immediately, so it deliberately avoids `prctl(PR_SET_PDEATHSIG)` — which would kill the
  service the moment the launcher returns.

## Commands

`start · restart · list · status · logs · grep · stop · clean` — run `python svc.py --help`
or any `... <cmd> --help` for full options.

## Tests

```bash
python test_svc.py     # 20 checks, runs against an isolated temp registry
```

## Prior art / credit

This is a common need; several projects solve parts of it. Notably
[**mcproc**](https://github.com/neptaco/mcproc) (Rust; daemon + CLI + MCP) converged on nearly
the same design — its `grep` (log search with context) and `restart` (replay config) ideas are
borrowed here with thanks. `mcproc` is a great choice on macOS/Linux or when you want native MCP
integration; **don't-block-ai** aims at zero-dependency, single-file, works-on-Windows, and
agent/shell/language-agnostic use. Also in the space: [PM2](https://github.com/Unitech/pm2)
(production Node process manager) and various background-process MCP servers.

## License

MIT — see [LICENSE](LICENSE).

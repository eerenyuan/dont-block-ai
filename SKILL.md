---
name: dont-block-ai
description: 安全地启动、查看、管理和关闭后台常驻服务（dev server、API、数据库、watcher 等）。当需要运行一个长期不退出的进程，又不想让 shell/agent 被它阻塞卡死时使用。提供命名服务注册表、输出日志捕获、就绪探测、跨会话可见、以及干净的进程树关闭。关键词：启动后台服务、dev server、npm run dev、卡住、阻塞、background、后台运行、关闭服务、查看日志。
---

# Background Service Skill

## 这个 skill 解决的核心痛点

启动一个**长期运行、不会自己退出**的服务时（`npm run dev`、`uvicorn`、`flask run`、`vite`、数据库、文件 watcher……），
如果用**阻塞方式**启动，shell 永远不返回，agent 就被自己启动的服务卡死，无法做后面的任何操作。

**判断规则（最重要）：**
> 这条命令会自己结束吗？
>
> - **会**（`npm test`、`go build`、`ls`、`git status`）→ 正常前台运行，等它结束拿结果。
> - **不会 / 是个常驻服务** → **必须**后台、非阻塞启动。用本 skill 的 `svc.py`。

**绝不要这样做**（这些都会卡死 agent）：

- 直接 `npm run dev` / `python app.py`（前台阻塞）
- 以为加了 `&` 就行（PowerShell 里 `&` 不是后台符；Windows 上 `&` 行为也不可靠）
- 用 start-process 但不重定向输出 —— 之后看不到任何日志

## 工具：svc.py（核心 asset）

一个自包含的跨平台后台服务管理器。它做四件事，正好覆盖需求：**安全启动、看到输出、知道启动了什么、需要时关掉**。

- **跨平台**：Windows / macOS / Linux 都可用（依赖 `psutil`，已实测）。
- **跨 agent**：它就是个普通 CLI，Claude Code、opencode、或人手敲都一样用——见 `README.md`。
- **跨 shell**：默认 `--shell auto`（Windows→cmd，POSIX→sh）；可显式 `--shell cmd|powershell|pwsh|bash|none`。
- **跨语言**：Node / Python / Java / Go / .NET / docker 等配方见 `RECIPES.md`。

状态目录 `~/.agent-bg/`（`registry.json` 记录每个服务，`logs/<name>.log` 是合并的 stdout+stderr）。
注册表是文件，所以**换新会话 / 新 agent 也能看到之前启动了什么并关掉它**。

调用前缀（下文用 `svc` 代表，三者等价）：

```text
python <本skill目录>/svc.py   # 最稳，任何 agent/shell（<本skill目录> = SKILL.md 所在目录）
svc        # bash 包装器（需把本目录加进 PATH）
svc.cmd / svc.ps1   # cmd / PowerShell 包装器
```

### 1. 安全启动（非阻塞，立即返回）

命令永远写在 `--` 之后；服务自己的参数（如 uvicorn 的 `--port`）不会被本工具吞掉。

```text
svc start <名字> [--cwd 目录] [--port N] [--wait-port N] [--wait-log "正则"]
          [--wait-timeout 秒] [--shell auto|cmd|powershell|pwsh|bash|none]
          [--env KEY=VALUE ...] [--restart] -- <真正的命令...>
```

- 不带 `--wait-*`：立即返回，不验证。
- `--wait-port 3000`：阻塞**最多** wait-timeout 秒，直到端口可连 → `status=ready`。**推荐**。
- `--wait-log "正则"`：阻塞直到日志出现该正则（适合不监听端口、或要等编译/迁移完成）。
- `--env KEY=VALUE`：注入环境变量（可重复），如 `--env PORT=3000`。
- `--shell none`：直接 exec argv、不经 shell（直接调 `node x.js`/`python x.py` 时最干净）。
  注意：`npm`/`pnpm`/`mvnw` 在 Windows 是 `.cmd`，必须用 shell，别用 `none`。
- 重复同名且仍存活 → 默认**拒绝**（防重复占端口）；要替换用 `--restart`。

```text
svc start web --cwd D:/app --wait-port 5173 -- npm run dev
svc start api --wait-log "Application startup complete" -- python -u -m uvicorn main:app --port 8000
```

### 2. 知道启动了什么

```text
svc list             # 所有服务 + alive/dead + 端口 + 命令
svc status <名字>     # 单个服务详情（pid、cwd、端口是否在监听）—— JSON
```

### 3. 看到输出

```text
svc logs <名字>            # 最后 40 行
svc logs <名字> -n 100     # 最后 100 行
svc logs <名字> --follow   # 实时跟踪（会一直阻塞；agent 用 -n，别用 --follow）
svc grep <名字> "正则" -C 2 # 在日志里正则搜索 + 上下文行（找报错最好用，默认不区分大小写）
```

### 4. 需要时干净关掉（关键：杀整棵进程树）

`npm run dev` 实为 `cmd/sh → node`，只杀父进程会留下孤儿 node 继续占端口。
本工具用 psutil 递归杀掉**整棵子进程树**（Windows 等价 `taskkill /T /F`），已验证端口会被释放。

```text
svc stop <名字>                 # 关掉这一个并从注册表移除
svc stop --all --project <目录>  # 只关这个项目的（推荐：避免误伤别的项目）
svc stop --all                  # ⚠️ 关掉全部（全局！见下方警告）
svc clean                       # 把已死掉的记录从注册表清理
svc list --project <目录>        # 只看某个项目的服务
```

> ⚠️ **`stop --all` 是全局的危险操作**：`~/.agent-bg` 是**所有项目、所有 agent 共享**的注册表。
> 在项目 A 里裸跑 `stop --all` 会把项目 B、甚至用户手动起的服务一起杀掉。
> **agent 做收尾/清理时，一律加 `--project <当前项目目录>` 限定范围**，不要裸 `stop --all`。
> 同理删日志、删注册表前要确认那不是别人的服务（本工具靠 service 名区分，名字会撞）。

### 5. 改完代码重启（无需重打命令）

`restart` 会**重放上次的全部启动配置**（命令、cwd、env、就绪探测），适合改代码后快速重起。

```text
svc restart <名字>   # 用存好的配置重启；同样等到 status=ready
```

## 工作流（agent 标准动作）

1. 起服务前先 `svc list`，避免重复启动。
2. `svc start <名字> --wait-port <端口> -- <命令>`，看到 `status=ready` 才算成功。
3. 若 `status=timeout` / `exited-early`：`svc logs <名字> -n 60` 看报错。
4. 不需要了：`svc stop <名字>`（常驻服务通常保留，交给用户决定）。

## 操作经验（会影响你怎么用）

只列会改变用法的几条；进程树清除、日志轮转、Windows 句柄继承等**已自动处理好、你不用管**，
其内部机制和踩坑原因见仓库的 `DESIGN.md`。

- **命令里的复杂引号很脆弱**：经 bash→python→shell 多层传递，内联 `node -e "..."` 这种容易被吃掉引号而启动失败。**把复杂逻辑写进脚本文件再运行**，或用 `--shell none` 直接传 argv。
- **Python 服务加 `-u`**（或 `--env PYTHONUNBUFFERED=1`），否则 stdout 有缓冲、`logs` 看不到实时输出。
- **普通程序 vs 自我 daemon 化的服务**：svc 适合“前台常驻、需要被后台化”的进程；对会自己 fork 成后台的（`mysqld -d`、`pm2 start`）要用它们的**前台模式**，否则 svc 记录的 pid 会失效。OS 级服务（systemd/Windows 服务/docker）用各自原生管理器。详见 `RECIPES.md`。
- **收尾用 `stop --all --project <目录>` 限定范围**，别裸 `stop --all`（全局，会误伤别的项目）。

## 配套文件

- `svc.py` —— 核心管理器（唯一必需）。命令：`start/restart/list/status/logs/grep/stop/clean`
- `svc` / `svc.cmd` / `svc.ps1` —— bash / cmd / PowerShell 薄包装器
- `RECIPES.md` —— Node/Python/Java/Go/.NET/docker 各语言启动配方 + 程序类型辨析
- `README.md` / `README.zh-CN.md` —— 面向使用者的总览（英 / 中）
- `DESIGN.md` —— 实现原理与踩坑详解（给改代码的人，非使用者）

## 现有方案与借鉴

这是个通用需求，社区已有同类项目。本 skill 的设计与 [mcproc](https://github.com/neptaco/mcproc)（Rust，
daemon+CLI+MCP）几乎收敛到同一套抽象，并直接借鉴了它的两个好特性：**`grep` 日志搜索**（正则+上下文行）
和 **`restart` 重放配置**。区别在于：

- **svc.py**：单文件、零构建、无需常驻 daemon、**Windows 实测可用**、agent/shell/语言全无关 —— 适合本机（尤其 Windows）和"复制即用"。
- **mcproc**：有独立 daemon（进程托管更稳、日志流更强），但**仅 macOS/Linux**、要装 Rust/Homebrew。**在 Mac/Linux 上、或想要 MCP 原生集成时，可直接用 mcproc。**
- 刻意**没有**照搬 mcproc 的 `PR_SET_PDEATHSIG`（让子进程随父进程一起死）：它依赖常驻 daemon 当父进程，而我们的启动器用完即退，照搬会导致服务在 svc.py 一返回就被杀掉。

## 与 Claude Code 原生后台的关系

Claude Code 的 Bash 工具有 `run_in_background: true`，单次会话内最省事，优先可用。
本 skill 的额外价值：**命名服务 + 跨会话持久注册表 + 干净的进程树关闭 + 统一日志**——
当你需要“知道现在到底有哪些服务在跑，并能在任意新会话里把它们关掉”时，用 svc.py。

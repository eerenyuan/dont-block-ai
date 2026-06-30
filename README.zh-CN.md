# don't-block-ai

[English](README.md) | **简体中文**

**启动一个常驻服务，又不让你的 AI agent 被它卡死。**

一个极小、零构建、跨平台的后台服务管理器，给 AI 编程 agent（Claude Code、opencode、Cursor、Aider……）以及和它们结对的人用。

---

## 问题

你的 agent 需要起一个 dev server、API、数据库或文件 watcher，于是它跑：

```bash
npm run dev
```

……然后 **shell 永远不返回**。命令一直挂在前台，agent 被自己启动的服务堵在后面，整个会话就卡死了。
几乎每个 agent 都会撞上这个坑——有的能很快缓过来，有的会困很久。

解法永远是同一个思路：**以分离 / 非阻塞方式启动服务**，捕获它的输出，记住自己启动了什么，事后还能干净地关掉它。
`svc.py` 干的正是这件事——而且在 Windows 上也稳。

## 一条规则

> **这条命令会自己结束吗？**
>
> - **会**（`npm test`、`go build`、`ls`）→ 正常前台运行，等结果。
> - **不会 / 是个常驻服务** → **绝不**阻塞式运行。用 `svc`。

## 快速上手

需要 Python 3.8+ 和 `psutil`（`pip install psutil`）。无需安装——就一个文件。

```bash
# 启动 dev server，只阻塞到端口可连接为止，然后立即返回
python svc.py start web --wait-port 5173 -- npm run dev

python svc.py list                  # 现在有哪些在跑：存活/死亡、端口、所属项目
python svc.py logs web -n 80         # 看捕获到的输出
python svc.py grep web "error" -C 3  # 在日志里带上下文地正则搜索
python svc.py restart web            # 改完代码，按上次的配置原样重启
python svc.py stop web               # 关掉它，连同整棵子进程树
```

可选的便捷包装器，让你能直接敲 `svc` 而不是 `python svc.py`：
`svc`（bash）、`svc.cmd`（cmd）、`svc.ps1`（PowerShell）——把本目录加进 `PATH` 即可。

## 它给你什么

| 需求 | 命令 | 说明 |
|---|---|---|
| **安全启动**（非阻塞） | `svc start <名> --wait-port N -- <命令>` | 立即返回；`--wait-port` / `--wait-log` 确认就绪又不会一直挂住 |
| **看到输出** | `svc logs <名> -n 80` / `svc grep <名> "正则" -C 3` | 每个服务合并的 stdout+stderr；带上下文的正则搜索 |
| **知道在跑什么** | `svc list` / `svc status <名>` | 文件型注册表——**跨会话、跨新 agent 都看得到** |
| **干净关闭** | `svc stop <名>` / `svc stop --all --project 目录` | 杀**整棵进程树**，释放端口 |
| **改完重启** | `svc restart <名>` | 重放存好的命令、cwd、env、就绪探测 |

状态存在 `~/.agent-bg/`（`registry.json` + `logs/<名>.log`）。可用环境变量 `AGENT_BG_HOME` 改位置。

## 跨一切

- **跨平台：** Windows / macOS / Linux（已在 Windows 上实测，而大多数工具恰恰在 Windows 上掉链子）。
- **跨 agent：** 它就是个 CLI——任何 agent 都能调。见 `SKILL.md`（Claude Code skill）以及下方给其他 agent 的片段。
- **跨 shell：** `--shell auto|cmd|powershell|pwsh|bash|none`。`none` 直接 exec argv（没有引号坑）；
  默认走平台 shell，这样 `npm`/`pnpm`/`mvnw`（在 Windows 上是 `.cmd`）都能正常找到。
- **跨语言：** Node、Python、Java、Go、.NET、docker…… 各语言现成配方见 `RECIPES.md`。

### 在非 Claude 的 agent 里用

把这段写进该 agent 的规则文件（opencode 的 `AGENTS.md`、Cursor 的 rules……）：

> 启动任何长期不退出的服务（dev server / API / DB / watcher），绝不阻塞式运行。
> 一律用 `python /path/to/svc.py start <名> --wait-port <端口> -- <命令>`，
> 再用 `svc.py list/logs/grep/stop` 查看与关闭。

## ⚠️ 一条安全提醒

`~/.agent-bg` 是一个**全局、共享**的注册表。不加范围地 `svc stop --all` 会停掉**所有项目**的**所有**服务
（包括别的 agent 或你手动起的）。agent 做收尾时，应当限定到自己的项目：

```bash
svc stop --all --project /path/to/this/project   # 只停这个项目的服务
```

## 为什么靠谱

后台进程那些常见的坑，本工具已经替你处理好了：**Windows 上日志也不会丢**、**关闭时连整棵子进程树一起清掉**
（不留占着端口的孤儿 `node`）、**每次运行用全新日志**所以复用同名也不串台。具体机制、以及每个选择背后的坑，
都写在 [DESIGN.md](DESIGN.md) 里（代码注释里也有）。

## 命令

`start · restart · list · status · logs · grep · stop · clean`——运行 `python svc.py --help`
或 `... <命令> --help` 查看完整选项。

## 测试

```bash
python test_svc.py     # 20 项检查，跑在隔离的临时注册表上
```

## 同类项目 / 致谢

这是个常见需求，已有若干项目解决了其中一部分。尤其是
[**mcproc**](https://github.com/neptaco/mcproc)（Rust；daemon + CLI + MCP）几乎收敛到了同一套设计——
它的 `grep`（带上下文的日志搜索）和 `restart`（重放配置）两个点子在此致谢借鉴。
在 macOS/Linux 上、或想要原生 MCP 集成时，`mcproc` 是很好的选择；而 **don't-block-ai** 追求的是
零依赖、单文件、能在 Windows 上跑、且与 agent/shell/语言无关。同领域还有
[PM2](https://github.com/Unitech/pm2)（生产级 Node 进程管理器）以及各种后台进程 MCP server。

## 许可证

MIT——见 [LICENSE](LICENSE)。

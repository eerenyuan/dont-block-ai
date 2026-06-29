# RECIPES — 各语言/框架的后台启动配方

调用前缀任选一种（三种等价）：

```bash
python "C:\Users\Yuan\.claude\skills\background-service\svc.py"   # 任何 agent / 任何 shell 都行（最稳）
svc            # 若已把 skill 目录加进 PATH（bash 用 svc，cmd 用 svc.cmd，PowerShell 用 svc.ps1）
```

下文统一写 `svc`。命令永远放在 `--` 之后。
**选就绪探测**：监听端口的服务用 `--wait-port`；不监听端口或要等编译/迁移完成的用 `--wait-log "<正则>"`。

---

## Node.js / 前端

```bash
# Vite / 通用 npm dev（Vite 默认 5173）
svc start web --cwd ./frontend --wait-port 5173 -- npm run dev

# pnpm / yarn 同理
svc start web --cwd ./frontend --wait-port 5173 -- pnpm dev

# Next.js（默认 3000）
svc start next --cwd ./app --wait-port 3000 -- npm run dev

# 用 --env 指定端口
svc start web --env PORT=4000 --wait-port 4000 -- npm run dev

# 直接 node 脚本（不需要 shell 解析时用 --shell none，最干净）
svc start api --env PORT=8080 --wait-log "listening" --shell none -- node server.js
```

> Windows 上 `npm`/`pnpm` 是 `.cmd`，必须经由 shell 才能找到——所以**用默认 `--shell auto`（cmd）**，
> 不要对 `npm ...` 用 `--shell none`。只有直接调 `node xxx.js`/`python xxx.py` 这种纯可执行文件才用 `none`。

## Python

```bash
# uvicorn / FastAPI（注意 -u 或让框架自己 flush，否则日志延迟）
svc start api --wait-log "Application startup complete" -- python -u -m uvicorn main:app --host 127.0.0.1 --port 8000

# 用端口探测更简单
svc start api --wait-port 8000 -- python -u -m uvicorn main:app --port 8000

# Flask
svc start api --env FLASK_APP=app.py --wait-port 5000 -- python -u -m flask run --port 5000

# Django dev server
svc start web --wait-port 8000 -- python -u manage.py runserver 127.0.0.1:8000

# gunicorn（生产风格）
svc start api --wait-log "Booting worker" -- gunicorn -b 127.0.0.1:8000 main:app

# 最普通的脚本
svc start worker -- python -u long_worker.py
```

> Python 一定加 `-u`（或 `PYTHONUNBUFFERED=1`），否则 stdout 有缓冲、`svc logs` 看不到实时输出。
> 可以 `--env PYTHONUNBUFFERED=1` 代替 `-u`。

## Java / JVM

```bash
# Spring Boot 打好的 jar（等待 "Started ... in" 这行）
svc start app --wait-log "Started .* in .* seconds" -- java -jar target/app.jar

# Spring Boot via Maven（mvnw 在 Windows 是 mvnw.cmd，用默认 cmd shell）
svc start app --wait-port 8080 -- ./mvnw spring-boot:run

# Gradle
svc start app --wait-port 8080 -- ./gradlew bootRun
```

## Go / Rust / .NET / 其他

```bash
svc start api --wait-port 8080 -- go run ./cmd/server
svc start api --wait-port 8080 -- cargo run
svc start api --wait-log "Now listening on" -- dotnet run
```

## 数据库 / 中间件 / docker

```bash
# docker compose（容器自身就是后台，但 compose up 前台会占住终端）
svc start db --wait-log "database system is ready to accept connections" -- docker compose up postgres

# 文件 watcher / 构建监听（没有端口，用日志或干脆不等）
svc start watch -- npm run watch
```

---

## 普通程序 vs “真正的 service”——很重要

| 类型 | 表现 | 怎么处理 |
|---|---|---|
| **前台常驻程序**（绝大多数 dev server：`npm run dev`、`uvicorn`、`java -jar`） | 自己不退出、一直占着终端 | ✅ 正是 svc 的目标：`svc start ... -- <命令>`，它帮你后台化并记录 pid |
| **会自我 daemon 化的程序**（`mysqld`、某些 `--daemon`/`-d` 模式、`pm2 start`） | 启动后**自己 fork 一个后台进程然后立刻返回** | ⚠️ 别让它再 daemon 一次。**用它的前台/不 daemon 模式**（如 `mysqld --console`、去掉 `-d`、`pm2-runtime`），这样 svc 记录的 pid 才是真正的服务进程，`stop` 才关得掉。否则 `svc list` 会显示 dead 但服务其实还活着 |
| **操作系统级服务**（Windows 服务 / systemd unit / 已在跑的 docker 容器） | 由 OS/容器自己管生命周期 | 用原生管理器：`sc`/`Stop-Service`、`systemctl`、`docker stop`。svc 不接管这些 |

一句话：**svc 适合“本来会一直前台运行、需要被后台化”的进程**；对于“本身就是守护进程/系统服务”的东西，用各自原生的管理方式。

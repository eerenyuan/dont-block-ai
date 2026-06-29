@echo off
rem Thin cmd.exe wrapper: svc start web --wait-port 3000 -- npm run dev
python "%~dp0svc.py" %*

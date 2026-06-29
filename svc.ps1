# Thin PowerShell wrapper: `.\svc.ps1 start web --wait-port 3000 -- npm run dev`
# Put this dir on PATH, or call by path. Requires python on PATH.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& python "$here\svc.py" @args
exit $LASTEXITCODE

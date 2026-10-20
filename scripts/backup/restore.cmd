@echo off
setlocal
if "%~1"=="" (
  echo usage: restore.cmd backups\backup-YYYYMMDD-HHMMSS [backup_id]
  echo first run without backup_id to dry-run and print required_confirmation.
  exit /b 1
)
cd /d %~dp0..\..
if "%~2"=="" (
  if exist launcher\MathPlatform.exe (
    launcher\MathPlatform.exe restore --file "%~1" --dry-run --json
  ) else (
    python tools\launcher\math_platform_launcher.py restore --root "%CD%" --file "%~1" --dry-run --json
  )
) else (
  if exist launcher\MathPlatform.exe (
    launcher\MathPlatform.exe restore --file "%~1" --confirm "%~2" --json
  ) else (
    python tools\launcher\math_platform_launcher.py restore --root "%CD%" --file "%~1" --confirm "%~2" --json
  )
)

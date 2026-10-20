@echo off
setlocal
cd /d %~dp0..\..
if exist launcher\MathPlatform.exe (
  launcher\MathPlatform.exe backup --json
) else (
  python tools\launcher\math_platform_launcher.py backup --root "%CD%" --json
)

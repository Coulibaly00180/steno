@echo off
rem Double-click: installs Steno (Docker Desktop, graphics card, models, desktop shortcuts).
rem The work is done by windows\steno.ps1; see docs\installation-windows.md.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\steno.ps1" install
if errorlevel 1 (
  echo.
  echo L'installation n'a pas abouti : lisez le message ci-dessus.
)
echo.
pause

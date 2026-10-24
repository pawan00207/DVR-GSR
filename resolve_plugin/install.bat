@echo off
rem CorridorKey -> DaVinci Resolve integration installer.
rem Repo ke venv python ko preference deta hai, warna system python.
cd /d "%~dp0.."

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "resolve_plugin\install.py"
) else (
    python "resolve_plugin\install.py"
)

echo.
pause

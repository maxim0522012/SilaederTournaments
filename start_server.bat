@echo off
cd /d "%~dp0"
for /f "usebackq tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="PORT" set "PORT=%%B"
if not defined PORT set "PORT=5000"
echo School table tennis server
echo Open on this computer: http://127.0.0.1:%PORT%
echo Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m waitress --listen=0.0.0.0:%PORT% app:app
pause

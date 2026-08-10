@echo off
cd /d "%~dp0"
for /f "usebackq tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="PORT" set "PORT=%%B"
for /f "usebackq tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="BIND_ADDRESS" set "BIND_ADDRESS=%%B"
if not defined PORT set "PORT=5000"
if not defined BIND_ADDRESS set "BIND_ADDRESS=127.0.0.1"
echo School table tennis server
echo Open on this computer: http://127.0.0.1:%PORT%
echo Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m flask --app app db upgrade
if errorlevel 1 (
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m flask --app app seed-data
if errorlevel 1 (
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m waitress --listen=%BIND_ADDRESS%:%PORT% app:app
pause

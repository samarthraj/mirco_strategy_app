@echo off
REM Restart the Vite dev server + Playground sidecar.
REM
REM Double-click this file, or run from any cmd / PowerShell window:
REM   scripts\restart-services.bat
REM
REM Each service opens in its own new terminal window.

setlocal
set "REPO_ROOT=%~dp0.."
pushd "%REPO_ROOT%"

set SIDECAR_PORT=8899
set VITE_PORT=5173

echo [restart] project: %REPO_ROOT%
echo.

echo === stopping services ===
call :kill_port %SIDECAR_PORT% "Playground sidecar :%SIDECAR_PORT%"
call :kill_port %VITE_PORT%    "Vite dev server :%VITE_PORT%"

REM release ports
timeout /t 2 /nobreak >nul

echo.
echo === starting services ===
start "Playground Sidecar :%SIDECAR_PORT%" cmd /k "cd /d "%REPO_ROOT%" ^&^& python playground_server.py"
echo   launched: Playground Sidecar  (new window)
start "Vite Dev Server :%VITE_PORT%" cmd /k "cd /d "%REPO_ROOT%" ^&^& npm run dev"
echo   launched: Vite Dev Server    (new window)

echo.
echo [restart] done.
echo   Sidecar health: http://127.0.0.1:%SIDECAR_PORT%/playground_api/health
echo   UI:             http://localhost:%VITE_PORT%
popd
endlocal
exit /b 0

:kill_port
REM %1 = port, %2 = label (quoted)
set "PORT=%~1"
set "LABEL=%~2"
set "FOUND_PID="
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%PORT% "') do (
    if not defined FOUND_PID set "FOUND_PID=%%A"
)
if defined FOUND_PID (
    echo   stopping %LABEL% ^(PID %FOUND_PID%^)
    taskkill /PID %FOUND_PID% /F >nul 2>&1
) else (
    echo   %LABEL%: not running
)
exit /b 0

@echo off
rem ripshelf on Windows: start the server, then open the UI in the browser.
rem Close this window (or press Ctrl+C) to stop it. See docs\windows.md.
rem
rem Everything stays in this folder unless you change it in Settings: data\ (database,
rem settings, logs), scratch\ (rips in progress), library\ (Movies, TV) and .venv\.
rem Settings that aren't in the Settings page go in ripshelf.env here
rem (RIPSHELF_PORT=8380, RIPSHELF_HOST=0.0.0.0 to reach it from other machines, ...).

setlocal
set "ROOT=%~dp0..\.."
pushd "%ROOT%"
set "ROOT=%CD%"
popd
if not defined RIPSHELF_HOST set "RIPSHELF_HOST=127.0.0.1"
if not defined RIPSHELF_PORT set "RIPSHELF_PORT=8380"
rem Titles with accents in the console.
set "PYTHONUTF8=1"

set "VENV=%ROOT%\.venv"
if not exist "%VENV%\Scripts\ripshelf.exe" (
    echo Setting up ripshelf in %VENV% ^(first start only^)...
    py -3.12 -m venv "%VENV%" 2>nul || python -m venv "%VENV%" || goto :nopython
    "%VENV%\Scripts\python" -m pip install --quiet --upgrade pip
    "%VENV%\Scripts\python" -m pip install --quiet -r "%ROOT%\requirements.lock" || goto :failed
    "%VENV%\Scripts\python" -m pip install --quiet --no-deps --no-build-isolation -e "%ROOT%" || goto :failed
)

rem The web UI is built once from web\ (it needs Node.js for that, not to run).
if not exist "%ROOT%\web\build\index.html" if not exist "%ROOT%\src\ripshelf\static\index.html" (
    where npm >nul 2>nul || goto :nonode
    echo Building the web UI ^(first start only^)...
    pushd "%ROOT%\web"
    call npm ci --no-audit --no-fund || (popd & goto :failed)
    call npm run build || (popd & goto :failed)
    popd
)

rem Open the browser once the server answers.
start "" /b powershell -NoProfile -Command "$u='http://localhost:%RIPSHELF_PORT%'; for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing \"$u/api/status\" | Out-Null; Start-Process $u; break } catch { Start-Sleep 1 } }"

echo ripshelf: http://localhost:%RIPSHELF_PORT%   (close this window to stop it)
"%VENV%\Scripts\ripshelf.exe"
goto :eof

:nopython
echo Python 3.12 was not found. Install it (winget install Python.Python.3.12), then run this again.
pause
exit /b 1

:nonode
echo The web UI isn't built and Node.js isn't installed. Install it (winget install OpenJS.NodeJS.LTS),
echo then run this again. It's only needed once, to build the UI.
pause
exit /b 1

:failed
echo Setting up ripshelf failed (see above).
pause
exit /b 1

@echo off
rem ripshelf on Windows: start the server, then open the UI in the browser.
rem Close this window (or press Ctrl+C) to stop it. See docs\windows.md.
rem
rem Everything stays in this folder unless you change it in Settings: data\ (database,
rem settings, logs), scratch\ (rips in progress), library\ (Movies, TV) and .venv\.
rem Settings that aren't in the Settings page go in ripshelf.env here
rem (RIPSHELF_PORT=8380, RIPSHELF_HOST=0.0.0.0 to reach it from other machines, ...).
rem
rem After an update (git pull, or a new zip unpacked over this one) it installs the
rem Python packages again if requirements.lock or pyproject.toml changed, and builds
rem the web UI again if anything in web\ changed. Stamps in .venv\ and web\build\ say
rem what they were made from.

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
set "DEPS_STAMP=%VENV%\ripshelf-deps.sha256"
call :hash DEPS "'%ROOT%\requirements.lock','%ROOT%\pyproject.toml'"
set "OLD="
if exist "%DEPS_STAMP%" set /p OLD=<"%DEPS_STAMP%"
if not exist "%VENV%\Scripts\ripshelf.exe" (
    echo Setting up ripshelf in %VENV% ^(first start only^)...
    if not exist "%VENV%\Scripts\python.exe" (
        py -3.12 -m venv "%VENV%" 2>nul || python -m venv "%VENV%" || goto :nopython
    )
    set "OLD="
)
if not "%OLD%"=="%DEPS%" (
    if defined OLD echo ripshelf's Python packages changed, installing them...
    "%VENV%\Scripts\python" -m pip install --quiet --upgrade pip
    "%VENV%\Scripts\python" -m pip install --quiet -r "%ROOT%\requirements.lock" || goto :failed
    "%VENV%\Scripts\python" -m pip install --quiet --no-deps --no-build-isolation -e "%ROOT%" || goto :failed
    > "%DEPS_STAMP%" echo %DEPS%
)

rem The web UI is built from web\ (it needs Node.js for that, not to run), and built
rem again when web\ changes. A UI that came ready-built in src\ripshelf\static is used as it is.
if exist "%ROOT%\src\ripshelf\static\index.html" goto :start
set "UI_STAMP=%ROOT%\web\build\ripshelf-ui.sha256"
call :hash UI "@(Get-ChildItem -LiteralPath '%ROOT%\web\src','%ROOT%\web\static' -Recurse -File | Sort-Object FullName | ForEach-Object FullName) + @('%ROOT%\web\package.json','%ROOT%\web\package-lock.json','%ROOT%\web\vite.config.ts','%ROOT%\web\tsconfig.json')"
set "OLD="
if exist "%UI_STAMP%" if exist "%ROOT%\web\build\index.html" set /p OLD=<"%UI_STAMP%"
if not "%OLD%"=="%UI%" (
    where npm >nul 2>nul || goto :nonode
    if exist "%ROOT%\web\build\index.html" (echo The web UI changed, building it again...) else (echo Building the web UI ^(first start only^)...)
    pushd "%ROOT%\web"
    call npm ci --no-audit --no-fund || (popd & goto :failed)
    call npm run build || (popd & goto :failed)
    popd
    > "%UI_STAMP%" echo %UI%
)

:start
rem Open the browser once the server answers.
start "" /b powershell -NoProfile -Command "$u='http://localhost:%RIPSHELF_PORT%'; for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing \"$u/api/status\" | Out-Null; Start-Process $u; break } catch { Start-Sleep 1 } }"

echo ripshelf: http://localhost:%RIPSHELF_PORT%   (close this window to stop it)
"%VENV%\Scripts\ripshelf.exe"
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" goto :eof
echo.
echo ripshelf stopped with an error (exit code %CODE%, see above and data\logs\ripshelf.log).
pause
exit /b %CODE%

rem :hash NAME FILES sets NAME to one SHA-256 over the files a PowerShell expression lists.
:hash
set "%1="
for /f "usebackq delims=" %%h in (`powershell -NoProfile -Command "$s = [Security.Cryptography.SHA256]::Create(); foreach ($f in %~2) { if (Test-Path -LiteralPath $f) { $b = [IO.File]::ReadAllBytes($f); [void]$s.TransformBlock($b, 0, $b.Length, $null, 0) } }; [void]$s.TransformFinalBlock(@(), 0, 0); [BitConverter]::ToString($s.Hash).Replace('-', '')"`) do set "%1=%%h"
rem No hash (PowerShell failed) means always: installing again does no harm.
if not defined %1 set "%1=unknown-%RANDOM%"
exit /b 0

:nopython
echo Python 3.12 was not found. Install it (winget install Python.Python.3.12), then run this again.
pause
exit /b 1

:nonode
echo The web UI needs building and Node.js isn't installed. Install it (winget install OpenJS.NodeJS.LTS),
echo then run this again. It's only needed to build the UI, after an install or an update.
pause
exit /b 1

:failed
echo Setting up ripshelf failed (see above).
pause
exit /b 1

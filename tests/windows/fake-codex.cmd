@echo off
if /I "%AACC_FAKE_CODEX_MODE%"=="timeout" (
    "%AACC_FAKE_CODEX_PYTHON%" "%~dp0fake_codex_timeout.py" %*
    exit /b %ERRORLEVEL%
)
"%AACC_FAKE_CODEX_PYTHON%" "%~dp0fake_codex_server.py" %*
exit /b %ERRORLEVEL%

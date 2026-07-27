@echo off
if defined AACC_TEST_DESCENDANT_PID_FILE (
    "%FAKE_CODEX_PYTHON%" "%~dp0spawn_descendant.py" %*
    exit /b %ERRORLEVEL%
)
"%FAKE_CODEX_PYTHON%" "%~dp0fake_codex_server.py" %*
exit /b %ERRORLEVEL%

@echo off
cd /d "%~dp0"
uv run python -m github_intelligence.dashboard
pause

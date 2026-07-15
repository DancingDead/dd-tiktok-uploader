@echo off
REM Lance l'usine en production (Flask + build React) via waitress.
REM À déclencher "à l'ouverture de session" par le Planificateur de tâches.
REM Prérequis : uv sur le PATH, `uv sync` fait, `cd frontend && npm run build` fait.

cd /d "%~dp0.."
set HOST=0.0.0.0
set PORT=8765
uv run python serve.py

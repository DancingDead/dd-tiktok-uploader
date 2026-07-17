@echo off
REM Lance l'usine en production (Flask + build React) via waitress.
REM Déclenché "au démarrage" (session 0, arrière-plan invisible) par le
REM Planificateur de tâches — tourne que quelqu'un soit connecté ou non.
REM Prérequis : uv installé, `uv sync` fait, `cd frontend && npm run build` fait.

cd /d "%~dp0.."

REM --- PATH déterministe (session 0 ne charge pas toujours le User PATH) ---
REM uv et ffmpeg sont installés via winget (scope utilisateur "Dancing Dead").
set "UVDIR=%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe"
set "FFROOT=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
set "FFDIR="
for /d %%D in ("%FFROOT%\ffmpeg-*-full_build") do set "FFDIR=%%D\bin"
set "PATH=%UVDIR%;%FFDIR%;%PATH%"

set HOST=0.0.0.0
set PORT=8765
uv run python serve.py

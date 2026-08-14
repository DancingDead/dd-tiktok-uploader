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

REM --- Mode UTF-8 : evite les crashs d'encodage (cp1252) sur les logs
REM contenant des accents / fleches (ex. "fenetre 1.0->2.0s"), y compris
REM dans les sous-process de generation. ---
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set HOST=0.0.0.0
set PORT=8765

REM --- Journal de demarrage ---
REM La tache DD-Usine se declenche au boot, en session 0 : personne ne voit
REM l'ecran. Sans ce journal, un demarrage rate ne laisse AUCUNE trace et la
REM panne est indiagnosticable apres coup (cas vecu). On note donc l'etat des
REM prerequis resolus juste au-dessus (uv, ffmpeg, le venv) AVANT de lancer
REM quoi que ce soit : si %LOCALAPPDATA% ou le disque ne sont pas encore
REM disponibles si tot au boot, ca se lit ici et nulle part ailleurs.
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOGFILE=%LOGDIR%\usine.log"
REM Rotation grossiere : au-dela de 10 Mo on repart d'un fichier neuf, en
REM gardant la generation precedente. Un seul niveau suffit — ce journal sert
REM au dernier demarrage, pas a l'historique long.
for %%F in ("%LOGFILE%") do if %%~zF GTR 10485760 move /y "%LOGFILE%" "%LOGFILE%.1" >nul

>>"%LOGFILE%" echo(
>>"%LOGFILE%" echo ==== demarrage %DATE% %TIME% ====
>>"%LOGFILE%" echo repo=%CD%
>>"%LOGFILE%" echo uv=%UVDIR%
if exist "%UVDIR%\uv.exe" (>>"%LOGFILE%" echo uv.exe=OK) else (>>"%LOGFILE%" echo uv.exe=ABSENT)
>>"%LOGFILE%" echo ffmpeg=%FFDIR%
if exist "%FFDIR%\ffmpeg.exe" (>>"%LOGFILE%" echo ffmpeg.exe=OK) else (>>"%LOGFILE%" echo ffmpeg.exe=ABSENT)
if exist ".venv\Scripts\python.exe" (>>"%LOGFILE%" echo venv=OK) else (>>"%LOGFILE%" echo venv=ABSENT)

uv run python serve.py >>"%LOGFILE%" 2>&1
>>"%LOGFILE%" echo ==== arret %DATE% %TIME% code=%ERRORLEVEL% ====

@echo off
REM Demarre LM Studio en HEADLESS (daemon llmster, sans GUI) :
REM   1) daemon up        -> moteur d'inference headless
REM   2) load --gpu max   -> charge le modele en offload GPU complet
REM   3) server start     -> serveur compatible OpenAI sur le port 1234
REM Declenche AU DEMARRAGE (session 0) par le Planificateur de taches, et
REM utilise aussi par le watchdog de redemarrage en cas de crash.

set "LMS=%USERPROFILE%\.lmstudio\bin\lms.exe"

"%LMS%" daemon up
"%LMS%" unload -a
"%LMS%" load qwen2.5-7b-instruct-1m --gpu max -y
"%LMS%" server start --port 1234

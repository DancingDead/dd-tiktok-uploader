# update.ps1 — met à jour le code depuis GitHub et redémarre proprement l'usine.
# Pensé pour être lancé À DISTANCE via SSH (session élevée, cf. LocalAccountTokenFilterPolicy).
#
#   powershell -ExecutionPolicy Bypass -File deploy\update.ps1
#
# Étapes : git pull -> uv sync -> build du front -> redémarrage propre de l'usine.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

Write-Host "=== git pull ===" -ForegroundColor Cyan
git pull

Write-Host "=== uv sync ===" -ForegroundColor Cyan
uv sync

Write-Host "=== build du front ===" -ForegroundColor Cyan
Set-Location (Join-Path $repo "frontend")
npm install --no-fund --no-audit
npm run build
Set-Location $repo

Write-Host "=== redemarrage de l'usine ===" -ForegroundColor Cyan
& (Join-Path $repo "deploy\restart-usine.ps1")

Write-Host "=== termine ===" -ForegroundColor Green

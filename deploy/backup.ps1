# backup.ps1 — sauvegarde la base et les médias de l'usine vers un dossier daté.
# Usage :  powershell -ExecutionPolicy Bypass -File deploy\backup.ps1 [-Dest <dossier>]
# À planifier (ex. quotidien) via le Planificateur de tâches.

param([string]$Dest = "$env:USERPROFILE\dd-backups")

$root  = Split-Path -Parent $PSScriptRoot          # racine du projet
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$out   = Join-Path $Dest $stamp
New-Item -ItemType Directory -Force -Path $out | Out-Null

# Base SQLite (les données de la plateforme)
Copy-Item (Join-Path $root "platform.db") $out -ErrorAction SilentlyContinue

# Médias : vidéos produites, sons, clips (robocopy = miroir incrémental)
foreach ($d in @("data", "tracks", "clips")) {
  $src = Join-Path $root $d
  if (Test-Path $src) {
    robocopy $src (Join-Path $out $d) /E /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
  }
}

Write-Host "Sauvegarde terminee -> $out"

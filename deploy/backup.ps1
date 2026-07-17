<#
.SYNOPSIS
    Sauvegarde des données de l'usine (la tour en est la seule copie).

.DESCRIPTION
    Sauvegarde vers $Dest :
      - platform.db  : snapshot à chaud daté (API backup SQLite, sûr même si la
                       webui écrit) dans $Dest\db-snapshots\, avec rétention.
      - tracks\ clips\ data\ : copie NON destructive (robocopy /E /XO) vers
                       $Dest\current\. On ne supprime jamais côté sauvegarde :
                       un fichier effacé par erreur sur la tour reste récupérable.
    Journalise dans $Dest\backup.log. Si $Dest est injoignable (disque
    débranché), log un avertissement et sort proprement (code 0) pour ne pas
    faire échouer la tâche planifiée.

    -Register installe une tâche planifiée quotidienne qui tourne même sans
    session ouverte.

.PARAMETER RepoPath   Racine du projet (contient platform.db, tracks, clips, data).
.PARAMETER Dest       Racine de sauvegarde (ex. disque externe E:\dd-backup).
.PARAMETER KeepDays   Rétention des snapshots de base, en jours (défaut 30).
.PARAMETER At         Heure de la tâche planifiée (défaut 03:00), avec -Register.
.PARAMETER Register   Installe la tâche planifiée au lieu de lancer la sauvegarde.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\backup.ps1 -Dest E:\dd-backup
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\backup.ps1 -Dest E:\dd-backup -Register
#>
[CmdletBinding()]
param(
    [string]$RepoPath = "C:\Users\dd\dd-tiktok-uploader",
    [Parameter(Mandatory = $true)][string]$Dest,
    [int]$KeepDays = 30,
    [string]$At = "03:00",
    [switch]$Register
)

$ErrorActionPreference = "Stop"

function Register-Task {
    # Tâche quotidienne, exécutée même sans session (compte SYSTEM).
    # Destination réseau/cloud → remplacer le principal par le compte dd.
    $script = $MyInvocation.ScriptName
    if (-not $script) { $script = $PSCommandPath }
    $args = "-ExecutionPolicy Bypass -NoProfile -File `"$script`" -RepoPath `"$RepoPath`" -Dest `"$Dest`" -KeepDays $KeepDays"
    $action    = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $args
    $trigger   = New-ScheduledTaskTrigger -Daily -At $At
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName "dd-backup" -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "Tâche planifiée 'dd-backup' installée (quotidienne à $At, compte SYSTEM)." -ForegroundColor Green
    Write-Host "Destination réseau/cloud ? Édite la tâche pour la faire tourner sous le compte dd." -ForegroundColor DarkYellow
}

function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    try { Add-Content -Path (Join-Path $Dest "backup.log") -Value $line -ErrorAction SilentlyContinue } catch {}
}

function Invoke-Backup {
    if (-not (Test-Path $RepoPath)) { throw "RepoPath introuvable : $RepoPath" }

    # Destination injoignable (disque débranché) : on sort proprement.
    $destRoot = Split-Path -Qualifier $Dest 2>$null
    if ($destRoot -and -not (Test-Path $destRoot)) {
        Write-Host "Destination $Dest injoignable (disque débranché ?) — sauvegarde ignorée." -ForegroundColor DarkYellow
        return
    }
    New-Item -ItemType Directory -Force -Path $Dest, (Join-Path $Dest "db-snapshots"), (Join-Path $Dest "current") | Out-Null
    Write-Log "=== Début sauvegarde depuis $RepoPath ==="

    # 1) Base SQLite : snapshot à chaud daté.
    $db = Join-Path $RepoPath "platform.db"
    if (Test-Path $db) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $snap  = Join-Path $Dest "db-snapshots\platform-$stamp.db"
        Push-Location $RepoPath
        try { uv run python deploy\_backup_db.py "$db" "$snap" | Out-Null }
        finally { Pop-Location }
        Write-Log "DB snapshot : $snap"
    } else {
        Write-Log "AVERTISSEMENT : platform.db absent ($db)"
    }

    # 2) Médias : copie non destructive (jamais de suppression côté backup).
    foreach ($sub in @("tracks", "clips", "data")) {
        $srcDir = Join-Path $RepoPath $sub
        if (-not (Test-Path $srcDir)) { continue }
        $dstDir = Join-Path $Dest "current\$sub"
        robocopy "$srcDir" "$dstDir" /E /XO /R:2 /W:5 /NP /NFL /NDL | Out-Null
        # robocopy : codes < 8 = succès (bits d'info) ; >= 8 = échec réel.
        if ($LASTEXITCODE -ge 8) { Write-Log "ERREUR robocopy $sub (code $LASTEXITCODE)" }
        else { Write-Log "Médias copiés : $sub (code $LASTEXITCODE)" }
    }

    # 3) Rétention des snapshots de base.
    $cutoff = (Get-Date).AddDays(-$KeepDays)
    Get-ChildItem (Join-Path $Dest "db-snapshots") -Filter "platform-*.db" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        ForEach-Object { Remove-Item $_.FullName -Force; Write-Log "Purge snapshot : $($_.Name)" }

    Write-Log "=== Fin sauvegarde ==="
}

if ($Register) { Register-Task } else { Invoke-Backup }

# restart-usine.ps1 — redémarrage PROPRE de l'usine web.
#
# Pourquoi : `Stop-ScheduledTask DD-Usine` ne tue PAS toujours le serveur
# (le process python/waitress est ré-parenté et survit, gardant le port 8765).
# Résultat : après un `git pull`, l'ancien code continue de tourner. Ce script
# tue explicitement tous les serveurs serve.py avant de relancer la tâche.
#
# À lancer en PowerShell ADMIN (pour tuer les process de la session 0) :
#   powershell -ExecutionPolicy Bypass -File "C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader\deploy\restart-usine.ps1"

$ErrorActionPreference = "SilentlyContinue"

Stop-ScheduledTask -TaskName "DD-Usine"
Start-Sleep -Seconds 2

# Tue TOUS les serveurs serve.py, toutes sessions confondues.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'serve\.py' } |
    ForEach-Object {
        Write-Host ("kill serve.py PID " + $_.ProcessId)
        Stop-Process -Id $_.ProcessId -Force
    }

# Attend que le port 8765 se libère.
$tries = 0
while ((Get-NetTCPConnection -LocalPort 8765 -State Listen) -and $tries -lt 15) {
    Get-NetTCPConnection -LocalPort 8765 -State Listen |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force }
    Start-Sleep -Seconds 1
    $tries++
}
Write-Host ("8765 libre: " + (-not [bool](Get-NetTCPConnection -LocalPort 8765 -State Listen)))

# Relance une instance fraîche.
Start-ScheduledTask -TaskName "DD-Usine"
Start-Sleep -Seconds 8
$owner = Get-NetTCPConnection -LocalPort 8765 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique
Write-Host ("Serveur PID: " + ($owner -join ','))
try {
    Write-Host ("SITE -> HTTP " + (Invoke-WebRequest 'http://localhost:8765/' -UseBasicParsing -TimeoutSec 8).StatusCode)
} catch {
    Write-Host "SITE DOWN"
}

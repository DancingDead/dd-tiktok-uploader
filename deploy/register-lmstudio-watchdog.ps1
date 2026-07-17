# register-lmstudio-watchdog.ps1
# Enregistre la tache DD-LMStudio-Watchdog (relance LM Studio headless si crash).
# A lancer en PowerShell ADMIN :
#   powershell -ExecutionPolicy Bypass -File "C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader\deploy\register-lmstudio-watchdog.ps1"

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$wd   = Join-Path $repo "deploy\watchdog-lmstudio.ps1"
$user = "$env:USERDOMAIN\$env:USERNAME"

$action    = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$wd`""
$t1        = New-ScheduledTaskTrigger -AtStartup
$t2        = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 3) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings  = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName "DD-LMStudio-Watchdog" -Action $action -Trigger $t1,$t2 -Settings $settings -Principal $principal -Description "Relance LM Studio headless si crash (check 3 min)" -Force | Out-Null

Write-Host "OK - DD-LMStudio-Watchdog enregistree pour l'utilisateur $user"
Write-Host ""
Write-Host "=== Taches DD ==="
Get-ScheduledTask -TaskName "DD-*" | Select-Object TaskName, State | Format-Table -AutoSize

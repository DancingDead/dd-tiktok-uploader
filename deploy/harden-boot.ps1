# harden-boot.ps1 — « j'appuie sur le bouton le matin, je ne fais rien d'autre ».
#
# Rend le démarrage de la tour autonome et résistant aux reboots Windows Update :
#   1. Auto-login blindé (Sysinternals Autologon, mot de passe chiffré dans la LSA)
#   2. Reprise de session automatique après une MàJ (ARSO)
#   3. Pas de redémarrage surprise en journée tant qu'une session est ouverte
#
# Contexte : LM Studio a besoin d'une SESSION INTERACTIVE ouverte (GPU), et les
# tâches DD-Usine / DD-LMStudio sont déclenchées ONLOGON. Sans ouverture de session
# automatique, un reboot (ex. Windows Update) retombe sur l'écran de login et TOUT
# reste éteint (Flask, LM Studio, et l'accès Tailscale de l'équipe).
#
# Usage (PowerShell EN ADMINISTRATEUR) :
#   powershell -ExecutionPolicy Bypass -File deploy\harden-boot.ps1
# Options :
#   -User <compte>   compte à connecter automatiquement (défaut : session courante)

param([string]$User = $env:USERNAME)

# --- Doit tourner en administrateur ---------------------------------------
$admin = ([Security.Principal.WindowsPrincipal] `
  [Security.Principal.WindowsIdentity]::GetCurrent()
  ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Error "Relance ce script dans un PowerShell OUVERT EN ADMINISTRATEUR."
  exit 1
}

$edition = (Get-CimInstance Win32_OperatingSystem).Caption
Write-Host "Edition Windows detectee : $edition`n" -ForegroundColor Cyan

# --- 1. Auto-login (Sysinternals Autologon) --------------------------------
# On préfère Autologon à la case netplwiz : il stocke le mot de passe chiffré
# (secret LSA) et ne se « décoche » pas tout seul après certaines mises à jour.
Write-Host "[1/3] Configuration de l'auto-login pour '$User'..." -ForegroundColor Yellow
try {
  $zip = Join-Path $env:TEMP "Autologon.zip"
  $dir = Join-Path $env:TEMP "Autologon"
  Invoke-WebRequest -Uri "https://download.sysinternals.com/files/Autologon.zip" `
                    -OutFile $zip -UseBasicParsing
  Expand-Archive -Path $zip -DestinationPath $dir -Force
  $exe = Join-Path $dir "Autologon64.exe"
  if (-not (Test-Path $exe)) { $exe = Join-Path $dir "Autologon.exe" }

  $sec   = Read-Host "Mot de passe Windows de '$User'" -AsSecureString
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
             [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))

  # Domaine "." = compte local de cette machine
  & $exe -accepteula $User "." $plain | Out-Null
  Remove-Variable plain
  Write-Host "      Auto-login active." -ForegroundColor Green
} catch {
  Write-Warning "Echec Autologon : $($_.Exception.Message)"
  Write-Warning "Solution de repli : lance 'netplwiz', decoche 'Les utilisateurs doivent entrer un nom...' et saisis le mot de passe."
}

# --- 2. Reprise de session automatique apres une MaJ (ARSO) ----------------
# Permet a Windows de rouvrir la session tout seul apres un reboot Windows
# Update, ce qui relance les taches ONLOGON (donc Flask + LM Studio).
Write-Host "`n[2/3] Activation de la reprise de session auto apres MaJ (ARSO)..." -ForegroundColor Yellow
$sys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
New-Item -Path $sys -Force | Out-Null
Set-ItemProperty -Path $sys -Name 'DisableAutomaticRestartSignOn' -Value 0 -Type DWord
Write-Host "      ARSO active (DisableAutomaticRestartSignOn=0)." -ForegroundColor Green
Write-Host "      Coche AUSSI le reglage GUI : Parametres > Comptes > Options de connexion >" -ForegroundColor DarkGray
Write-Host "      'Utiliser mes infos de connexion pour terminer automatiquement la config apres une MaJ'." -ForegroundColor DarkGray

# --- 3. Pas de reboot surprise tant qu'une session est ouverte -------------
# L'auto-login garde toujours un utilisateur connecte, donc cette strategie
# empeche Windows Update de redemarrer la tour dans ton dos en pleine journee.
Write-Host "`n[3/3] Blocage des redemarrages automatiques en journee..." -ForegroundColor Yellow
$au = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
New-Item -Path $au -Force | Out-Null
Set-ItemProperty -Path $au -Name 'NoAutoRebootWithLoggedOnUsers' -Value 1 -Type DWord
Write-Host "      Cle posee (NoAutoRebootWithLoggedOnUsers=1)." -ForegroundColor Green
if ($edition -match 'Home') {
  Write-Warning "Windows HOME : cette strategie peut etre ignoree."
  Write-Warning "Regle en plus des HEURES D'ACTIVITE larges : Parametres > Windows Update > Options avancees > Heures d'activite (fenetre de 18h)."
}

Write-Host "`nTermine. Fais un reboot de test : la tour doit rouvrir la session et tout relancer SEULE." -ForegroundColor Cyan

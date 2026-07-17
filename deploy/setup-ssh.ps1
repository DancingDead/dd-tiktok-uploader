# setup-ssh.ps1 — installe et durcit un accès SSH distant à la tour Dancing Dead.
#
# But : pouvoir administrer l'usine depuis le Mac (git pull, uv sync, db.py add-member,
# restart-usine.ps1, update.ps1...) via un terminal SSH, à travers Tailscale UNIQUEMENT
# (le port 22 n'est jamais exposé sur Internet, contrairement au site en Funnel).
#
# À lancer UNE SEULE FOIS, dans un PowerShell ADMINISTRATEUR sur la tour :
#
#   powershell -ExecutionPolicy Bypass -File deploy\setup-ssh.ps1 -PublicKey "ssh-ed25519 AAAA... toi@mac"
#
# Idempotent : relançable sans risque. La clé passée écrase le fichier de clés admin.

param(
    [Parameter(Mandatory = $true)]
    [string]$PublicKey
)

$ErrorActionPreference = "Stop"

# --- Garde-fou : élévation obligatoire -------------------------------------
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    throw "Ce script doit tourner dans un PowerShell ADMINISTRATEUR (clic droit > Exécuter en tant qu'administrateur)."
}

# --- 1. Installer OpenSSH Server -------------------------------------------
Write-Host "=== 1/6 Installation d'OpenSSH Server ===" -ForegroundColor Cyan
$cap = Get-WindowsCapability -Online -Name "OpenSSH.Server*"
if ($cap.State -ne "Installed") {
    Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0" | Out-Null
    Write-Host "OpenSSH.Server installé."
} else {
    Write-Host "OpenSSH.Server déjà installé."
}

# --- 2. Service sshd : démarrage auto + lancé ------------------------------
Write-Host "=== 2/6 Service sshd (démarrage auto) ===" -ForegroundColor Cyan
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
# ssh-agent utile mais optionnel côté serveur.
Set-Service -Name ssh-agent -StartupType Automatic -ErrorAction SilentlyContinue

# --- 3. Shell par défaut = PowerShell --------------------------------------
Write-Host "=== 3/6 Shell par défaut = PowerShell ===" -ForegroundColor Cyan
$psPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
    -Value $psPath -PropertyType String -Force | Out-Null
Write-Host "DefaultShell -> $psPath"

# --- 4. Pare-feu : port 22 restreint aux pairs Tailscale -------------------
Write-Host "=== 4/6 Pare-feu (Tailscale only) ===" -ForegroundColor Cyan
# La règle par défaut créée par OpenSSH ouvre le 22 à TOUT le réseau local : on la coupe.
Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue |
    Set-NetFirewallRule -Enabled False
if (Get-NetFirewallRule -DisplayName "DD-SSH-Tailscale" -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName "DD-SSH-Tailscale"
}
New-NetFirewallRule -DisplayName "DD-SSH-Tailscale" -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort 22 -RemoteAddress "100.64.0.0/10" -Profile Any | Out-Null
Write-Host "Port 22 autorisé uniquement depuis 100.64.0.0/10 (Tailscale)."

# --- 5. Jeton admin complet sur les sessions réseau ------------------------
# Sans ça, une session SSH du compte admin local reçoit un jeton FILTRÉ (UAC) et ne
# peut pas tuer les process de la session 0 → restart-usine.ps1 échouerait.
Write-Host "=== 5/6 LocalAccountTokenFilterPolicy=1 ===" -ForegroundColor Cyan
New-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" `
    -Name LocalAccountTokenFilterPolicy -Value 1 -PropertyType DWord -Force | Out-Null

# --- 6. Déposer la clé publique (compte admin -> administrators_authorized_keys) ---
Write-Host "=== 6/6 Clé publique autorisée ===" -ForegroundColor Cyan
# Pour un membre du groupe Administrateurs, sshd lit CE fichier, pas ~/.ssh/authorized_keys.
$akFile = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
$key = $PublicKey.Trim()
if ($key -notmatch "^(ssh-ed25519|ssh-rsa|ecdsa-)") {
    throw "La clé fournie ne ressemble pas à une clé publique OpenSSH : '$key'"
}
Set-Content -Path $akFile -Value $key -Encoding ascii -Force
# ACL stricte imposée par sshd : SYSTEM + Administrateurs uniquement, pas d'héritage.
icacls $akFile /inheritance:r /grant "*S-1-5-18:F" /grant "*S-1-5-32-544:F" | Out-Null
Write-Host "Clé écrite dans $akFile"

# --- Redémarrage du service pour appliquer shell + clé ---------------------
Restart-Service sshd

Write-Host ""
Write-Host "=== TERMINÉ ===" -ForegroundColor Green
Write-Host "Depuis le Mac, connecte-toi avec :" -ForegroundColor Green
Write-Host '    ssh "Dancing Dead@dancingdeadhq"' -ForegroundColor Yellow
Write-Host "  (ou  ssh `"Dancing Dead@100.74.173.64`"  si MagicDNS ne résout pas)"
Write-Host ""
Write-Host "Note : l'auth par mot de passe reste activée en secours. Une fois la clé"
Write-Host "validée, tu peux la couper en ajoutant 'PasswordAuthentication no' dans"
Write-Host "  $env:ProgramData\ssh\sshd_config  puis  Restart-Service sshd"

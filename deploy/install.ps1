<#
.SYNOPSIS
    Installe l'usine à vidéos Dancing Dead sur la tour Windows.

.DESCRIPTION
    Accélérateur du RUNBOOK.md. Automatise ce qui est automatisable :
    dépendances (winget), clone + uv sync, OpenSSH Server, et l'enregistrement
    des services Windows (webui via NSSM, cloudflared). Les étapes qui exigent
    une authentification interactive ou le dashboard Cloudflare ne sont PAS
    automatisées : le script s'arrête et t'indique la commande exacte à lancer.

    À lancer depuis un PowerShell ADMINISTRATEUR, dans la session `dd`.

    NB : script fourni comme aide — relis-le avant de l'exécuter. Il est
    idempotent autant que possible (winget ignore ce qui est déjà installé).

.PARAMETER RepoUrl
    URL git du projet.

.PARAMETER RepoPath
    Où cloner le projet.

.PARAMETER Stage
    all (défaut) | deps | project | ssh | services
        deps     : winget (git, uv, ffmpeg, cloudflared, tailscale, nssm)
        project  : git clone/pull + uv sync + pytest
        ssh      : OpenSSH Server
        services : cloudflared + webui (NSSM). Suppose le tunnel déjà créé.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\install.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\install.ps1 -Stage services
#>
[CmdletBinding()]
param(
    [string]$RepoUrl  = "https://github.com/DancingDead/dd-tiktok-uploader.git",
    [string]$RepoPath = "C:\Users\dd\dd-tiktok-uploader",
    [ValidateSet("all", "deps", "project", "ssh", "services")]
    [string]$Stage    = "all"
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]$id
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Lance ce script dans un PowerShell ADMINISTRATEUR."
    }
}

function Winget-Install($id, $extra = @()) {
    Write-Host "→ winget install $id" -ForegroundColor Cyan
    winget install --id $id --accept-source-agreements --accept-package-agreements @extra
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
        # -1978335189 = "déjà installé / aucune mise à jour" : non bloquant.
        Write-Warning "winget $id a renvoyé $LASTEXITCODE (peut-être déjà installé)."
    }
}

function Stage-Deps {
    Write-Host "`n== Dépendances (winget) ==" -ForegroundColor Yellow
    Winget-Install "Git.Git"            @("--scope", "machine")
    Winget-Install "astral-sh.uv"
    Winget-Install "Gyan.FFmpeg"        @("--scope", "machine")
    Winget-Install "Cloudflare.cloudflared"
    Winget-Install "tailscale.tailscale"
    Winget-Install "NSSM.NSSM"
    Write-Host "Si NSSM n'est pas trouvé plus bas, installe-le à la main depuis https://nssm.cc/download" -ForegroundColor DarkYellow
    Write-Host "→ Rouvre un terminal après cette étape pour rafraîchir le PATH." -ForegroundColor DarkYellow
}

function Stage-Project {
    Write-Host "`n== Projet (clone + uv sync + tests) ==" -ForegroundColor Yellow
    if (Test-Path $RepoPath) {
        Write-Host "Repo présent, git pull…"
        git -C $RepoPath pull
    } else {
        git clone $RepoUrl $RepoPath
    }
    Push-Location $RepoPath
    try {
        uv sync
        Write-Host "→ pytest…" -ForegroundColor Cyan
        uv run pytest -q
    } finally { Pop-Location }
    Write-Host "Pense à créer les membres :  uv run python db.py add-member <nom>" -ForegroundColor DarkYellow
}

function Stage-Ssh {
    Write-Host "`n== OpenSSH Server ==" -ForegroundColor Yellow
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
    Set-Service sshd -StartupType Automatic
    Start-Service sshd
    Write-Host "sshd démarré. Ajoute ta clé publique du Mac dans :" -ForegroundColor DarkYellow
    Write-Host "  C:\Users\dd\.ssh\authorized_keys" -ForegroundColor DarkYellow
    Write-Host "  (+ C:\ProgramData\ssh\administrators_authorized_keys si dd est admin)" -ForegroundColor DarkYellow
}

function Stage-Services {
    Write-Host "`n== Services (cloudflared + webui via NSSM) ==" -ForegroundColor Yellow

    # cloudflared : suppose que `cloudflared tunnel create dd-app` et le
    # config.yml ont déjà été faits (cf. RUNBOOK §5b, étapes interactives).
    Write-Host "→ cloudflared service install" -ForegroundColor Cyan
    cloudflared service install
    Get-Service cloudflared | Format-Table -AutoSize

    # webui via NSSM, chemins ABSOLUS (un service ne voit pas le PATH user).
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    $ffcmd = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
    if (-not $uv)    { throw "uv introuvable dans le PATH — rouvre un terminal après l'étape deps." }
    if (-not $ffcmd) { throw "ffmpeg introuvable — installe Gyan.FFmpeg en --scope machine." }
    $ffdir = Split-Path $ffcmd

    if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
        throw "nssm introuvable. Installe-le (winget NSSM.NSSM ou https://nssm.cc) puis relance -Stage services."
    }

    Write-Host "→ NSSM: enregistrement du service dd-webui" -ForegroundColor Cyan
    # Recrée proprement si déjà présent.
    if (Get-Service dd-webui -ErrorAction SilentlyContinue) {
        nssm stop dd-webui
        nssm remove dd-webui confirm
    }
    nssm install dd-webui "$uv" "run python serve.py"
    nssm set dd-webui AppDirectory "$RepoPath"
    nssm set dd-webui AppEnvironmentExtra "DD_BEHIND_HTTPS_PROXY=1" "PATH=$ffdir;%PATH%"
    nssm set dd-webui Start SERVICE_AUTO_START
    nssm set dd-webui AppStdout "$RepoPath\data\webui.log"
    nssm set dd-webui AppStderr "$RepoPath\data\webui.log"
    nssm start dd-webui
    Get-Service dd-webui | Format-Table -AutoSize

    Write-Host "Optionnel : faire tourner le service sous le compte dd —" -ForegroundColor DarkYellow
    Write-Host "  nssm edit dd-webui  → onglet Log on → This account → .\dd" -ForegroundColor DarkYellow
}

# --- Orchestration ---
Assert-Admin
switch ($Stage) {
    "deps"     { Stage-Deps }
    "project"  { Stage-Project }
    "ssh"      { Stage-Ssh }
    "services" { Stage-Services }
    "all" {
        Stage-Deps
        Stage-Project
        Stage-Ssh
        Write-Host "`n=== ÉTAPES INTERACTIVES RESTANTES (voir RUNBOOK) ===" -ForegroundColor Green
        Write-Host "1) tailscale up            (§5a — connecte la tour au tailnet)"
        Write-Host "2) cloudflared tunnel login / create dd-app / route dns   (§5b)"
        Write-Host "3) config.yml du tunnel    (deploy\cloudflared-config.example.yml)"
        Write-Host "4) Cloudflare Access        (§5c — dashboard Zero Trust)"
        Write-Host "Puis :  deploy\install.ps1 -Stage services"
        Write-Host "Enfin : redémarrer la tour et valider (§8)."
    }
}
Write-Host "`nTerminé (stage: $Stage)." -ForegroundColor Green

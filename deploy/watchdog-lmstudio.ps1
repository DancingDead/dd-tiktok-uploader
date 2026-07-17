# watchdog-lmstudio.ps1 — verifie que LM Studio headless sert bien, relance sinon.
# Declenche toutes les 3 min par le Planificateur de taches (session 0).
#
# Sain si : le serveur 1234 repond ET le modele attendu est charge en memoire.
# Recuperation IDEMPOTENTE : on decharge TOUT avant de recharger, pour ne jamais
# empiler des instances en double (bug qui saturait la VRAM).

$ErrorActionPreference = "SilentlyContinue"
$lms   = "$env:USERPROFILE\.lmstudio\bin\lms.exe"
$model = "qwen2.5-7b-instruct-1m"

$healthy = $false
try {
    $null = Invoke-RestMethod "http://localhost:1234/v1/models" -TimeoutSec 8   # serveur up ?
    $ps   = (& $lms ps) | Out-String                                            # modele charge ?
    if ($ps -match [regex]::Escape($model)) { $healthy = $true }
} catch {
    $healthy = $false
}

if ($healthy) { exit 0 }

# --- Recuperation propre (une seule instance garantie) ---
& $lms server stop               2>$null   # tue un eventuel serveur zombie
& $lms unload -a                 2>$null   # decharge toute instance (anti-doublon)
& $lms load $model --gpu max -y  2>$null   # exactement une instance, sur GPU
& $lms server start --port 1234  2>$null

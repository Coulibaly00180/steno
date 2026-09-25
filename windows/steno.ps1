<#
.SYNOPSIS
    Sténo sous Windows : installation, démarrage, arrêt, mise à jour (n°19).

.DESCRIPTION
    install  Vérifie Docker Desktop et la carte graphique, prépare .env, construit et démarre
             Sténo, télécharge les modèles, crée les raccourcis du Bureau, puis ouvre
             l'assistant de premier lancement.
    start    Démarre Docker Desktop si besoin, puis Sténo, et ouvre le navigateur.
    stop     Arrête Sténo. Les vidéos, la base et les modèles restent en place.
    update   Récupère la dernière version (git), reconstruit, télécharge les modèles manquants.
    status   État de Docker, de la carte graphique et des services.

    Double-cliquer sur « Installer Steno.cmd » lance « install ». Les raccourcis du Bureau
    lancent « start » et « stop ».

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File windows\steno.ps1 status
#>
param(
    [ValidateSet("install", "start", "stop", "update", "status")]
    [string]$Action = "start",
    # Ignore the NVIDIA card (the CPU image, as on a computer without one).
    [switch]$Cpu,
    [switch]$NoShortcuts,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateFile = Join-Path $Root ".steno-launcher.json"
$EnvFile = Join-Path $Root ".env"
$AppUrl = "http://127.0.0.1:3000"
$ApiUrl = "http://127.0.0.1:8000"
# The Ollama image already needed by Sténo: running nvidia-smi in it proves Docker reaches the card.
$GpuProbeImage = "ollama/ollama:0.34.1"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "    $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "    $Text" }
function Write-Warn([string]$Text) { Write-Host "    $Text" -ForegroundColor Yellow }
function Stop-WithError([string]$Text) {
    Write-Host ""
    Write-Host "    $Text" -ForegroundColor Red
    exit 1
}

# A native command whose output is not wanted. Through cmd.exe: Windows PowerShell 5.1 would
# turn anything written on stderr into an error.
function Test-Quiet([string]$CommandLine) {
    cmd /c "$CommandLine >nul 2>&1"
    return $LASTEXITCODE -eq 0
}

function Invoke-Checked([string]$Description, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { Stop-WithError "$Description a échoué (code $LASTEXITCODE). Détails ci-dessus." }
}

# --- Docker ---------------------------------------------------------------------------------------

function Test-DockerEngine { return Test-Quiet "docker info" }

function Start-DockerDesktop {
    $exe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $exe)) { return $false }
    Start-Process -FilePath $exe | Out-Null
    $deadline = (Get-Date).AddMinutes(4)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerEngine) { return $true }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Confirm-Docker {
    Write-Step "Docker Desktop"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Warn "Docker Desktop n'est pas installé : Sténo tourne dans ses conteneurs."
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            $answer = Read-Host "    L'installer maintenant avec winget ? (O/N)"
            if ($answer -match "^[oOyY]") {
                & winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
                Stop-WithError ("Docker Desktop est installé. Redémarrez l'ordinateur, ouvrez Docker Desktop une première fois " +
                    "pour accepter ses conditions, puis relancez « Installer Steno ».")
            }
        }
        Start-Process "https://www.docker.com/products/docker-desktop/"
        Stop-WithError "Installez Docker Desktop (page ouverte dans le navigateur), puis relancez « Installer Steno »."
    }
    if (-not (Test-DockerEngine)) {
        Write-Note "Docker Desktop n'est pas démarré : démarrage (jusqu'à 4 minutes)..."
        if (-not (Start-DockerDesktop)) {
            Stop-WithError "Docker Desktop ne répond pas. Ouvrez-le, attendez « Engine running », puis relancez."
        }
    }
    Write-Ok "Docker Desktop fonctionne."
}

# --- Graphics card --------------------------------------------------------------------------------

function Get-NvidiaGpu {
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    $path = if ($smi) { $smi.Source } else { Join-Path $env:SystemRoot "System32\nvidia-smi.exe" }
    if (-not (Test-Path $path)) { return $null }
    $line = cmd /c "`"$path`" --query-gpu=name,memory.total --format=csv,noheader,nounits 2>nul" | Select-Object -First 1
    if (-not $line) { return $null }
    $parts = $line.Split(",")
    $memory = [double]::Parse($parts[1].Trim(), [System.Globalization.CultureInfo]::InvariantCulture) / 1024
    return [pscustomobject]@{ Name = $parts[0].Trim(); MemoryGb = $memory; Memory = ("{0:N1} Go" -f $memory) }
}

function Test-DockerGpu { return Test-Quiet "docker run --rm --gpus all --entrypoint nvidia-smi $GpuProbeImage -L" }

# The models the card can hold (qwen3:8b with a 32k context needs about 9 GB of video memory).
function Select-Setup([bool]$ForceCpu) {
    Write-Step "Carte graphique"
    $gpu = if ($ForceCpu) { $null } else { Get-NvidiaGpu }
    if ($null -eq $gpu) {
        if ($ForceCpu) { Write-Note "Carte graphique ignorée à la demande (-Cpu)." }
        else { Write-Note "Pas de carte NVIDIA détectée : Sténo tournera sur le processeur (plus lent)." }
        return [pscustomobject]@{ Gpu = $false; GpuName = $null; MemoryGb = 0; Llm = "qwen3:4b" }
    }
    Write-Ok "$($gpu.Name), $($gpu.Memory) de mémoire vidéo."
    Write-Note "Vérification de l'accès de Docker à la carte (premier lancement : téléchargement d'une image)..."
    if (-not (Test-DockerGpu)) {
        Write-Warn ("Docker n'accède pas à la carte : mettez à jour le pilote NVIDIA et Docker Desktop (moteur WSL 2). " +
            "En attendant, Sténo tournera sur le processeur.")
        return [pscustomobject]@{ Gpu = $false; GpuName = $gpu.Name; MemoryGb = 0; Llm = "qwen3:4b" }
    }
    Write-Ok "Docker utilise la carte graphique."
    $llm = if ($gpu.MemoryGb -ge 10) { "qwen3:8b" } else { "qwen3:4b" }
    return [pscustomobject]@{ Gpu = $true; GpuName = $gpu.Name; MemoryGb = $gpu.MemoryGb; Llm = $llm }
}

# --- Configuration --------------------------------------------------------------------------------

function Get-EnvValue([string]$Name) {
    if (-not (Test-Path $EnvFile)) { return $null }
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        if ($line -match "^\s*$([regex]::Escape($Name))=(.*)$") { return $Matches[1].Trim() }
    }
    return $null
}

# UTF-8 without BOM: Docker Compose would read a BOM as part of the first variable's name.
function Set-EnvValue([string]$Name, [string]$Value) {
    $lines = [System.Collections.Generic.List[string]]::new()
    if (Test-Path $EnvFile) { $lines.AddRange([string[]][System.IO.File]::ReadAllLines($EnvFile)) }
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$([regex]::Escape($Name))=") { $lines[$i] = "$Name=$Value"; $found = $true }
    }
    if (-not $found) { $lines.Add("$Name=$Value") }
    [System.IO.File]::WriteAllText($EnvFile, (($lines -join "`n") + "`n"), $Utf8NoBom)
}

function Get-LanAddress {
    try {
        $config = Get-NetIPConfiguration -ErrorAction Stop |
            Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } | Select-Object -First 1
        if ($config) { return ($config.IPv4Address | Select-Object -First 1).IPAddress }
    } catch { }
    return $null
}

function Initialize-Configuration($Setup) {
    Write-Step "Configuration"
    if (-not (Test-Path $EnvFile)) {
        [System.IO.File]::WriteAllText($EnvFile, [System.IO.File]::ReadAllText((Join-Path $Root ".env.example")), $Utf8NoBom)
        Set-EnvValue "LLM_MODEL" $Setup.Llm
        Write-Ok ".env créé (modèle de langage : $($Setup.Llm))."
    } else {
        Write-Note ".env existe déjà : vos réglages sont conservés."
    }
    $address = Get-LanAddress
    $current = Get-EnvValue "LAN_ADDRESS"
    if ($address -and (-not $current -or $current -eq "steno.local")) {
        Set-EnvValue "LAN_ADDRESS" $address
        Write-Note "Adresse sur le réseau local : $address (pour l'accès depuis un autre appareil, désactivé par défaut)."
    }
    $state = @{ gpu = $Setup.Gpu; gpu_name = $Setup.GpuName; llm = $Setup.Llm; installed_at = (Get-Date).ToString("s") }
    [System.IO.File]::WriteAllText($StateFile, ($state | ConvertTo-Json), $Utf8NoBom)
}

function Get-State {
    if (-not (Test-Path $StateFile)) { return $null }
    return [System.IO.File]::ReadAllText($StateFile) | ConvertFrom-Json
}

function Get-ComposeFiles {
    $state = Get-State
    if ($state -and $state.gpu) { return @("-f", "compose.yaml", "-f", "compose.gpu.yaml") }
    return @("-f", "compose.yaml")
}

# --- Actions --------------------------------------------------------------------------------------

function Wait-Application([int]$Minutes) {
    $deadline = (Get-Date).AddMinutes($Minutes)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $AppUrl -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Start-Application([bool]$Build) {
    $files = Get-ComposeFiles
    if ($Build) {
        Write-Step "Construction et démarrage de Sténo (première fois : 10 à 20 minutes)"
        Invoke-Checked "La construction" { docker compose @files up -d --build }
    } else {
        Write-Step "Démarrage de Sténo"
        Invoke-Checked "Le démarrage" { docker compose @files up -d }
    }
    Write-Note "Attente de l'application..."
    if (-not (Wait-Application 10)) {
        Stop-WithError "Sténo ne répond pas sur $AppUrl. Consultez : docker compose logs api web"
    }
    Write-Ok "Sténo répond sur $AppUrl."
}

function Get-Models {
    Write-Step "Modèles d'IA (première fois : plusieurs Go à télécharger)"
    $files = Get-ComposeFiles
    Invoke-Checked "Le téléchargement des modèles" { docker compose @files --profile tools run --rm model-pull }
    Write-Ok "Modèles prêts. Le modèle de transcription se télécharge lors de la première analyse."
}

function New-Shortcuts {
    Write-Step "Raccourcis du Bureau"
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shell = New-Object -ComObject WScript.Shell
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $items = @(
        @{ Name = "Sténo"; Action = "start"; Description = "Démarrer Sténo et l'ouvrir dans le navigateur" },
        @{ Name = "Arrêter Sténo"; Action = "stop"; Description = "Arrêter Sténo (les données restent)" }
    )
    foreach ($item in $items) {
        $link = $shell.CreateShortcut((Join-Path $desktop "$($item.Name).lnk"))
        $link.TargetPath = $powershell
        $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" $($item.Action)"
        $link.WorkingDirectory = $Root
        $link.Description = $item.Description
        $link.Save()
    }
    Write-Ok "« Sténo » et « Arrêter Sténo » sont sur le Bureau."
}

function Open-Browser([string]$Path) {
    if (-not $NoBrowser) { Start-Process "$AppUrl$Path" }
}

function Show-Status {
    Write-Step "Docker Desktop"
    if (Test-DockerEngine) { Write-Ok "Démarré." } else { Write-Warn "Arrêté ou absent." }
    Write-Step "Carte graphique"
    $gpu = Get-NvidiaGpu
    if ($gpu) { Write-Ok "$($gpu.Name), $($gpu.Memory)." } else { Write-Note "Aucune carte NVIDIA détectée." }
    $state = Get-State
    if ($state) {
        $mode = if ($state.gpu) { "carte graphique" } else { "processeur" }
        Write-Note "Sténo est configuré pour le $mode (modèle de langage installé : $($state.llm))."
    } else {
        Write-Warn "Sténo n'a pas été installé par ce lanceur (lancez « Installer Steno.cmd »)."
    }
    if (Test-DockerEngine) {
        Write-Step "Services"
        $files = Get-ComposeFiles
        docker compose @files ps --format "table {{.Service}}\t{{.State}}\t{{.Status}}"
        try {
            # Decoded as UTF-8 by hand: Windows PowerShell 5.1 reads JSON without a charset as Latin-1.
            $response = Invoke-WebRequest -Uri "$ApiUrl/status" -UseBasicParsing -TimeoutSec 10
            $status = [System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
            Write-Step "État de Sténo"
            foreach ($property in $status.services.PSObject.Properties) {
                $service = $property.Value
                $line = "{0,-10} {1} {2}" -f $property.Name, $service.status, $service.detail
                if ($service.status -eq "ok") { Write-Ok $line } else { Write-Warn $line }
            }
        } catch {
            Write-Warn "L'API ne répond pas sur $ApiUrl."
        }
    }
}

switch ($Action) {
    "install" {
        Write-Host "Installation de Sténo dans $Root" -ForegroundColor Cyan
        Confirm-Docker
        $setup = Select-Setup $Cpu.IsPresent
        Initialize-Configuration $setup
        Start-Application $true
        Get-Models
        if (-not $NoShortcuts) { New-Shortcuts }
        Write-Step "Terminé"
        Write-Ok "Sténo est installé. L'assistant de premier lancement s'ouvre dans le navigateur."
        Open-Browser "/bienvenue"
    }
    "start" {
        Confirm-Docker
        if (-not (Get-State)) { Write-Warn "Sténo n'a pas été installé par ce lanceur : configuration processeur utilisée." }
        Start-Application $false
        Open-Browser "/"
    }
    "stop" {
        Write-Step "Arrêt de Sténo"
        if (-not (Test-DockerEngine)) { Write-Ok "Docker Desktop est déjà arrêté."; break }
        $files = Get-ComposeFiles
        # « stop » and not « down »: the containers and their data stay as they are.
        Invoke-Checked "L'arrêt" { docker compose @files stop }
        Write-Ok "Sténo est arrêté. Vos vidéos et votre bibliothèque sont conservées."
    }
    "update" {
        Confirm-Docker
        if ((Test-Path (Join-Path $Root ".git")) -and (Get-Command git -ErrorAction SilentlyContinue)) {
            Write-Step "Récupération de la dernière version"
            Invoke-Checked "La mise à jour (git pull)" { git pull --ff-only }
        } else {
            Write-Note "Pas de dépôt git : remplacez le dossier par la nouvelle version, puis relancez « update »."
        }
        Start-Application $true
        Get-Models
        Open-Browser "/"
    }
    "status" { Show-Status }
}

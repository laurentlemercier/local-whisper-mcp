<#
.SYNOPSIS
    Test complet du Local Whisper Service V2.3 via REST et MCP Streamable HTTP.

.DESCRIPTION
    Teste :
      1. GET /health
      2. GET /v1/models avec authentification Bearer
      3. POST /v1/transcriptions/upload avec un fichier audio
      4. Polling REST jusqu'à completed/failed/timeout
      5. GET /result
      6. Préparation automatique d'un venv Python pour le client MCP
      7. Installation automatique du SDK MCP officiel
      8. Découverte et appels des outils MCP avec Streamable HTTP
      9. Nettoyage des jobs REST créés par le script

    IMPORTANT :
      - aucun Python 3.11 n'est imposé ; le venv est créé avec le Python
        actuellement disponible dans PATH, ou celui fourni par -PythonExe.
      - tous les appels au Python du venv utilisent explicitement l'opérateur
        d'appel PowerShell '&' afin de gérer correctement les chemins complets.
      - le fichier mcp_client.py doit être dans le même répertoire que ce script.

.PARAMETER BaseUrl
    URL de base du service, sans / final.

.PARAMETER ApiToken
    Token Bearer du service. Par défaut : $env:API_TOKEN.

.PARAMETER AudioFile
    Fichier audio local utilisé par le test REST et le test MCP data.
    Aucune valeur par défaut : le chemin dépend de chaque machine. Les tests
    REST et MCP sont ignorés avec un avertissement si le fichier est absent.

.PARAMETER AudioUrl
    Si fourni, le client MCP teste transcribe_url au lieu de transcribe_data.
    Le test REST reste basé sur AudioFile.

.PARAMETER Model
    small ou medium.

.PARAMETER Language
    auto ou code de langue, par exemple fr.

.PARAMETER PollSeconds
    Intervalle entre deux interrogations d'un job.

.PARAMETER TimeoutSeconds
    Timeout global d'un job de transcription.

.PARAMETER McpHttpTimeoutSeconds
    Timeout HTTP du client MCP.

.PARAMETER PythonExe
    Python utilisé pour créer le venv. Par défaut : python.exe trouvé dans PATH.

.PARAMETER SkipMcp
    Ne lance pas le test MCP.

.PARAMETER SkipRestUpload
    Ne lance pas la transcription REST.

.PARAMETER KeepJobs
    Ne supprime pas les jobs REST créés par le script.

.EXAMPLE
    .\test-speech-service-v2.3.ps1 -ApiToken "toto"

.EXAMPLE
    .\test-speech-service-v2.3.ps1 `
        -BaseUrl "http://192.168.1.50:8000" `
        -ApiToken "toto" `
        -AudioFile "C:\chemins\vers\mon\audio.m4a"

.EXAMPLE
    .\test-speech-service-v2.3.ps1 -ApiToken "toto" -SkipRestUpload

.EXAMPLE
    .\test-speech-service-v2.3.ps1 -ApiToken "toto" -AudioUrl "https://example.com/audio/test.m4a"
#>

[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:8000",
    [string]$ApiToken = $env:API_TOKEN,
    [string]$AudioFile = "",
    [ValidateSet("small", "medium")]
    [string]$Model = "small",
    [string]$Language = "auto",
    [string]$AudioUrl = "",
    [double]$PollSeconds = 2,
    [int]$TimeoutSeconds = 1800,
    [string]$PythonExe = "",
    [switch]$SkipMcp,
    [switch]$SkipRestUpload,
    [switch]$KeepJobs
)

$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd("/")

# Tolère un éventuel copier/coller accidentel d'un lien Markdown :
# [[http://localhost:8000](http://localhost:8000)] -> http://localhost:8000
if ($BaseUrl -match '^\[\[([^\]]+)\]\(([^)]+)\)\]$') {
    $BaseUrl = $Matches[1].TrimEnd("/")
}

$script:CreatedJobs = [System.Collections.Generic.List[string]]::new()
$script:Failures = 0
$script:Warnings = 0

$script:VenvDir = Join-Path -Path $PSScriptRoot -ChildPath ".venv"
$script:VenvPython = Join-Path -Path $script:VenvDir -ChildPath "Scripts\python.exe"
$script:McpClient = Join-Path -Path $PSScriptRoot -ChildPath "mcp_client.py"

function Write-Section {
    param([Parameter(Mandatory)][string]$Title)
    Write-Host ""
    Write-Host ("=" * 78) -ForegroundColor DarkGray
    Write-Host $Title -ForegroundColor Cyan
    Write-Host ("=" * 78) -ForegroundColor DarkGray
}

function Write-Ok {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[OK]   $Message" -ForegroundColor Green
}

function Write-Info {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Gray
}

function Write-Warn {
    param([Parameter(Mandatory)][string]$Message)
    $script:Warnings++
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([Parameter(Mandatory)][string]$Message)
    $script:Failures++
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Assert-Token {
    if ([string]::IsNullOrWhiteSpace($ApiToken)) {
        throw 'API token absent. Utilisez -ApiToken ou définissez $env:API_TOKEN.'
    }
}

function Invoke-Api {
    param(
        [Parameter(Mandatory)][ValidateSet("GET", "POST", "DELETE")][string]$Method,
        [Parameter(Mandatory)][string]$Uri,
        [object]$Body = $null,
        [string]$ContentType = "application/json"
    )

    Assert-Token

    $headers = @{
        Authorization = "Bearer $ApiToken"
        Accept        = "application/json"
    }

    if ($null -ne $Body) {
        return Invoke-RestMethod `
            -Method $Method `
            -Uri $Uri `
            -Headers $headers `
            -ContentType $ContentType `
            -Body $Body
    }

    return Invoke-RestMethod `
        -Method $Method `
        -Uri $Uri `
        -Headers $headers
}

function Test-Health {
    Write-Section "1. REST - HEALTH"

    $health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health"

    if ($health.status -ne "ok") {
        throw "Health inattendu : $($health | ConvertTo-Json -Depth 10 -Compress)"
    }

    Write-Ok "Service accessible : $($health.service) / version $($health.version)"
}

function Test-Models {
    Write-Section "2. REST - MODELS / AUTHENTICATION"

    $models = Invoke-Api -Method GET -Uri "$BaseUrl/v1/models"

    Write-Ok "Authentification Bearer acceptée."
    if ($null -ne $models.available) {
        Write-Info ("Modèles disponibles : " + ($models.available -join ", "))
    }
}

function Test-RestUpload {
    Write-Section "3. REST - UPLOAD MULTIPART"

    if (-not (Test-Path -LiteralPath $AudioFile -PathType Leaf)) {
        Write-Warn "Fichier audio introuvable : $AudioFile"
        Write-Warn "Le test REST upload est ignoré."
        return $null
    }

    $file = Get-Item -LiteralPath $AudioFile
    Write-Info "Fichier : $($file.FullName)"
    Write-Info "Taille  : $([math]::Round($file.Length / 1MB, 2)) MB"
    Write-Info "Modèle  : $Model"
    Write-Info "Langue  : $Language"

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($null -eq $curl) {
        throw "curl.exe est introuvable dans PATH. Le test REST upload utilise curl.exe pour le multipart."
    }

    $curlArgs = @(
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--request", "POST",
        "--header", "Authorization: Bearer $ApiToken",
        "--header", "Accept: application/json",
        "--form", "file=@$($file.FullName)",
        "$BaseUrl/v1/transcriptions/upload?model=$Model&language=$Language"
    )

    $raw = & $curl.Source @curlArgs 2>&1
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        throw "curl.exe a échoué (code $exitCode) : $($raw -join "`n")"
    }

    try {
        $job = ($raw -join "`n") | ConvertFrom-Json
    }
    catch {
        throw "Réponse REST non JSON : $($raw -join "`n")"
    }

    if ([string]::IsNullOrWhiteSpace([string]$job.id)) {
        throw "La réponse REST ne contient pas d'id : $($job | ConvertTo-Json -Depth 10)"
    }

    [void]$script:CreatedJobs.Add([string]$job.id)
    Write-Ok "Job REST créé : $($job.id), status=$($job.status)"

    return $job
}

function Wait-RestJob {
    param([Parameter(Mandatory)][string]$JobId)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastStatus = ""

    while ((Get-Date) -lt $deadline) {
        $job = Invoke-Api -Method GET -Uri "$BaseUrl/v1/transcriptions/$JobId"

        $progress = if ($null -ne $job.progress) {
            try { "{0:P0}" -f [double]$job.progress } catch { [string]$job.progress }
        }
        else {
            "-"
        }

        $line = "  status={0,-12} progress={1}" -f $job.status, $progress
        if ($line -ne $lastStatus) {
            Write-Host $line
            $lastStatus = $line
        }

        if ($job.status -eq "completed") {
            return $job
        }

        if ($job.status -eq "failed") {
            $errorText = if ($null -ne $job.error) { [string]$job.error } else { "erreur non précisée" }
            throw "Job $JobId failed: $errorText"
        }

        Start-Sleep -Seconds $PollSeconds
    }

    throw "Timeout après $TimeoutSeconds s pour le job $JobId."
}

function Show-Result {
    param([Parameter(Mandatory)]$Job)

    Write-Host ""
    Write-Host "Résultat :" -ForegroundColor White
    Write-Host "  ID                        : $($Job.id)"
    Write-Host "  Status                    : $($Job.status)"
    Write-Host "  Modèle                    : $($Job.model)"
    Write-Host "  Langue                    : $($Job.result.language)"
    Write-Host "  Durée audio               : $($Job.audio_duration_seconds) s"
    Write-Host "  Durée transcription       : $($Job.transcription_duration_seconds) s"
    Write-Host "  Real-time factor          : $($Job.real_time_factor)"
    Write-Host "  Transcribed at            : $($Job.transcribed_at)"
    Write-Host ""
    Write-Host "  Texte :" -ForegroundColor White
    Write-Host "  $($Job.result.text)"
}

function Test-RestJob {
    param([Parameter(Mandatory)][string]$JobId)

    Write-Section "4. REST - POLLING + RESULTAT"

    $job = Wait-RestJob -JobId $JobId
    Write-Ok "Job REST terminé."
    Show-Result -Job $job

    $result = Invoke-Api -Method GET -Uri "$BaseUrl/v1/transcriptions/$JobId/result"

    $resultText = $null
    if ($null -ne $result.text) {
        $resultText = [string]$result.text
    }
    elseif ($null -ne $result.result -and $null -ne $result.result.text) {
        $resultText = [string]$result.result.text
    }

    if (-not [string]::IsNullOrWhiteSpace($resultText)) {
        Write-Ok "GET /result fonctionne."
    }
    else {
        Write-Warn "GET /result a répondu sans texte exploitable."
    }

    return $job
}

function Resolve-Python {
    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
            throw "Python spécifié avec -PythonExe introuvable : $PythonExe"
        }
        return (Resolve-Path -LiteralPath $PythonExe).Path
    }

    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
    }

    if ($null -eq $cmd) {
        throw "python.exe introuvable dans PATH. Installez Python puis relancez le test."
    }

    return $cmd.Source
}

function Invoke-Python {
    param(
        [Parameter(Mandatory)][string]$Python,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python introuvable avant exécution : [$Python]"
    }

    Write-Info "Exécution Python : [$Python]"

    # IMPORTANT : ne pas laisser stdout/stderr du processus devenir la valeur
    # de retour de cette fonction. Avec PowerShell, une commande native qui
    # écrit sur stdout produit des objets dans le pipeline ; dans un appel du
    # type `$code = Invoke-Python ...`, cela transforme `$code` en tableau de
    # chaînes + code retour et provoque un faux échec.
    #
    # On capture donc temporairement stdout/stderr, puis on ne retourne qu'un
    # entier. Les diagnostics ne sont affichés qu'en cas d'échec.
    $logFile = Join-Path $env:TEMP (
        "local-whisper-python-{0}-{1}.log" -f ([guid]::NewGuid().ToString("N")), (Get-Date -Format "yyyyMMddHHmmss")
    )

    try {
        & $Python @Arguments *> $logFile
        $exitCode = [int]$LASTEXITCODE

        if ($exitCode -ne 0 -and (Test-Path -LiteralPath $logFile)) {
            $diagnostic = Get-Content -LiteralPath $logFile -Raw -ErrorAction SilentlyContinue
            if (-not [string]::IsNullOrWhiteSpace($diagnostic)) {
                Write-Host $diagnostic.TrimEnd() -ForegroundColor DarkYellow
            }
        }

        return $exitCode
    }
    finally {
        Remove-Item -LiteralPath $logFile -Force -ErrorAction SilentlyContinue
    }
}

function Get-PythonVersion {
    param([Parameter(Mandatory)][string]$Python)

    $output = & $Python --version 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "Impossible d'exécuter Python : [$Python]"
    }

    return ($output -join " ").Trim()
}

function Ensure-McpVenv {
    Write-Section "5. MCP - PREPARATION DU VENV"

    if (-not (Test-Path -LiteralPath $script:McpClient -PathType Leaf)) {
        throw "Client MCP introuvable : $script:McpClient"
    }

    $bootstrapPython = Resolve-Python
    Write-Info "Python bootstrap : [$bootstrapPython]"
    Write-Info "Version          : $(Get-PythonVersion -Python $bootstrapPython)"
    Write-Info "Venv             : [$script:VenvDir]"
    Write-Info "Python venv      : [$script:VenvPython]"

    if (-not (Test-Path -LiteralPath $script:VenvPython -PathType Leaf)) {
        Write-Info "Création du venv..."
        $code = Invoke-Python -Python $bootstrapPython -Arguments @("-m", "venv", $script:VenvDir)
        if ($code -ne 0) {
            throw "Échec de création du venv (code $code) : [$script:VenvDir]"
        }

        if (-not (Test-Path -LiteralPath $script:VenvPython -PathType Leaf)) {
            throw "Le venv semble avoir été créé, mais python.exe est introuvable : [$script:VenvPython]"
        }
        Write-Ok "Venv créé."
    }
    else {
        Write-Ok "Venv déjà présent : réutilisation."
    }

    # Vérification explicite AVANT toute exécution.
    if (-not (Test-Path -LiteralPath $script:VenvPython -PathType Leaf)) {
        throw "Python du venv introuvable : [$script:VenvPython]"
    }

    $venvVersion = Get-PythonVersion -Python $script:VenvPython
    Write-Info "Python du venv   : $venvVersion"

    Write-Info "Mise à jour de pip..."
    $code = Invoke-Python -Python $script:VenvPython -Arguments @(
        "-m", "pip", "install",
        "--disable-pip-version-check",
        "--quiet",
        "--upgrade",
        "pip"
    )
    if ($code -ne 0) {
        throw "Échec de mise à jour de pip dans le venv (code $code)."
    }

    Write-Info "Installation/vérification du SDK MCP (mcp>=1.13,<2)..."
    $code = Invoke-Python -Python $script:VenvPython -Arguments @(
        "-m", "pip", "install",
        "--disable-pip-version-check",
        "--upgrade",
        "mcp>=1.13,<2"
    )
    if ($code -ne 0) {
        throw "Échec d'installation du SDK MCP (code $code)."
    }

    $mcpCheck = & $script:VenvPython -c "from importlib.metadata import version; import mcp; print(version('mcp')); print(mcp.__file__)" 2>&1
    $mcpCheckCode = $LASTEXITCODE

    if ($mcpCheckCode -ne 0) {
        throw "Le package mcp est installé mais son import échoue : $($mcpCheck -join "`n")"
    }

    Write-Ok "SDK MCP prêt."
    foreach ($line in $mcpCheck) {
        Write-Host "  $line"
    }

    return $script:VenvPython
}

function Test-Mcp {
    Write-Section "6. MCP - STREAMABLE HTTP"

    if ($SkipMcp) {
        Write-Warn "Test MCP désactivé avec -SkipMcp."
        return
    }

    if ([string]::IsNullOrWhiteSpace($AudioFile)) {
        Write-Warn "Le test MCP utilise transcribe_data et exige -AudioFile."
        Write-Warn "Le test MCP est ignoré."
        return
    }

    $python = Ensure-McpVenv

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python MCP introuvable juste avant lancement : [$python]"
    }

    if (-not (Test-Path -LiteralPath $script:McpClient -PathType Leaf)) {
        throw "Client MCP introuvable juste avant lancement : [$script:McpClient]"
    }

    # Construction explicite du tableau d'arguments PowerShell.
    # Aucun appel de type 'string command arguments' n'est utilisé.
    $arguments = @(
        $script:McpClient,
        "--endpoint", "$BaseUrl/mcp",
        "--api-token", $ApiToken,
        "--audio-file", $AudioFile,
        "--model", $Model,
        "--language", $Language,
        "--poll-seconds", ([string]$PollSeconds),
        "--timeout", ([string]$TimeoutSeconds)
    )

    if (-not [string]::IsNullOrWhiteSpace($AudioUrl)) {
        Write-Warn "-AudioUrl est ignoré par ce client MCP V2.3 : le test MCP utilise transcribe_data avec -AudioFile."
    }

    Write-Info "Client MCP : [$script:McpClient]"
    Write-Info "Python     : [$python]"
    Write-Info "Base URL   : [$BaseUrl]"
    Write-Info "MCP endpoint : [$BaseUrl/mcp]"

    # IMPORTANT : le '&' force PowerShell à exécuter le chemin comme programme.
    # On ne construit jamais une chaîne du type 'python.exe ...'.
    & $python @arguments
    $code = $LASTEXITCODE

    switch ($code) {
        0  { Write-Ok "MCP Streamable HTTP : test réussi." }
        21 { Write-Fail "Un ou plusieurs outils MCP attendus sont absents." }
        23 { Write-Fail "Aucun fichier audio utilisable pour le test MCP." }
        24 { Write-Fail "Soumission MCP impossible." }
        25 { Write-Fail "Le job MCP a échoué." }
        26 { Write-Fail "Timeout MCP." }
        27 { Write-Fail "Le résultat MCP est absent." }
        29 { Write-Fail "Erreur d'exécution du client MCP." }
        130 { Write-Warn "Client MCP interrompu par l'utilisateur." }
        default { Write-Fail "Client MCP terminé avec le code $code." }
    }
}

function Cleanup-Jobs {
    if ($KeepJobs -or $script:CreatedJobs.Count -eq 0) {
        return
    }

    Write-Section "7. NETTOYAGE REST"

    foreach ($jobId in $script:CreatedJobs) {
        try {
            $r = Invoke-Api -Method DELETE -Uri "$BaseUrl/v1/transcriptions/$jobId"
            if ($r.deleted) {
                Write-Ok "Job supprimé : $jobId"
            }
            else {
                Write-Warn "Job non supprimé : $jobId"
            }
        }
        catch {
            Write-Warn "Impossible de supprimer $jobId : $($_.Exception.Message)"
        }
    }
}

try {
    Write-Host ""
    Write-Host "Local Whisper Service V2.3 - test REST + MCP" -ForegroundColor Magenta
    Write-Host "Base URL : $BaseUrl"
    Write-Host "Modèle   : $Model"
    Write-Host "Langue   : $Language"
    Write-Host "MCP venv : $script:VenvDir"

    Test-Health
    Test-Models

    if (-not $SkipRestUpload) {
        $restJob = Test-RestUpload
        if ($null -ne $restJob) {
            Test-RestJob -JobId ([string]$restJob.id) | Out-Null
        }
    }
    else {
        Write-Section "3-4. REST - UPLOAD/POLLING"
        Write-Warn "Test REST upload désactivé avec -SkipRestUpload."
    }

    Test-Mcp
}
catch {
    Write-Fail $_.Exception.Message
}
finally {
    Cleanup-Jobs
}

Write-Section "SYNTHESE"

if ($script:Failures -eq 0) {
    if ($script:Warnings -gt 0) {
        Write-Ok "Tests terminés sans échec bloquant ($script:Warnings avertissement(s))."
    }
    else {
        Write-Ok "Tous les tests exécutés sont OK."
    }
    exit 0
}
else {
    Write-Host "[FAIL] $script:Failures test(s) en échec." -ForegroundColor Red
    exit 1
}

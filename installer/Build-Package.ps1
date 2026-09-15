<#
.SYNOPSIS
    Собирает автономный установочный пакет ProjectBureau-install.zip —
    запускать на машине разработчика (нужны .NET SDK и интернет). Результат
    раздаётся на рабочие компьютеры: там достаточно распаковать zip и
    запустить Install-Target.ps1 — ни git, ни .NET SDK там не нужны.

.EXAMPLE
    .\Build-Package.ps1
    .\Build-Package.ps1 -OutputDir "\\server\share\ProjectBureau"
#>
param(
    [string]$OutputDir = (Join-Path $env:USERPROFILE "Desktop"),
    [string]$PythonVersion = "3.11.9"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LoaderDir = Join-Path $RepoRoot "loader"
$ExtensionRoot = Join-Path $RepoRoot "ProjectBureau.extension"
$RuntimeDir = Join-Path $ExtensionRoot "runtime\python"

# --- 1. Embeddable Python (если ещё не скачан) -----------------------------
if (-not (Get-ChildItem -Path $RuntimeDir -Filter "python3*.dll" -ErrorAction SilentlyContinue)) {
    Write-Host "Скачиваю embeddable Python $PythonVersion..."
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    $zipUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    $zipPath = Join-Path $env:TEMP "pb_python_embed.zip"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $RuntimeDir -Force
    Remove-Item $zipPath
}

# --- 2. Сборка загрузчика ---------------------------------------------------
Write-Host "Собираю ProjectBureau.Loader.dll (Release)..."
Push-Location $LoaderDir
try {
    & dotnet build ProjectBureau.Loader.csproj -c Release
    if ($LASTEXITCODE -ne 0) { throw "dotnet build завершился с ошибкой" }
} finally {
    Pop-Location
}

# --- 3. Сборка пакета --------------------------------------------------------
$StageDir = Join-Path $env:TEMP ("ProjectBureau_package_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

Write-Host "Собираю пакет..."
robocopy (Join-Path $LoaderDir "bin\Release") (Join-Path $StageDir "loader") /MIR /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy: ошибка копирования loader (код $LASTEXITCODE)" }
robocopy $ExtensionRoot (Join-Path $StageDir "ProjectBureau.extension") /MIR /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy: ошибка копирования ProjectBureau.extension (код $LASTEXITCODE)" }
$global:LASTEXITCODE = 0
Copy-Item (Join-Path $PSScriptRoot "Install-Target.ps1") -Destination $StageDir
Copy-Item (Join-Path $PSScriptRoot "Install-Target.bat") -Destination $StageDir

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$ZipPath = Join-Path $OutputDir "ProjectBureau-install.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath }
Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $ZipPath

Remove-Item $StageDir -Recurse -Force

Write-Host ""
Write-Host "Готово: $ZipPath"

# --- 4. Setup.exe через Inno Setup (если он установлен) ---------------------
$Iscc = Get-ChildItem -Path @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) -ErrorAction SilentlyContinue | Select-Object -First 1

if ($Iscc) {
    Write-Host "Собираю ProjectBureauSetup.exe..."
    & $Iscc.FullName (Join-Path $PSScriptRoot "ProjectBureau.iss") "/O$OutputDir" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ISCC завершился с ошибкой" }
    Write-Host "Готово: $(Join-Path $OutputDir 'ProjectBureauSetup.exe')"
    Write-Host "На каждой из рабочих машин: просто запустить ProjectBureauSetup.exe (git и .NET SDK не нужны)."
} else {
    Write-Host "Inno Setup не найден — .zip + Install-Target.ps1 всё равно работают."
    Write-Host "Чтобы получить один ProjectBureauSetup.exe: winget install JRSoftware.InnoSetup, затем запустить этот скрипт ещё раз."
}

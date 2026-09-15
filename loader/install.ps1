<#
.SYNOPSIS
    Разовая установка панели "Бюро" в Revit без pyRevit.

    1) Скачивает embeddable CPython (если ещё не скачан) в
       ProjectBureau.extension\runtime\python.
    2) Собирает ProjectBureau.Loader.dll (dotnet build, Release).
    3) Кладёт ProjectBureau.addin в %AppData%\Autodesk\Revit\Addins\<версия>.

    Дальше обновление кнопок/скриптов — обычный git pull в этом репозитории
    и перезапуск Revit, этот скрипт запускать не нужно. Повторный запуск
    нужен только если поменялся сам загрузчик (папка loader\) или версия
    Revit.
#>
param(
    [string]$RevitVersion = "2024",
    [string]$PythonVersion = "3.11.9"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ExtensionRoot = Join-Path $RepoRoot "ProjectBureau.extension"
$RuntimeDir = Join-Path $ExtensionRoot "runtime\python"

# --- 1. Embeddable Python -------------------------------------------------
if (-not (Test-Path (Join-Path $RuntimeDir "python3*.dll"))) {
    Write-Host "Скачиваю embeddable Python $PythonVersion..."
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    $zipUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    $zipPath = Join-Path $env:TEMP "pb_python_embed.zip"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $RuntimeDir -Force
    Remove-Item $zipPath

    # В embeddable-сборке по умолчанию отключён import site / пользовательский
    # sys.path — pythonnet сам управляет sys.path, поэтому это не мешает, но
    # оставляем ._pth как есть (по умолчанию Python 3.11 ищет stdlib в
    # python311.zip/Lib — это уже настроено дистрибутивом).
    Write-Host "Python распакован в $RuntimeDir"
} else {
    Write-Host "Embeddable Python уже на месте: $RuntimeDir"
}

# --- 2. Сборка загрузчика --------------------------------------------------
Write-Host "Собираю ProjectBureau.Loader.dll..."
Push-Location $PSScriptRoot
try {
    & dotnet build ProjectBureau.Loader.csproj -c Release
    if ($LASTEXITCODE -ne 0) { throw "dotnet build завершился с ошибкой" }
} finally {
    Pop-Location
}

# --- 3. .addin манифест -----------------------------------------------------
$AddinsDir = Join-Path $env:APPDATA "Autodesk\Revit\Addins\$RevitVersion"
New-Item -ItemType Directory -Force -Path $AddinsDir | Out-Null
Copy-Item (Join-Path $PSScriptRoot "ProjectBureau.addin") -Destination $AddinsDir -Force
Write-Host "Манифест установлен: $AddinsDir\ProjectBureau.addin"

Write-Host ""
Write-Host "Готово. Запустите Revit $RevitVersion — должна появиться лента «Бюро»."
Write-Host "Дальше правки кнопок/скриптов — обычный git pull в $RepoRoot, без повторного запуска этого скрипта."

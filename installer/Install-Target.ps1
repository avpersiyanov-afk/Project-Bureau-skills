<#
.SYNOPSIS
    Устанавливает панель "Бюро" на рабочем компьютере — без git, без .NET
    SDK, без интернета (кроме последующего самообновления внутри Revit).

    Запускать из распакованного ProjectBureau-install.zip (этот скрипт
    лежит рядом с папками loader\ и ProjectBureau.extension\).

.EXAMPLE
    .\Install-Target.ps1
    .\Install-Target.ps1 -TargetRoot "D:\ProjectBureau" -RevitVersion 2025
#>
param(
    [string]$TargetRoot = "C:\ProjectBureau",
    [string]$RevitVersion = "2024"
)

$ErrorActionPreference = "Stop"
$PackageRoot = $PSScriptRoot

function Copy-Tree($From, $To) {
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    robocopy $From $To /MIR /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy сообщил об ошибке (код $LASTEXITCODE) при копировании $From -> $To"
    }
}

Write-Host "Устанавливаю в $TargetRoot ..."

Copy-Tree (Join-Path $PackageRoot "loader") (Join-Path $TargetRoot "loader")
Copy-Tree (Join-Path $PackageRoot "ProjectBureau.extension") (Join-Path $TargetRoot "ProjectBureau.extension")

$LoaderDll = Join-Path $TargetRoot "loader\ProjectBureau.Loader.dll"
if (-not (Test-Path $LoaderDll)) {
    throw "Не нашёл $LoaderDll после копирования — пакет собран неправильно?"
}

$AddinXml = @"
<?xml version="1.0" encoding="utf-8" standalone="no"?>
<RevitAddIns>
  <AddIn Type="Application">
    <Name>ProjectBureau</Name>
    <Assembly>$LoaderDll</Assembly>
    <AddInId>8f2b6f2a-2e9f-4d2a-9f3b-6c7a1e0d5b21</AddInId>
    <FullClassName>ProjectBureau.Loader.App</FullClassName>
    <VendorId>PBUREAU</VendorId>
  </AddIn>
</RevitAddIns>
"@

$AddinsDir = Join-Path $env:APPDATA "Autodesk\Revit\Addins\$RevitVersion"
New-Item -ItemType Directory -Force -Path $AddinsDir | Out-Null
$AddinPath = Join-Path $AddinsDir "ProjectBureau.addin"
Set-Content -Path $AddinPath -Value $AddinXml -Encoding UTF8
Write-Host "Манифест установлен: $AddinPath"

Write-Host ""
Write-Host "Готово. Запустите Revit $RevitVersion — появится лента «Бюро»."
Write-Host "Дальше обновления кнопок/скриптов панель скачивает сама с GitHub при каждом запуске Revit — ничего вручную обновлять не нужно."

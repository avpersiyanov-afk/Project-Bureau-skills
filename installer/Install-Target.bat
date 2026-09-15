@echo off
setlocal enabledelayedexpansion

rem Install ProjectBureau panel for Revit without pyRevit, without admin
rem rights, without PowerShell script execution (cmd/robocopy are not
rem governed by the "signed PowerShell scripts only" policy). Run from
rem the unpacked ProjectBureau-install package (this file sits next to
rem loader\ and ProjectBureau.extension\).

set "SRC=%~dp0"
set "TARGET=%LOCALAPPDATA%\ProjectBureau"

echo Installing into %TARGET% ...

robocopy "%SRC%loader" "%TARGET%\loader" /MIR /NFL /NDL /NJH /NJS /NC /NS /NP >nul
robocopy "%SRC%ProjectBureau.extension" "%TARGET%\ProjectBureau.extension" /MIR /NFL /NDL /NJH /NJS /NC /NS /NP >nul

if not exist "%TARGET%\loader\ProjectBureau.Loader.dll" (
    echo ERROR: %TARGET%\loader\ProjectBureau.Loader.dll not found after copy.
    goto :end
)

set "FOUND=0"
for %%Y in (2022 2023 2024 2025 2026) do (
    if exist "C:\Program Files\Autodesk\Revit %%Y\" (
        set "FOUND=1"
        set "ADDINDIR=%APPDATA%\Autodesk\Revit\Addins\%%Y"
        if not exist "!ADDINDIR!" mkdir "!ADDINDIR!"
        (
            echo ^<?xml version="1.0" encoding="utf-8" standalone="no"?^>
            echo ^<RevitAddIns^>
            echo   ^<AddIn Type="Application"^>
            echo     ^<Name^>ProjectBureau^</Name^>
            echo     ^<Assembly^>%TARGET%\loader\ProjectBureau.Loader.dll^</Assembly^>
            echo     ^<AddInId^>8f2b6f2a-2e9f-4d2a-9f3b-6c7a1e0d5b21^</AddInId^>
            echo     ^<FullClassName^>ProjectBureau.Loader.App^</FullClassName^>
            echo     ^<VendorId^>PBUREAU^</VendorId^>
            echo   ^</AddIn^>
            echo ^</RevitAddIns^>
        ) > "!ADDINDIR!\ProjectBureau.addin"
        echo Revit %%Y found, addin installed: !ADDINDIR!\ProjectBureau.addin
    )
)

if "%FOUND%"=="0" (
    echo No Revit installation found under "C:\Program Files\Autodesk\Revit <year>" - addin NOT installed.
)

echo.
echo Done. Start Revit - the "Project Bureau Skills" ribbon tab should appear.
echo Future updates are automatic (downloaded from GitHub on each Revit startup) - no need to run this again unless the loader itself (loader\ folder) changes.

:end
endlocal
pause

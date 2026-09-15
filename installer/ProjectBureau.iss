; Установщик панели "Бюро" для Revit без pyRevit.
; Собирается через Inno Setup (ISCC.exe ProjectBureau.iss) — на выходе
; один файл ProjectBureauSetup.exe, который раздаётся на рабочие машины.
; Payload (loader\bin\Release, ProjectBureau.extension со встроенным
; Python) должен быть уже собран: сначала installer\Build-Package.ps1
; либо вручную loader\install.ps1 / dotnet build.

#define MyAppName "ProjectBureau"
#define MyAppVersion "1.0"
#define RepoRoot ".."

[Setup]
AppId={{8F2B6F2A-2E9F-4D2A-9F3B-6C7A1E0D5B21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName=C:\ProjectBureau
DefaultGroupName=ProjectBureau
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableDirPage=no
OutputDir=..\dist
OutputBaseFilename=ProjectBureauSetup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName} — панель "Бюро" для Revit

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "{#RepoRoot}\loader\bin\Release\*"; DestDir: "{app}\loader"; Flags: recursesubdirs ignoreversion
Source: "{#RepoRoot}\ProjectBureau.extension\*"; DestDir: "{app}\ProjectBureau.extension"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__,__pycache__\*"

[Code]
var
  DetectedYears: TArrayOfString;

function AddinXml(const AppDir: String): String;
begin
  Result :=
    '<?xml version="1.0" encoding="utf-8" standalone="no"?>' + #13#10 +
    '<RevitAddIns>' + #13#10 +
    '  <AddIn Type="Application">' + #13#10 +
    '    <Name>ProjectBureau</Name>' + #13#10 +
    '    <Assembly>' + AppDir + '\loader\ProjectBureau.Loader.dll</Assembly>' + #13#10 +
    '    <AddInId>8f2b6f2a-2e9f-4d2a-9f3b-6c7a1e0d5b21</AddInId>' + #13#10 +
    '    <FullClassName>ProjectBureau.Loader.App</FullClassName>' + #13#10 +
    '    <VendorId>PBUREAU</VendorId>' + #13#10 +
    '  </AddIn>' + #13#10 +
    '</RevitAddIns>';
end;

procedure InstallAddinFor(const Year: String);
var
  AddinDir: String;
begin
  if not DirExists('C:\Program Files\Autodesk\Revit ' + Year) then
    exit;
  AddinDir := ExpandConstant('{userappdata}\Autodesk\Revit\Addins\' + Year);
  ForceDirectories(AddinDir);
  SaveStringToFile(AddinDir + '\ProjectBureau.addin', AddinXml(ExpandConstant('{app}')), False);
  SetArrayLength(DetectedYears, GetArrayLength(DetectedYears) + 1);
  DetectedYears[GetArrayLength(DetectedYears) - 1] := Year;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Years: TArrayOfString;
  I: Integer;
  Msg: String;
begin
  if CurStep <> ssPostInstall then
    exit;

  Years := ['2022', '2023', '2024', '2025', '2026'];
  SetArrayLength(DetectedYears, 0);
  for I := 0 to GetArrayLength(Years) - 1 do
    InstallAddinFor(Years[I]);

  if GetArrayLength(DetectedYears) = 0 then
  begin
    MsgBox('Revit на этом компьютере не найден в стандартной папке ' +
      '(C:\Program Files\Autodesk\Revit <год>). Файлы панели установлены, ' +
      'но .addin-манифест не прописан — панель не появится в Revit. ' +
      'Сообщите об этом (нестандартная установка Revit).',
      mbInformation, MB_OK);
  end
  else
  begin
    Msg := 'Найден Revit: ';
    for I := 0 to GetArrayLength(DetectedYears) - 1 do
    begin
      if I > 0 then
        Msg := Msg + ', ';
      Msg := Msg + DetectedYears[I];
    end;
    MsgBox(Msg + '. Панель «Бюро» появится при следующем запуске Revit.',
      mbInformation, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Years: TArrayOfString;
  I: Integer;
  AddinPath: String;
begin
  if CurUninstallStep <> usUninstall then
    exit;

  Years := ['2022', '2023', '2024', '2025', '2026'];
  for I := 0 to GetArrayLength(Years) - 1 do
  begin
    AddinPath := ExpandConstant('{userappdata}\Autodesk\Revit\Addins\' + Years[I] + '\ProjectBureau.addin');
    if FileExists(AddinPath) then
      DeleteFile(AddinPath);
  end;
end;

#define AppName "Мой Садовод"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppPublisher "Namba1337"
#define AppExeName "MoySadovod.exe"
#define SourceExe "dist\MoySadovod.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=installer
OutputBaseFilename=MoySadovod_Setup_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные задачи:"; Flags: unchecked

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "{#AppExeName}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Небольшая задержка (~2 сек) перед запуском — сразу после распаковки
; свежий .exe иногда ещё "занят" антивирусом/Defender (LoadLibrary
; падает с "Failed to load Python DLL" при автозапуске впритык к записи
; файла); при ручном запуске чуть позже всё работает нормально.
Filename: "{cmd}"; Parameters: "/c ping -n 3 127.0.0.1 >nul & start """" ""{app}\{#AppExeName}"""; Description: "Запустить {#AppName}"; Flags: nowait postinstall runhidden

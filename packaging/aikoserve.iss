; Installateur Windows d'AikoServe (Inno Setup 6)
#define AppVersion GetEnv("AIKOSERVE_VERSION")
#if AppVersion == ""
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{97BB4699-21F0-4A24-BEFA-29BB82070F50}
AppName=AikoServe
AppVersion={#AppVersion}
AppPublisher=Aikographic
DefaultDirName={localappdata}\Programs\AikoServe
DefaultGroupName=AikoServe
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=AikoServe-Windows-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\AikoServe.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer une icône sur le Bureau"; GroupDescription: "Options :"

[Files]
Source: "..\app\dist\AikoServe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AikoServe"; Filename: "{app}\AikoServe.exe"
Name: "{group}\Désinstaller AikoServe"; Filename: "{uninstallexe}"
Name: "{userdesktop}\AikoServe"; Filename: "{app}\AikoServe.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AikoServe.exe"; Description: "Lancer AikoServe"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM AikoServe.exe"; Flags: runhidden; RunOnceId: "KillAikoServe"

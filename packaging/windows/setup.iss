; Required preprocessor definitions are supplied by tools/build_installer.py.
[Setup]
AppId={{FA463DA6-0CE4-45BB-AB31-2581F880B0A7}
AppName=Motion Splitter
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\MotionSplitter
DefaultGroupName=Motion Splitter
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#ArtifactName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MotionSplitter.exe
CloseApplications=yes
[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Motion Splitter"; Filename: "{app}\MotionSplitter.exe"
[Run]
Filename: "{app}\MotionSplitter.exe"; Description: "Launch Motion Splitter"; Flags: nowait postinstall skipifsilent
; No user data, settings or models in UninstallDelete.

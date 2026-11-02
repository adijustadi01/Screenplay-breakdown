; Inno Setup script — builds ScreenplayBreakdown-Setup.exe from the PyInstaller output.
;   iscc /DAppVersion=0.3.0 desktop\installer.iss
#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif

[Setup]
AppId={{6C1F4B2E-8E2B-4E58-9B5A-5C0D3A7E21B4}
AppName=Screenplay Breakdown
AppVersion={#AppVersion}
AppPublisher=Screenplay Breakdown
DefaultDirName={autopf}\Screenplay Breakdown
DefaultGroupName=Screenplay Breakdown
; Installs for the current user only: no administrator rights needed.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=ScreenplayBreakdown-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Screenplay Breakdown

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\dist\ScreenplayBreakdown\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Screenplay Breakdown"; Filename: "{app}\ScreenplayBreakdown.exe"
Name: "{group}\Sample scripts"; Filename: "{app}\_internal\samples"
Name: "{group}\Uninstall Screenplay Breakdown"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Screenplay Breakdown"; Filename: "{app}\ScreenplayBreakdown.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ScreenplayBreakdown.exe"; Description: "Start Screenplay Breakdown now"; Flags: nowait postinstall skipifsilent

; Your projects live in %APPDATA%\ScreenplayBreakdown and are kept when you uninstall.

; Inno Setup script – creates SDJMobileCare_Setup_<version>.exe
; Build the app first (pyinstaller --noconfirm sdj_mobile_care.spec), then compile this file with Inno Setup 6.
; Shop data lives in %LOCALAPPDATA%\SDJ Mobile Care – uninstalling the app never deletes it.

#define MyAppName "SDJ Mobile Care"
#define MyAppVersion "1.4.0"
#define MyAppExeName "SDJMobileCare.exe"

[Setup]
AppId={{6F3B2C9A-41D7-4E8B-9C2F-5A7D1E3B8C40}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=SDJ Mobile Care
DefaultDirName={autopf}\SDJ Mobile Care
DefaultGroupName=SDJ Mobile Care
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=SDJMobileCare_Setup_{#MyAppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\SDJMobileCare\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SDJ Mobile Care"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\SDJ Mobile Care (demo data)"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--demo"
Name: "{group}\Uninstall SDJ Mobile Care"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SDJ Mobile Care"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Phone link: let linked phones reach this PC on the shop Wi-Fi (TCP 47810 / UDP 47811, this program only).
; Every request is encrypted with the phone's own key, so nothing is open to unlinked devices.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SDJ Mobile Care phone sync"""; Flags: runhidden waituntilterminated; StatusMsg: "Allowing phone sync on the shop network..."; Check: IsAdminInstallMode
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SDJ Mobile Care phone sync (TCP 47810)"""; Flags: runhidden waituntilterminated; Check: IsAdminInstallMode
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SDJ Mobile Care phone sync (UDP 47811)"""; Flags: runhidden waituntilterminated; Check: IsAdminInstallMode
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""SDJ Mobile Care phone sync"" dir=in action=allow program=""{app}\{#MyAppExeName}"" enable=yes profile=any"; Flags: runhidden waituntilterminated; Check: IsAdminInstallMode
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""SDJ Mobile Care phone sync (TCP 47810)"" dir=in action=allow protocol=TCP localport=47810 enable=yes profile=any"; Flags: runhidden waituntilterminated; Check: IsAdminInstallMode
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""SDJ Mobile Care phone sync (UDP 47811)"" dir=in action=allow protocol=UDP localport=47811 enable=yes profile=any"; Flags: runhidden waituntilterminated; Check: IsAdminInstallMode
Filename: "{app}\{#MyAppExeName}"; Description: "Launch SDJ Mobile Care"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SDJ Mobile Care phone sync"""; Flags: runhidden; RunOnceId: "SDJFirewall"; Check: IsAdminInstallMode
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SDJ Mobile Care phone sync (TCP 47810)"""; Flags: runhidden; RunOnceId: "SDJFirewallTcp"; Check: IsAdminInstallMode
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SDJ Mobile Care phone sync (UDP 47811)"""; Flags: runhidden; RunOnceId: "SDJFirewallUdp"; Check: IsAdminInstallMode

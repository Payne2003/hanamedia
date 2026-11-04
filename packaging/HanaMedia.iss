; Inno Setup: tạo HanaMedia-Setup-<phiên bản>.exe từ dist\HanaMedia.
; Cài vào %LOCALAPPDATA%\Programs\HanaMedia (không cần quyền quản trị).
; Dữ liệu (tài khoản, hàng đợi, Chrome đã đăng nhập) nằm riêng ở %LOCALAPPDATA%\HanaMedia\data:
; cài bản mới đè lên hoặc gỡ cài đặt đều KHÔNG xóa dữ liệu này.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{7B6C2A5E-3F1D-4C8E-9A61-2D4F5E8B9C10}
AppName=HANA MEDIA
AppVersion={#AppVersion}
AppPublisher=HANA MEDIA
DefaultDirName={localappdata}\Programs\HanaMedia
DefaultGroupName=HANA MEDIA
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=HanaMedia-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=HANA MEDIA
UninstallDisplayIcon={app}\HanaMedia.exe
SetupIconFile=hanamedia.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng ngoài Desktop"; GroupDescription: "Biểu tượng:"

[Files]
Source: "..\dist\HanaMedia\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Xóa thư viện của bản cũ trước khi chép bản mới (không đụng tới dữ liệu ở %LOCALAPPDATA%\HanaMedia).
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\HANA MEDIA"; Filename: "{app}\HanaMedia.exe"
Name: "{autodesktop}\HANA MEDIA"; Filename: "{app}\HanaMedia.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HanaMedia.exe"; Description: "Mở HANA MEDIA"; Flags: nowait postinstall skipifsilent

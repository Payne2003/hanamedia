param(
  [string]$ExtensionId = "",            # leave empty: computed from the unpacked extension folder path
  [string]$Python = "",                 # leave empty: python on PATH
  [switch]$Uninstall
)
# Registers the Native Messaging host for the current user only (HKCU), no admin rights needed.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$name = "com.hanamedia.short"
$regKey = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$name"
$manifestPath = Join-Path $here "$name.json"

if ($Uninstall) {
  Remove-Item $regKey -ErrorAction SilentlyContinue
  Remove-Item $manifestPath -ErrorAction SilentlyContinue
  "Đã gỡ native host $name"; exit 0
}

if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
if (-not $ExtensionId) {
  # Chrome derives an unpacked extension's ID from SHA-256 of its absolute path (UTF-16LE on Windows),
  # first 32 hex chars mapped 0-f -> a-p.
  $extDir = (Resolve-Path (Join-Path $root "extension")).Path
  $bytes = [System.Text.Encoding]::Unicode.GetBytes($extDir)
  $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  $hex = ($hash | ForEach-Object { $_.ToString("x2") }) -join ""
  $ExtensionId = -join ($hex.Substring(0, 32).ToCharArray() | ForEach-Object { [char]([Convert]::ToInt32([string]$_, 16) + 97) })
  "ID extension tính từ đường dẫn $extDir : $ExtensionId"
  "(Nếu chrome://extensions hiển thị ID khác, chạy lại với -ExtensionId <ID thật>)"
}

$bat = Join-Path $here "hana_host.bat"
@"
@echo off
set PYTHONIOENCODING=utf-8
"$Python" -u "%~dp0hana_host.py" %*
"@ | Set-Content -Path $bat -Encoding ASCII

$manifest = [ordered]@{
  name = $name
  description = "HanaMedia Short job bridge"
  path = $bat
  type = "stdio"
  allowed_origins = @("chrome-extension://$ExtensionId/")
}
[System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
New-Item -Path $regKey -Force | Out-Null
Set-ItemProperty -Path $regKey -Name "(default)" -Value $manifestPath
"Đã đăng ký $name -> $manifestPath"
"Python: $Python"

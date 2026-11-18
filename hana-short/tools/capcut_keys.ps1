param([string]$Keys = "^e;WAIT:4;{ENTER}", [string]$Process = "CapCut")
# Bring the CapCut main window to the front and send a keyboard sequence (SendKeys syntax, ';'-separated,
# "WAIT:n" pauses n seconds). CapCut's Qt UI exposes no UIA controls, so shortcuts are the only lever.
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W { [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }
"@
$p = Get-Process -Name $Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { "NO_WINDOW"; exit 0 }
[void][W]::ShowWindow($p.MainWindowHandle, 9)
[void][W]::SetForegroundWindow($p.MainWindowHandle)
Start-Sleep -Milliseconds 600
if ([W]::GetForegroundWindow() -ne $p.MainWindowHandle) { "NOT_FOREGROUND"; }
$sh = New-Object -ComObject WScript.Shell
foreach ($k in $Keys.Split(';')) {
  if ($k -like 'WAIT:*') { Start-Sleep -Seconds ([double]$k.Substring(5)); continue }
  $sh.SendKeys($k); "SENT $k"; Start-Sleep -Milliseconds 400
}
"DONE"

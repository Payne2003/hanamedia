param([int]$X = 0, [int]$Y = 0, [int]$W = 0, [int]$H = 0, [string]$Out = "capture.png")
# Capture a screen region (whole primary screen when W/H are 0) to PNG. Used to locate CapCut's export
# dialog controls, which UI Automation cannot see.
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
"@
[void][Dpi]::SetProcessDPIAware()
if ($W -le 0 -or $H -le 0) { $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; $X = $b.X; $Y = $b.Y; $W = $b.Width; $H = $b.Height }
$bmp = New-Object System.Drawing.Bitmap $W, $H
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($X, $Y, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"saved $Out ${W}x${H} at $X,$Y"

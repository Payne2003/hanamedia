param([string]$Process = "CapCut", [int]$Depth = 6, [int]$Max = 400)
# Dump the UI Automation tree of every top-level window owned by $Process as JSON.
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$ae = [System.Windows.Automation.AutomationElement]
$procs = Get-Process -Name $Process -ErrorAction SilentlyContinue
if (-not $procs) { @{ ok = $false; error = "process_not_running" } | ConvertTo-Json; exit 0 }
$ids = @($procs | ForEach-Object { $_.Id })
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$out = New-Object System.Collections.ArrayList
function Walk($el, $d) {
  if ($out.Count -ge $Max -or $d -gt $Depth) { return }
  try { $c = $el.Current } catch { return }
  $t = ($c.ControlType.ProgrammaticName -replace 'ControlType\.', '')
  $r = $c.BoundingRectangle
  $item = [ordered]@{ d = $d; type = $t; name = $c.Name; aid = $c.AutomationId; cls = $c.ClassName; enabled = $c.IsEnabled;
    rect = @([int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height) }
  [void]$out.Add($item)
  $ch = $walker.GetFirstChild($el)
  while ($ch -ne $null) { Walk $ch ($d + 1); $ch = $walker.GetNextSibling($ch) }
}
$root = $ae::RootElement
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
$wins = @($all | Where-Object { $ids -contains $_.Current.ProcessId })
foreach ($w in $wins) { Walk $w 0 }
[ordered]@{ ok = $true; windows = @($wins | ForEach-Object { $_.Current.Name }); elements = $out } | ConvertTo-Json -Depth 6 -Compress

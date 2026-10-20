
@echo off
setlocal
set "P=%TEMP%\dvd_%RANDOM%.ps1"

> "%P%" echo Add-Type -AssemblyName System.Windows.Forms
>>"%P%" echo Add-Type -AssemblyName System.Drawing
>>"%P%" echo $f = New-Object Windows.Forms.Form
>>"%P%" echo $f.FormBorderStyle = 'None'
>>"%P%" echo $f.TopMost = $true
>>"%P%" echo $f.ShowInTaskbar = $false
>>"%P%" echo $f.StartPosition = 'Manual'
>>"%P%" echo $f.ClientSize = New-Object Drawing.Size(300,150)
>>"%P%" echo $f.BackColor = [Drawing.Color]::Magenta
>>"%P%" echo $f.TransparencyKey = [Drawing.Color]::Magenta
>>"%P%" echo $l = New-Object Windows.Forms.Label
>>"%P%" echo $l.Text = 'DVD'
>>"%P%" echo $l.Font = New-Object Drawing.Font('Arial',36,[Drawing.FontStyle]::Bold)
>>"%P%" echo $l.ForeColor = [Drawing.Color]::Black
>>"%P%" echo $l.BackColor = [Drawing.Color]::Magenta
>>"%P%" echo $l.TextAlign = 'MiddleCenter'
>>"%P%" echo $l.Dock = 'Fill'
>>"%P%" echo $l.Cursor = [Windows.Forms.Cursors]::Hand
>>"%P%" echo $f.Controls.Add($l)
>>"%P%" echo $s = @{x=100.0;y=100.0;dx=2.0;dy=1.5}
>>"%P%" echo $t = New-Object Windows.Forms.Timer
>>"%P%" echo $t.Interval = 20
>>"%P%" echo $t.Add_Tick({
>>"%P%" echo $b = [Windows.Forms.Screen]::PrimaryScreen.Bounds
>>"%P%" echo $mx = $b.Width - $f.Width
>>"%P%" echo $my = $b.Height - $f.Height
>>"%P%" echo $s.x += $s.dx
>>"%P%" echo $s.y += $s.dy
>>"%P%" echo if ($s.x -le 0) { $s.x=0; $s.dx=[Math]::Abs($s.dx) }
>>"%P%" echo elseif ($s.x -ge $mx) { $s.x=$mx; $s.dx=-[Math]::Abs($s.dx) }
>>"%P%" echo if ($s.y -le 0) { $s.y=0; $s.dy=[Math]::Abs($s.dy) }
>>"%P%" echo elseif ($s.y -ge $my) { $s.y=$my; $s.dy=-[Math]::Abs($s.dy) }
>>"%P%" echo $f.Location = New-Object Drawing.Point([int]$s.x,[int]$s.y)
>>"%P%" echo })
>>"%P%" echo $close = { $t.Stop(); $f.Close() }
>>"%P%" echo $l.Add_Click($close)
>>"%P%" echo $f.Add_Click($close)
>>"%P%" echo $f.Add_Shown({ $t.Start() })
>>"%P%" echo $f.Add_FormClosed({ Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue })
>>"%P%" echo [void]$f.ShowDialog()

if not exist "%P%" exit /b 1

start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%P%"
endlocal
exit

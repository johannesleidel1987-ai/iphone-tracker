# Legt die Windows-Aufgabe "iPhone Duo Apple-Check" an: alle 5 Minuten, nur aktueller Benutzer, ohne Admin.
# Aufruf im Ordner local:  powershell -ExecutionPolicy Bypass -File install_task.ps1
# Entfernen:               Unregister-ScheduledTask -TaskName "iPhone Duo Apple-Check" -Confirm:$false

$ErrorActionPreference = "Stop"
$TaskName = "iPhone Duo Apple-Check"
$Script = Join-Path $PSScriptRoot "apple_check.py"

# pythonw.exe startet ohne Konsolenfenster
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $py = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
    if ($py) { $pyw = Join-Path (Split-Path $py) "pythonw.exe" }
}
if (-not $pyw -or -not (Test-Path $pyw)) { throw "pythonw.exe nicht gefunden. Python-Installation pruefen." }
if (-not (Test-Path (Join-Path $PSScriptRoot "config.json"))) { throw "config.json fehlt (Vorlage: config.example.json)." }

$action   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$Script`"" -WorkingDirectory $PSScriptRoot
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
              -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -StartWhenAvailable -MultipleInstances IgnoreNew `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 3)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Apple-Store-Bestand iPhone Duo 256 GB Sternenweiss (Muenchen, Augsburg, Sindelfingen)" -Force | Out-Null

Write-Host "Aufgabe '$TaskName' angelegt: alle 5 Minuten mit $pyw"
Write-Host "Log: $(Join-Path $PSScriptRoot 'apple_check.log')"

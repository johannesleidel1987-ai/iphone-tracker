# Legt die Windows-Aufgabe "iPhone Duo eBay-Check" an: alle 2 Minuten, nur aktueller Benutzer, ohne Admin.
# Aufruf im Ordner local:  powershell -ExecutionPolicy Bypass -File install_task.ps1
# Entfernen:               Unregister-ScheduledTask -TaskName "iPhone Duo eBay-Check" -Confirm:$false

$ErrorActionPreference = "Stop"
$TaskName = "iPhone Duo eBay-Check"
$Script = Join-Path $PSScriptRoot "ebay_check.py"

# pythonw.exe startet ohne Konsolenfenster
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $py = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
    if ($py) { $pyw = Join-Path (Split-Path $py) "pythonw.exe" }
}
if (-not $pyw -or -not (Test-Path $pyw)) { throw "pythonw.exe nicht gefunden. Python-Installation pruefen." }
$libsOk = $true
try { & $pyw -c "import requests, bs4" 2>$null; if ($LASTEXITCODE -ne 0) { $libsOk = $false } } catch { $libsOk = $false }
if (-not $libsOk) { throw "requests/beautifulsoup4 fehlen: py -m pip install requests beautifulsoup4" }
if (-not (Test-Path (Join-Path $PSScriptRoot "config.json"))) { throw "config.json fehlt (Vorlage: config.example.json)." }

$action   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$Script`"" -WorkingDirectory $PSScriptRoot
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
              -RepetitionInterval (New-TimeSpan -Minutes 2) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -StartWhenAvailable -MultipleInstances IgnoreNew `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "eBay-Angebote iPhone Duo 256 GB Sternenweiss (Sofort-Kaufen und Auktionen < 24h)" -Force | Out-Null

Write-Host "Aufgabe '$TaskName' angelegt: alle 2 Minuten mit $pyw"
Write-Host "State: $(Join-Path $PSScriptRoot 'ebay_state.json')"

param(
    [string]$TaskName = "terminal_check2_bot",
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
)

$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$MainModule = "src.main"

if (-not (Test-Path $Python)) {
    throw "Python virtualenv not found: $Python"
}

if (-not (Test-Path (Join-Path $ProjectDir ".env"))) {
    throw ".env not found. Run telegram_setup.py first."
}

$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m $MainModule" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "terminal_check2_bot Telegram port operation monitor" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started Windows scheduled task: $TaskName"

param(
    [string]$MediaShare = '\\192.168.1.10\Media',
    [string]$Pythonw = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\pythonw.exe",
    [string]$EncoderScript = "$env:LOCALAPPDATA\OrthrusMediaEncoder\desktop_encoder.py",
    [string]$TaskName = 'Orthrus Desktop GPU Encoder'
)

$ErrorActionPreference = 'Stop'
$programRoot = Join-Path $env:ProgramData 'OrthrusMediaEncoder'
$watchdogRoot = Join-Path $programRoot 'watchdog'
$project = Join-Path $PSScriptRoot 'EncoderWatchdog\EncoderWatchdog.csproj'
$publish = Join-Path $env:TEMP 'jellypi-encoder-watchdog'
$heartbeat = Join-Path $MediaShare '.media-encoder\desktop-heartbeat.json'
$pidFile = Join-Path $programRoot 'desktop-encoder.pid.json'
$installedScript = Join-Path $programRoot 'desktop_encoder.py'

if (-not (Test-Path -LiteralPath $Pythonw)) { throw "Missing Python launcher: $Pythonw" }
if (-not (Test-Path -LiteralPath $EncoderScript)) { throw "Missing encoder script: $EncoderScript" }

New-Item -ItemType Directory -Force -Path $watchdogRoot | Out-Null
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $publish -Recurse -Force -ErrorAction SilentlyContinue
dotnet publish $project -c Release -r win-x64 --self-contained false -o $publish
Copy-Item -LiteralPath (Join-Path $publish 'EncoderWatchdog.exe') -Destination $watchdogRoot -Force
Copy-Item -LiteralPath $EncoderScript -Destination $installedScript -Force

@{
    pythonw = $Pythonw
    encoderScript = $installedScript
    heartbeat = $heartbeat
    pidFile = $pidFile
    maxHeartbeatAgeSeconds = 120
    startupTimeoutSeconds = 30
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $programRoot 'watchdog.json') -Encoding UTF8

$action = New-ScheduledTaskAction -Execute (Join-Path $watchdogRoot 'EncoderWatchdog.exe')
$minuteTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($logonTrigger, $minuteTrigger) -Settings $settings `
    -Description 'Keeps the preferred JellyPi NVIDIA encoder alive and verifies its heartbeat.' `
    -User $env:USERNAME -RunLevel Limited -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

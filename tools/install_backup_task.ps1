param(
    [string]$At = '03:00',
    [switch]$DescribeOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$manage = Join-Path $projectRoot 'manage.py'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the project .venv first.' }
$name = 'CampusServiceDeskDailyBackup'
$description = [ordered]@{
    TaskName = $name
    Schedule = "Daily $At (Windows local time)"
    Execute = $python
    Arguments = '"' + $manage + '" backup_site'
    WorkingDirectory = $projectRoot
    Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
}
if ($DescribeOnly) { $description | ConvertTo-Json; exit 0 }
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    throw 'Task already exists. Inspect it before updating; this script does not overwrite tasks.'
}
$action = New-ScheduledTaskAction -Execute $python -Argument $description.Arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $description.Identity -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Daily consistent SQLite and attachment backup; seven daily snapshots; private execution records.' | Select-Object TaskName,State

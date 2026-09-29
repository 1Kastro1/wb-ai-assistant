param([ValidateSet('enable','disable','status')][string]$Action='status')
$ErrorActionPreference='Stop'
$TaskName='WB AI Assistant'
$ProjectRoot=Split-Path -Parent $PSScriptRoot

if($Action -eq 'disable'){
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host 'Автозапуск WB AI Assistant выключен.'
  exit 0
}

if($Action -eq 'status'){
  $task=Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if($task){Write-Host "Автозапуск включён: $($task.State)"}else{Write-Host 'Автозапуск выключен.'}
  exit 0
}

$tray=Join-Path $ProjectRoot 'scripts\tray.ps1'
$arguments="-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$tray`""
$taskAction=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 0) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Settings $settings -Description 'Запускает локальный WB AI Assistant после входа в Windows.' -Force | Out-Null
Write-Host 'Автозапуск WB AI Assistant включён. При следующем входе откроется приложение.'

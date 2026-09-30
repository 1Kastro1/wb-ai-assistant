param([ValidateSet('install','start','stop','setup','backup','restore','update','test')][string]$Action='start',[string]$BackupName)
$ErrorActionPreference='Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:NEXT_TELEMETRY_DISABLED='1'
$env:PYTHONUTF8='1'
if(Test-Path -LiteralPath (Join-Path $ProjectRoot '.env')) {
  foreach($line in Get-Content -LiteralPath (Join-Path $ProjectRoot '.env')) {
    if($line -match '^(OLLAMA_MODEL|WB_DATA_DIR|WB_ENABLE_REAL_PUBLISH|WB_PUBLIC_ORIGIN)=(.*)$') {
      [Environment]::SetEnvironmentVariable($matches[1],$matches[2].Trim(),'Process')
    }
  }
}
$RuntimeRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies'
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if(!(Test-Path -LiteralPath $PythonExe)) {
  $ParentPython = Join-Path (Split-Path -Parent $ProjectRoot) '.venv\Scripts\python.exe'
  if(Test-Path -LiteralPath $ParentPython){$PythonExe=$ParentPython}
}
$NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
$NodeExe = if($NodeCommand){$NodeCommand.Source}else{Join-Path $RuntimeRoot 'node\bin\node.exe'}
$NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
$PnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if($NpmCommand){$PackageManager=$NpmCommand.Source;$UsePnpm=$false}
elseif($PnpmCommand){$PackageManager=$PnpmCommand.Source;$UsePnpm=$true}
else{$PackageManager=Join-Path $RuntimeRoot 'bin\fallback\pnpm.cmd';$UsePnpm=$true}
$DataRoot = if($env:WB_DATA_DIR){$env:WB_DATA_DIR}else{Join-Path $env:LOCALAPPDATA 'WBAIAssistant\data'}
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
if($DataRoot -match '(?i)(onedrive|dropbox|google drive|[\\/]public[\\/]|[\\/]static[\\/])'){throw 'Choose a data directory outside cloud-sync/public folders.'}
$env:WB_DATA_DIR=$DataRoot
$RunRoot = Join-Path $DataRoot 'run'
New-Item -ItemType Directory -Force -Path $RunRoot | Out-Null
$StateFile=Join-Path $RunRoot 'processes.json'

function Run-Python([string[]]$Arguments){ & $PythonExe @Arguments; if($LASTEXITCODE -ne 0){throw "Python failed ($LASTEXITCODE)"} }
function Run-Package([string[]]$Arguments){ & $PackageManager @Arguments; if($LASTEXITCODE -ne 0){throw "Package command failed ($LASTEXITCODE)"} }
function Stop-App {
  if(Test-Path -LiteralPath $StateFile){
    $entries=Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    foreach($entry in $entries){
      $proc=Get-Process -Id $entry.id -ErrorAction SilentlyContinue
      if($proc -and $proc.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.start){
        $proc.Kill()
        $null=$proc.WaitForExit(5000)
      }
    }
    Remove-Item -LiteralPath $StateFile
  }
}

Push-Location $ProjectRoot
try {
  if($Action -eq 'stop'){Stop-App;Write-Host 'Application stopped.';exit 0}
  if($Action -in @('install','update')){
    if($Action -eq 'update'){Stop-App}
    if(!(Test-Path -LiteralPath $PythonExe)){
      $SystemPython=Get-Command python.exe -ErrorAction SilentlyContinue
      $BootstrapPython=if($SystemPython){$SystemPython.Source}else{Join-Path $RuntimeRoot 'python\python.exe'}
      if(!(Test-Path -LiteralPath $BootstrapPython)){throw 'Install Python 3.12+ and run install.bat again.'}
      & $BootstrapPython -m venv (Join-Path $ProjectRoot '.venv')
      if($LASTEXITCODE -ne 0){throw 'Cannot create Python environment.'}
      $PythonExe=Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    }
    if(!(Test-Path -LiteralPath $NodeExe)){throw 'Install Node.js 22 LTS.'}
    Run-Python @('-m','pip','install','--timeout','60','-r','backend/requirements.lock.txt')
    Run-Python @('scripts/install_voice.py','--data-root',$DataRoot)
    Push-Location frontend
    try {
      if($UsePnpm){Run-Package @('install','--frozen-lockfile')}else{Run-Package @('install')}
      Run-Package @('run','build')
    } finally {Pop-Location}
    $Action='migrate'
  }
  if(!(Test-Path -LiteralPath $PythonExe)){throw 'Run install.bat first.'}
  Push-Location backend
  try {
    if($Action -eq 'test'){Run-Python @('-m','pytest','-q');exit 0}
    Run-Python @('-m','alembic','upgrade','head')
    if($Action -eq 'setup'){Run-Python @('-m','app.maintenance','setup');exit 0}
    if($Action -eq 'migrate'){Write-Host 'Installation complete. Run start.bat and create the owner password in the browser.';exit 0}
    if($Action -eq 'backup'){Run-Python @('-m','app.maintenance','backup');exit 0}
    if($Action -eq 'restore'){
      Stop-App
      if(!$BackupName){$BackupName=Read-Host 'Backup filename (.db)'}
      Run-Python @('-m','app.maintenance','restore',$BackupName)
      exit 0
    }
  } finally {Pop-Location}
  if(Test-Path -LiteralPath $StateFile){throw 'Application may already be running. Use stop.bat first.'}
  foreach($port in @(8000,3000)){
    $listener=Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if($listener){throw "Port $port is occupied. Stop the other application first."}
  }
  if(!(Test-Path -LiteralPath (Join-Path $ProjectRoot 'frontend\.next\BUILD_ID'))){throw 'Frontend build missing. Run install.bat.'}
  $BackendProcess = Start-Process -FilePath $PythonExe -ArgumentList '-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log' -WorkingDirectory (Join-Path $ProjectRoot 'backend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $RunRoot 'backend.out.log') -RedirectStandardError (Join-Path $RunRoot 'backend.err.log')
  try {
    $NextPath=Join-Path $ProjectRoot 'frontend\node_modules\next\dist\bin\next'
    $FrontendProcess = Start-Process -FilePath $NodeExe -ArgumentList ('"'+$NextPath+'" start -H 127.0.0.1 -p 3000') -WorkingDirectory (Join-Path $ProjectRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $RunRoot 'frontend.out.log') -RedirectStandardError (Join-Path $RunRoot 'frontend.err.log')
    $healthy=$false
    for($attempt=0;$attempt -lt 30;$attempt++){
      try{$null=Invoke-RestMethod 'http://127.0.0.1:8000/health';$null=Invoke-WebRequest 'http://127.0.0.1:3000' -UseBasicParsing;$healthy=$true;break}catch{Start-Sleep -Seconds 1}
    }
    if(!$healthy){throw 'Startup failed. Check logs in LocalAppData/WBAIAssistant/data/run.'}
    $BackendProcess.Refresh()
    $FrontendProcess.Refresh()
    if($BackendProcess.HasExited -or $FrontendProcess.HasExited){throw 'One application process exited during startup. Check local logs.'}
    $managed=@()
    foreach($port in @(8000,3000)){
      $listener=Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop | Select-Object -First 1
      $process=Get-Process -Id $listener.OwningProcess -ErrorAction Stop
      $managed+=@{id=$process.Id;start=$process.StartTime.ToUniversalTime().Ticks.ToString()}
    }
    $managed | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
    if($env:WB_NO_BROWSER -ne '1'){
      try {Start-Process 'http://127.0.0.1:3000'} catch {Write-Host 'Open http://127.0.0.1:3000 in your browser.'}
    }
    Write-Host 'WB AI Assistant: http://127.0.0.1:3000'
  } catch {
    Stop-App
    if(!$BackendProcess.HasExited){$BackendProcess.Kill()}
    throw
  }
} finally {Pop-Location}

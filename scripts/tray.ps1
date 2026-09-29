$ErrorActionPreference='Stop'
$ProjectRoot=Split-Path -Parent $PSScriptRoot
$Manage=Join-Path $PSScriptRoot 'manage.ps1'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Start-LocalAssistant {
  $running=Get-NetTCPConnection -State Listen -LocalPort 3000 -ErrorAction SilentlyContinue
  if(!$running){
    $previous=$env:WB_NO_BROWSER;$env:WB_NO_BROWSER='1'
    try{& $Manage -Action start}catch{[System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'WB Assistant')|Out-Null}
    finally{$env:WB_NO_BROWSER=$previous}
  }
}

$icon=New-Object System.Windows.Forms.NotifyIcon
$icon.Icon=[System.Drawing.SystemIcons]::Application
$icon.Text='WB AI Assistant'
$icon.Visible=$true
$menu=New-Object System.Windows.Forms.ContextMenuStrip
$open=$menu.Items.Add('Открыть WB Assistant')
$restart=$menu.Items.Add('Перезапустить')
$stop=$menu.Items.Add('Остановить приложение')
$null=$menu.Items.Add('-')
$exit=$menu.Items.Add('Закрыть значок')
$icon.ContextMenuStrip=$menu

$open.Add_Click({Start-LocalAssistant;Start-Process 'http://127.0.0.1:3000'})
$icon.Add_DoubleClick({Start-LocalAssistant;Start-Process 'http://127.0.0.1:3000'})
$restart.Add_Click({try{& $Manage -Action stop}catch{};Start-LocalAssistant})
$stop.Add_Click({try{& $Manage -Action stop}catch{[System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'WB Assistant')|Out-Null}})
$exit.Add_Click({$icon.Visible=$false;$icon.Dispose();[System.Windows.Forms.Application]::Exit()})

Start-LocalAssistant
$icon.ShowBalloonTip(3000,'WB AI Assistant','Приложение запущено. Откройте его двойным щелчком по значку.','Info')
[System.Windows.Forms.Application]::Run()

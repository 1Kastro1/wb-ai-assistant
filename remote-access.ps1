$ErrorActionPreference = 'Stop'
$tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
$tailscalePath = if($tailscale){$tailscale.Source}else{'C:\Program Files\Tailscale\tailscale.exe'}
if(!(Test-Path -LiteralPath $tailscalePath)){ throw 'Tailscale is not installed.' }
$status = & $tailscalePath status --json | ConvertFrom-Json
if($status.BackendState -ne 'Running'){ throw 'Sign in to Tailscale and run this script again.' }
& $tailscalePath serve --bg http://127.0.0.1:3000
if($LASTEXITCODE -ne 0){ throw 'Could not enable private Tailscale Serve access.' }
$dns = $status.Self.DNSName.TrimEnd('.')
Write-Host "Private application URL: https://$dns"

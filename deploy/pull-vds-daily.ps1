# Pull newest Dushevye VDS backup archive to the project share.
# Secrets: do NOT hardcode. Prefer SSH key, or set env DUSHEVYE_VDS_PASS for this session only.
$ErrorActionPreference = 'Stop'
$HostName = '178.212.15.231'
$User = 'root'
$RemoteDir = '/var/backups/dushevye'
$DestPrimary = 'Z:\My Progect\Dushevye-Stanok\backups\daily'
$DestSecondary = 'Z:\bobrlink-backups\dushevye-vds'

New-Item -ItemType Directory -Force -Path $DestPrimary | Out-Null

function Invoke-Remote {
  param([string]$RemoteCommand)
  $ssh = Get-Command ssh -ErrorAction SilentlyContinue
  if (-not $ssh) { throw 'OpenSSH client (ssh) not found on PATH' }
  $pass = $env:DUSHEVYE_VDS_PASS
  if ($pass) {
    $plink = Get-Command plink -ErrorAction SilentlyContinue
    if ($plink) {
      & plink.EXE -batch -pw $pass -l $User $HostName $RemoteCommand
      return
    }
  }
  & ssh.exe -o BatchMode=$(if ($pass) { 'no' } else { 'yes' }) -o StrictHostKeyChecking=accept-new "${User}@${HostName}" $RemoteCommand
}

# List newest archive name
$newest = (& ssh.exe -o BatchMode=yes -o StrictHostKeyChecking=accept-new "${User}@${HostName}" "ls -1t $RemoteDir/dushevye-*.tgz 2>/dev/null | head -1").Trim()
if (-not $newest) {
  # fallback: try with plink + env password
  if ($env:DUSHEVYE_VDS_PASS -and (Get-Command plink -ErrorAction SilentlyContinue)) {
    $newest = (& plink.EXE -batch -pw $env:DUSHEVYE_VDS_PASS -l $User $HostName "ls -1t $RemoteDir/dushevye-*.tgz 2>/dev/null | head -1").Trim()
  }
}
if (-not $newest) { throw "No archives found in $RemoteDir (run dushevye-backup.sh on VDS first, and ensure SSH works)." }

$base = Split-Path -Leaf $newest
$local = Join-Path $DestPrimary $base
Write-Host "Fetching $newest -> $local"
& scp.exe -o StrictHostKeyChecking=accept-new "${User}@${HostName}:${newest}" $local
if (-not (Test-Path $local)) { throw "Download failed: $local" }

# Optional duplicate
try {
  New-Item -ItemType Directory -Force -Path $DestSecondary -ErrorAction Stop | Out-Null
  Copy-Item -Force $local (Join-Path $DestSecondary $base) -ErrorAction Stop
  Write-Host "Also copied to $DestSecondary"
} catch {
  Write-Host "Secondary share skipped: $($_.Exception.Message)"
}
Write-Host "OK $local"

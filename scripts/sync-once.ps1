# Run one complete organize -> archive -> index cycle against the running gateway.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$node = (Get-Command node -ErrorAction Stop).Source
& $node (Join-Path $root 'scripts/hourly_chatos_sync.mjs')
exit $LASTEXITCODE

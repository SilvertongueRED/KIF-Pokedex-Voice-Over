# Repair .git/packed-refs after a truncated trailing write corrupted it
# ("fatal: unterminated line in .git/packed-refs").  Keeps the header and
# every well-formed ref line; drops any malformed/partial line.  Safe: a
# backup is written to packed-refs.bak and loose refs/objects are untouched.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$p = Join-Path $repo '.git\packed-refs'
if (-not (Test-Path $p)) { Write-Host 'No packed-refs found - nothing to repair.'; exit 0 }
Copy-Item $p "$p.bak" -Force
$lines = Get-Content -LiteralPath $p
$good  = $lines | Where-Object {
    $_ -match '^#' -or $_ -match '^[0-9a-fA-F]{40} \S' -or $_ -match '^\^[0-9a-fA-F]{40}$'
}
$dropped = $lines.Count - $good.Count
[System.IO.File]::WriteAllText($p, ($good -join "`n") + "`n")
Write-Host "packed-refs repaired: kept $($good.Count) line(s), dropped $dropped malformed line(s)."
Write-Host "Backup at $p.bak"

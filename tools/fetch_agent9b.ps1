param([string]$Root = 'D:\AnimaStudio')
# Fetch the 9B brain. Same resume + stall-recovery pattern as fetch_ms.ps1:
# curl -C - continues a partial file, and a dead-stalled transfer is restarted.
$ErrorActionPreference = 'Continue'
$out = Join-Path $Root 'tools\gguf\Qwen3.5-9B-Q4_K_M.gguf'
$u = 'https://www.modelscope.cn/models/unsloth/Qwen3.5-9B-GGUF/resolve/master/Qwen3.5-9B-Q4_K_M.gguf'

function Get-RemoteLength([string]$url) {
    $h = & curl.exe -sIL -m 30 $url 2>$null
    $l = ($h -split "`n" | Where-Object { $_ -match '^[Cc]ontent-[Ll]ength:' } | Select-Object -Last 1)
    if ($l) {
        $v = [int64](($l -replace '[^0-9]', ''))
        if ($v -gt 0) { return $v }
    }
    return 0
}

$want = Get-RemoteLength $u
Write-Output ("TARGET Qwen3.5-9B-Q4_K_M.gguf " + $want + " bytes")
$stall = 0
while ($true) {
    $cur = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
    if ($want -gt 0 -and $cur -ge $want) { break }
    if ($stall -ge 5) { Write-Output ("GIVEUP at " + $cur + "/" + $want); break }
    & curl.exe -L -C - -sS --max-time 300 --speed-limit 131072 --speed-time 45 -o $out $u
    $code = $LASTEXITCODE
    $after = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
    if ($after -le $cur) {
        $stall++
        Write-Output ("  !! retry curl=" + $code + " size=" + $after)
        Start-Sleep -Seconds 3
    } else {
        $stall = 0
        Write-Output ("  .. " + [math]::Round($after / 1MB, 1) + "MB / " + [math]::Round($want / 1MB, 1) + "MB")
    }
}
$fin = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
if ($want -gt 0 -and $fin -ge $want) { Write-Output ("OK " + $fin) }
else { Write-Output ("INCOMPLETE " + $fin + "/" + $want) }
Write-Output ("FETCH-DONE " + (Get-Date -Format 'HH:mm:ss'))
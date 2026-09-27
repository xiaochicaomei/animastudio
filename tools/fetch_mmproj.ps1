param([string]$Root = 'D:\AnimaStudio')
# Fetch the vision projector for the 9B brain: this is what lets the judge look at
# a rendered panel instead of guessing from a tag list.
# Resume + stall recovery, and a size probe that tries Content-Range as a fallback
# (ModelScope's HEAD does not always return Content-Length).
$ErrorActionPreference = 'Continue'
$out = Join-Path $Root 'tools\gguf\mmproj-F16.gguf'
$u = 'https://www.modelscope.cn/models/unsloth/Qwen3.5-9B-GGUF/resolve/master/mmproj-F16.gguf'

function Get-RemoteLength([string]$url) {
    $h = & curl.exe -sIL -m 30 $url 2>$null
    $l = ($h -split "`n" | Where-Object { $_ -match '^[Cc]ontent-[Ll]ength:' } | Select-Object -Last 1)
    if ($l) {
        $v = [int64](($l -replace '[^0-9]', ''))
        if ($v -gt 0) { return $v }
    }
    $r = & curl.exe -sL -r 0-0 -o NUL -D - -m 40 $url 2>$null
    $cr = ($r -split "`n" | Where-Object { $_ -match '^[Cc]ontent-[Rr]ange:' } | Select-Object -Last 1)
    if ($cr -and $cr -match '/(\d+)') { return [int64]$Matches[1] }
    return 0
}

$want = Get-RemoteLength $u
Write-Output ("TARGET mmproj-F16.gguf " + $want + " bytes")
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
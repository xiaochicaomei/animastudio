param([string]$Root = 'D:\AnimaStudio')
$ErrorActionPreference = 'Continue'
$dl = Join-Path $Root '_dl'
New-Item -ItemType Directory -Force -Path $dl | Out-Null

$ms = 'https://www.modelscope.cn/models'
$hf = 'https://hf-mirror.com'

$items = @(
    @{ n = 'anima-base-v1.0.safetensors'; u = "$ms/circlestone-labs/Anima/resolve/master/split_files/diffusion_models/anima-base-v1.0.safetensors" },
    @{ n = 'qwen_3_06b_base.safetensors'; u = "$ms/circlestone-labs/Anima/resolve/master/split_files/text_encoders/qwen_3_06b_base.safetensors" },
    @{ n = 'Qwen3.5-9B-Q4_K_M.gguf';     u = "$ms/unsloth/Qwen3.5-9B-GGUF/resolve/master/Qwen3.5-9B-Q4_K_M.gguf" },
    @{ n = 'wd-eva02-model.onnx';         u = "$ms/fireicewolf/wd-eva02-large-tagger-v3/resolve/master/model.onnx" },
    @{ n = 'selected_tags.csv';           u = "$ms/fireicewolf/wd-eva02-large-tagger-v3/resolve/master/selected_tags.csv" },
    @{ n = 'yolox_m_8xb8-300e_humanart-c2c7a14a.onnx'; u = "$hf/Claquasse/Anima-Control-Pose/resolve/main/detector/yolox_m_8xb8-300e_humanart-c2c7a14a.onnx" },
    @{ n = 'rtmw-dw-x-l_simcc-cocktail14_270e-256x192_20231122.onnx'; u = "$hf/Claquasse/Anima-Control-Pose/resolve/main/detector/rtmw-dw-x-l_simcc-cocktail14_270e-256x192_20231122.onnx" }
)

function Get-RemoteLength([string]$u) {
    $h = & curl.exe -sIL -m 30 $u 2>$null
    $l = ($h -split "`n" | Where-Object { $_ -match '^[Cc]ontent-[Ll]ength:' } | Select-Object -Last 1)
    if ($l) {
        $v = [int64](($l -replace '[^0-9]', ''))
        if ($v -gt 0) { return $v }
    }
    $r = & curl.exe -sL -r 0-0 -o NUL -D - -m 30 $u 2>$null
    $cr = ($r -split "`n" | Where-Object { $_ -match '^[Cc]ontent-[Rr]ange:' } | Select-Object -Last 1)
    if ($cr -and $cr -match '/(\d+)') { return [int64]$Matches[1] }
    return 0
}

Write-Output ("=== mos-group start " + (Get-Date -Format 'HH:mm:ss') + " ===")

foreach ($it in $items) {
    $out = Join-Path $dl $it.n
    $want = Get-RemoteLength $it.u
    Write-Output ("TARGET " + $it.n + " " + $want + " bytes")
    $stall = 0
    while ($true) {
        $cur = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
        if ($want -gt 0 -and $cur -ge $want) { break }
        if ($stall -ge 5) { Write-Output ("GIVEUP " + $it.n + " at " + $cur + "/" + $want); break }
        & curl.exe -L -C - -sS --max-time 300 --speed-limit 131072 --speed-time 45 -o $out $it.u
        $code = $LASTEXITCODE
        $after = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
        if ($after -le $cur) {
            $stall++
            Write-Output ("  !! retry " + $it.n + " curl=" + $code + " size=" + $after)
            Start-Sleep -Seconds 3
        } else {
            $stall = 0
            Write-Output ("  .. " + $it.n + " " + [math]::Round($after / 1MB, 1) + "MB / " + [math]::Round($want / 1MB, 1) + "MB")
        }
    }
    $fin = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
    if ($want -gt 0 -and $fin -ge $want) { Write-Output ("OK " + $it.n + " " + $fin) }
    else { Write-Output ("INCOMPLETE " + $it.n + " " + $fin + "/" + $want) }
}

Write-Output ("MOS-GROUP-DONE " + (Get-Date -Format 'HH:mm:ss'))
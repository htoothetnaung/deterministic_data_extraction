param(
    [string]$LlamaServer = "llama-server",
    [int]$Port = 8080,
    [string]$HostName = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ModelPath = Join-Path $ProjectRoot "model_weights\PaddleOCR-VL-1.6-GGUF\PaddleOCR-VL-1.6-GGUF.gguf"
$MmprojPath = Join-Path $ProjectRoot "model_weights\PaddleOCR-VL-1.6-GGUF\PaddleOCR-VL-1.6-GGUF-mmproj.gguf"

if (-not (Test-Path -LiteralPath $ModelPath)) {
    throw "Model file not found: $ModelPath"
}

if (-not (Test-Path -LiteralPath $MmprojPath)) {
    throw "MM projector file not found: $MmprojPath"
}

& $LlamaServer `
    -m $ModelPath `
    --mmproj $MmprojPath `
    --port $Port `
    --host $HostName `
    --temp 0

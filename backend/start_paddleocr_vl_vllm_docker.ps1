param(
    [int]$Port = 8118,
    [string]$Image = "ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-genai-vllm-server:latest-nvidia-gpu",
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"

$ArgsList = @(
    "run",
    "-it",
    "--rm",
    "--gpus",
    "all",
    "-p",
    "${Port}:${Port}"
)

if ($ConfigPath) {
    $ResolvedConfig = Resolve-Path -LiteralPath $ConfigPath
    $ArgsList += @("-v", "${ResolvedConfig}:/tmp/vllm_config.yml")
}

$ArgsList += @(
    $Image,
    "paddleocr",
    "genai_server",
    "--model_name",
    "PaddleOCR-VL-1.6-0.9B",
    "--host",
    "0.0.0.0",
    "--port",
    "$Port",
    "--backend",
    "vllm"
)

if ($ConfigPath) {
    $ArgsList += @("--backend_config", "/tmp/vllm_config.yml")
}

& docker @ArgsList

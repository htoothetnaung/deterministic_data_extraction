param(
    [string]$Python = "C:\Users\austin\anaconda3\envs\brillar\python.exe",
    [int]$Port = 8118,
    [string]$HostName = "127.0.0.1",
    [string]$ModelName = "PaddleOCR-VL-1.6-0.9B",
    [string]$ConfigPath = "C:\Users\austin\Documents\Brillar_job\genai\data_extraction\backend\paddleocr_vl_vllm_config.yml"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python executable not found: $Python"
}

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$CacheRoot = Join-Path $ProjectRoot "backend\.cache\paddlex"
$env:PADDLE_PDX_CACHE_HOME = $CacheRoot
$env:PADDLEOCR_HOME = Join-Path $CacheRoot "paddleocr"
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"
$env:SSL_CERT_FILE = "C:\Users\austin\anaconda3\envs\brillar\lib\site-packages\certifi\cacert.pem"
$env:CURL_CA_BUNDLE = $env:SSL_CERT_FILE
$env:REQUESTS_CA_BUNDLE = $env:SSL_CERT_FILE

& $Python -c "import importlib.util, platform, sys; print('python', sys.executable); print('platform', platform.system()); print('vllm_available', importlib.util.find_spec('vllm') is not None); sys.exit(0 if importlib.util.find_spec('vllm') is not None and platform.system() != 'Windows' else 2)"
if ($LASTEXITCODE -eq 2) {
    throw "Native vLLM server is not available in this Windows brillar environment. vLLM's supported path is Linux/WSL; use paddleocr_vl_local with llama.cpp on Windows, or start this script inside a Linux/WSL Python env that has vllm installed."
}
if ($LASTEXITCODE -ne 0) {
    throw "vLLM environment check failed."
}

$ArgsList = @(
    "-m",
    "paddlex.inference.genai.server",
    "--model_name",
    $ModelName,
    "--host",
    $HostName,
    "--port",
    "$Port",
    "--backend",
    "vllm"
)

if ($ConfigPath -and (Test-Path -LiteralPath $ConfigPath)) {
    $ArgsList += @("--backend_config", $ConfigPath)
}

& $Python @ArgsList

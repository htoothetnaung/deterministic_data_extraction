# PaddleOCR-VL API setup

PaddleOCR-VL is the cloud/API parser path. It is different from local
PaddleOCR: local PaddleOCR detects/recognizes text, while PaddleOCR-VL returns
layout-aware, AI-ready document parsing output.

## Python package

Use the project `brillar` conda environment:

```powershell
conda activate brillar
pip install langchain-paddleocr
```

`langchain-paddleocr` currently declares a newer `langchain-core` dependency
than this environment's existing LangChain 0.2 stack. The adapter has been
verified to import with `langchain-core==0.2.43`, so keep the existing
LangChain line unless the whole project is migrated to LangChain 1.x.

## Environment variables

The backend loads both:

- `.env` at the project root
- `backend/.env`

`backend/.env` wins if both files define the same key.

Recommended values:

```dotenv
EXTRACT_PADDLEOCR_VL_BASE_URL=https://paddleocr.aistudio-app.com
AISTUDIO_ACCESS_TOKEN=your-aistudio-access-token
```

Backward-compatible names also work:

```dotenv
EXTRACT_PADDLEOCR_VL_API_URL=https://paddleocr.aistudio-app.com
PADDLEOCR_ACCESS_TOKEN=your-paddleocr-access-token
EXTRACT_AISTUDIO_ACCESS_TOKEN=your-aistudio-access-token
EXTRACT_PADDLEOCR_ACCESS_TOKEN=your-paddleocr-access-token
```

Token priority:

```text
1. EXTRACT_AISTUDIO_ACCESS_TOKEN
2. EXTRACT_PADDLEOCR_ACCESS_TOKEN
3. AISTUDIO_ACCESS_TOKEN
4. PADDLEOCR_ACCESS_TOKEN
5. ${AISTUDIO_CACHE_HOME}/.cache/aistudio/.auth/token
```

The adapter publishes the resolved token to `PADDLEOCR_ACCESS_TOKEN` before
calling the official loader, because the SDK also reads that variable
internally.

## Verify without making an API call

```powershell
cd C:\Users\austin\Documents\Brillar_job\genai\data_extraction\backend
C:\Users\austin\anaconda3\envs\brillar\python.exe -c "from app.services.parsers import paddleocr_vl; print(paddleocr_vl.is_available()); print(paddleocr_vl._access_token_source())"
```

Expected output with a configured token:

```text
True
AISTUDIO_ACCESS_TOKEN
```


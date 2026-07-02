"""Application configuration.

Centralised settings for the FastAPI service. Values can be overridden via
environment variables when running in production.
"""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
ROOT_DIR = BASE_DIR.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_DIR = ROOT_DIR / "artifacts"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXTRACT_",
        env_file=(str(ROOT_DIR / ".env"), str(BASE_DIR / ".env")),
        extra="ignore",
    )

    # The FastAPI service runs on its own port (8000). The Next.js gateway
    # forwards requests here using the `XTransformPort` query parameter.
    app_name: str = "Atenxion - Deterministic Document Extraction API"
    app_version: str = "0.1.0"
    debug: bool = True

    host: str = "0.0.0.0"
    port: int = 8000

    upload_dir: str = str(UPLOAD_DIR)
    artifact_dir: str = str(ARTIFACT_DIR)

    # Database (Postgres + pgvector).  Leave empty to use in-memory fallback.
    database_url: str = ""
    embedding_model_name: str = "sentence-transformers/all-mpnet-base-v2"
    embedding_device: str = "cpu"

    # Optional PDF-Extract-Kit adapter. Keep this external because the kit has
    # separate heavyweight dependencies and model weights.
    pdf_extract_kit_repo: str = ""
    pdf_extract_kit_python: str = "python"
    pdf_extract_kit_model_root: str = ""
    pdf_extract_kit_timeout_seconds: int = 1800

    # Optional Mistral OCR adapter. Also accepts MISTRAL_API_KEY directly so it
    # can share the standard Mistral environment variable with notebooks.
    mistral_api_key: str = ""
    mistral_ocr_model: str = "mistral-ocr-latest"
    mistral_ocr_table_format: str = "html"
    mistral_ocr_include_images: bool = False
    mistral_ocr_confidence_granularity: str = "page"
    mistral_ocr_extract_header: bool = True
    mistral_ocr_extract_footer: bool = True
    mistral_ocr_timeout_seconds: int = 900
    mistral_ocr_max_inline_mb: int = 45
    mistral_ocr_ca_bundle: str = ""

    # Optional PaddleOCR-VL adapter via langchain-paddleocr. Local PaddleOCR
    # does not need these values; it uses the installed paddleocr package.
    paddleocr_vl_api_url: str = ""
    paddleocr_vl_base_url: str = ""
    paddleocr_access_token: str = ""
    aistudio_access_token: str = ""
    paddleocr_vl_timeout_seconds: int = 900
    paddleocr_vl_local_server_url: str = "http://127.0.0.1:8080/v1"
    paddleocr_vl_local_model_path: str = ""
    paddleocr_vl_local_mmproj_path: str = ""
    paddleocr_vl_local_max_pages: int = 1
    paddleocr_vl_local_max_pixels: int = 1003520
    paddleocr_vl_local_max_new_tokens: int = 2048
    paddleocr_vl_vllm_server_url: str = "http://127.0.0.1:8118/v1"
    paddleocr_vl_vllm_model_name: str = "PaddleOCR-VL-1.6-0.9B"
    paddleocr_vl_vllm_api_key: str = ""
    paddleocr_vl_vllm_max_pages: int = 1
    paddleocr_vl_vllm_max_pixels: int = 1003520
    paddleocr_vl_vllm_max_new_tokens: int = 2048
    paddleocr_lang: str = "en"
    paddleocr_use_gpu: bool = False
    paddleocr_device: str = ""
    paddleocr_max_pages: int = 2
    paddleocr_pdf_zoom: float = 1.5

    # Optional DocLayout-YOLO demo parser. The model path is explicit so this
    # parser never downloads weights during overnight document runs.
    doclayout_yolo_model_path: str = ""
    doclayout_yolo_img_size: int = 1024
    doclayout_yolo_confidence: float = 0.25
    doclayout_yolo_pdf_zoom: float = 1.5

    # Docling parser options mirror the Atenxion Docling service request shape.
    docling_service_url: str = ""
    docling_timeout_seconds: int = 900
    docling_do_table_structure: bool = True
    docling_image_export_mode: str = "placeholder"
    docling_do_ocr: bool = False
    docling_force_ocr: bool = False
    docling_ocr_engine: str = "auto"
    docling_ocr_lang: str = "eng"
    docling_to_formats: str = "md,json"
    docling_pipeline: str = "standard"
    docling_max_pages: int = 0
    docling_generate_page_images: bool = False
    docling_generate_picture_images: bool = False
    docling_generate_table_images: bool = False
    docling_force_backend_text: bool = False
    docling_accelerator_device: str = "auto"
    docling_accelerator_threads: int = 4

    # CORS - allow the Next.js dev server (port 3000) and the gateway origin.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


settings = Settings()

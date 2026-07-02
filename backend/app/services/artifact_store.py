"""Local filesystem artifact storage for production case processing."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import BinaryIO

from app.core.config import settings
from app.models.parser_benchmark import ParserRunResult
from app.services.parsers.persistence import safe_name


class ArtifactStore:
    """Small local-disk artifact store with an object-storage-shaped boundary."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.artifact_dir)

    def case_dir(self, case_id: str) -> Path:
        return self.root / safe_name(case_id)

    def document_dir(self, case_id: str, document_id: str) -> Path:
        return self.case_dir(case_id) / safe_name(document_id)

    def store_raw(self, case_id: str, document_id: str, file_obj: BinaryIO, filename: str) -> tuple[str, str, int]:
        """Persist a raw upload and return storage path, sha256, and byte count."""
        raw_dir = self.document_dir(case_id, document_id) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / safe_name(filename)
        hasher = hashlib.sha256()
        size = 0
        with path.open("wb") as out:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                out.write(chunk)
                size += len(chunk)
        return str(path), hasher.hexdigest(), size

    def copy_raw(self, case_id: str, document_id: str, source: str | Path, filename: str | None = None) -> tuple[str, str, int]:
        source_path = Path(source)
        raw_dir = self.document_dir(case_id, document_id) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        target = raw_dir / safe_name(filename or source_path.name)
        hasher = hashlib.sha256()
        size = 0
        with source_path.open("rb") as inp, target.open("wb") as out:
            while True:
                chunk = inp.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                out.write(chunk)
                size += len(chunk)
        return str(target), hasher.hexdigest(), size

    def store_parse_output(
        self,
        case_id: str,
        document_id: str,
        parser_id: str,
        result: ParserRunResult,
        cleaned_evidence: dict | None = None,
    ) -> str:
        parser_dir = self.document_dir(case_id, document_id) / "parser_outputs" / safe_name(parser_id)
        parser_dir.mkdir(parents=True, exist_ok=True)
        output_path = parser_dir / "output.md"
        structured_path = parser_dir / "structured.json"
        cleaned_path = parser_dir / "cleaned_evidence.json"

        output_path.write_text(result.raw_text or result.text_preview or result.error or "", encoding="utf-8")
        structured_path.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        if cleaned_evidence is not None:
            cleaned_path.write_text(json.dumps(cleaned_evidence, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return str(parser_dir)

    def get_parse_output(self, case_id: str, document_id: str, parser_id: str) -> ParserRunResult | None:
        parser_dir = self.document_dir(case_id, document_id) / "parser_outputs" / safe_name(parser_id)
        structured_path = parser_dir / "structured.json"
        output_path = parser_dir / "output.md"
        if not structured_path.exists():
            return None
        result = ParserRunResult.model_validate(json.loads(structured_path.read_text(encoding="utf-8")))
        if output_path.exists():
            result = result.model_copy(update={"raw_text": output_path.read_text(encoding="utf-8", errors="replace")})
        return result

    def get_image(self, case_id: str, document_id: str, page_number: int) -> Path | None:
        path = self.document_dir(case_id, document_id) / "pages" / f"page_{page_number}.png"
        return path if path.exists() else None

    def hash_reuse(self, case_id: str, file_hash: str) -> tuple[bool, str | None]:
        """Check whether a raw file with this hash already exists in the case tree."""
        hash_path = self.case_dir(case_id) / "hashes" / f"{file_hash}.txt"
        if hash_path.exists():
            stored = hash_path.read_text(encoding="utf-8").strip()
            return bool(stored), stored or None
        return False, None

    def remember_hash(self, case_id: str, file_hash: str, storage_path: str) -> None:
        hash_dir = self.case_dir(case_id) / "hashes"
        hash_dir.mkdir(parents=True, exist_ok=True)
        (hash_dir / f"{file_hash}.txt").write_text(storage_path, encoding="utf-8")

    def reuse_raw(self, case_id: str, document_id: str, existing_storage_path: str, filename: str) -> str:
        raw_dir = self.document_dir(case_id, document_id) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        target = raw_dir / safe_name(filename)
        shutil.copy2(existing_storage_path, target)
        return str(target)


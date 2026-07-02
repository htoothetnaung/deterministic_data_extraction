"""In-memory data store with rich mock seed data.

This is a PLACEHOLDER persistence layer. Swap for a real database
(Postgres, SQLite, etc.) by replacing the ``store`` object with a repository
pattern backed by your ORM of choice (SQLAlchemy / SQLModel / Tortoise).
"""
from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models.batch import BatchProcessingResult
from app.models.benchmark import BenchmarkRun
from app.models.document import (
    DocumentMetadata,
    DocumentSource,
    DocumentStatus,
    DocumentType,
)
from app.models.field import EditableExtractionField, FieldType
from app.models.ocr import BlockType, OcrBlock, OcrResult
from app.models.template import (
    ExtractionTemplate,
    TemplateFieldDefinition,
)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _utc(**kw) -> datetime:
    return datetime.now(timezone.utc).replace(**kw) if kw else datetime.now(timezone.utc)


class _Store:
    def __init__(self) -> None:
        self.documents: dict[str, DocumentMetadata] = {}
        self.ocr_results: dict[str, OcrResult] = {}
        # document_id -> { field_key: value } seed values for mock extraction
        self.seed_field_values: dict[str, dict[str, Any]] = {}
        self.templates: dict[str, ExtractionTemplate] = {}
        self.batches: dict[str, BatchProcessingResult] = {}
        self.benchmarks: dict[str, BenchmarkRun] = {}
        self._run_counter = itertools.count(1)

    def seed(self) -> None:
        """Populate the store with mock data. Called AFTER ``store`` is bound
        at module level so service modules can import ``store`` safely."""
        self._seed()

    # ---- id helpers -----------------------------------------------------
    def gen_id(self, prefix: str) -> str:
        return _id(prefix)

    def gen_run_id(self) -> str:
        n = next(self._run_counter)
        return f"RUN-{datetime.now(timezone.utc).strftime('%Y')}-{n:04d}"

    # ---- seed -----------------------------------------------------------
    def _seed(self) -> None:
        # ---- Corporate documents (placeholder DB) ----
        corp_docs = [
            ("Concrete Cube Test Report — Mix A", DocumentType.REPORT, "construction-reports", 8),
            ("Concrete Cube Test Report — Mix B", DocumentType.REPORT, "construction-reports", 8),
            ("Compressive Strength Report — Column C7", DocumentType.REPORT, "construction-reports", 6),
            ("Invoice INV-2024-0871", DocumentType.INVOICE, "finance", 2),
            ("Invoice INV-2024-0872", DocumentType.INVOICE, "finance", 2),
            ("Purchase Order PO-5523", DocumentType.OTHER, "procurement", 3),
            ("Subcontractor Agreement — Phase 2", DocumentType.CONTRACT, "legal", 14),
            ("Material Delivery Form — Cement", DocumentType.FORM, "logistics", 1),
        ]
        for i, (name, dtype, coll, pages) in enumerate(corp_docs):
            did = f"doc-corp-{i+1:03d}"
            self.documents[did] = DocumentMetadata(
                id=did,
                name=name,
                type=dtype,
                source=DocumentSource.CORPORATE_DB,
                mime_type="application/pdf",
                size_bytes=240_000 + i * 12_000,
                page_count=pages,
                status=DocumentStatus.UPLOADED,
                collection=coll,
                tags=[coll, dtype.value],
                uploaded_at=_utc(),
                confidence=0.0,
            )

        # ---- Uploaded (reviewed) sample document ----
        sample_id = "doc-upl-001"
        self.documents[sample_id] = DocumentMetadata(
            id=sample_id,
            name="Concrete_2s_sample.pdf",
            type=DocumentType.REPORT,
            source=DocumentSource.UPLOAD,
            mime_type="application/pdf",
            size_bytes=318_204,
            page_count=2,
            status=DocumentStatus.OCR_DONE,
            tags=["construction-reports", "report"],
            uploaded_at=_utc(),
            confidence=0.86,
        )

        # ---- OCR result for the sample document ----
        blocks = [
            OcrBlock(id="blk-1", page=1, type=BlockType.HEADING, text="CONSTRUCTION TEST REPORT", confidence=0.97, bbox=[0.08, 0.06, 0.84, 0.05]),
            OcrBlock(id="blk-2", page=1, type=BlockType.KEY_VALUE, text="Testing Institute: SGS Myanmar Ltd.", confidence=0.93, data={"key": "Testing Institute", "value": "SGS Myanmar Ltd."}),
            OcrBlock(id="blk-3", page=1, type=BlockType.KEY_VALUE, text="Reception No.: RC-2024-0418", confidence=0.88, data={"key": "Reception No.", "value": "RC-2024-0418"}),
            OcrBlock(id="blk-4", page=1, type=BlockType.KEY_VALUE, text="Sample Date: 12/03/2024", confidence=0.71, data={"key": "Sample Date", "value": "12/03/2024"}),
            OcrBlock(id="blk-5", page=1, type=BlockType.KEY_VALUE, text="Mix Design: C30/37", confidence=0.58, data={"key": "Mix Design", "value": "C30/37"}),
            OcrBlock(id="blk-6", page=1, type=BlockType.KEY_VALUE, text="Client: Brillar Construction Pte Ltd", confidence=0.95, data={"key": "Client", "value": "Brillar Construction Pte Ltd"}),
            OcrBlock(id="blk-7", page=2, type=BlockType.TABLE, text="Specimen | Age (days) | Load (kN) | Strength (MPa)", confidence=0.82, data={"rows": [["S1", "7", "612", "27.2"], ["S2", "7", "598", "26.6"], ["S3", "28", "745", "33.1"]]}),
            OcrBlock(id="blk-8", page=2, type=BlockType.TEXT, text="Results comply with the specified characteristic strength of 30 MPa at 28 days.", confidence=0.9),
        ]
        overall = round(sum(b.confidence for b in blocks) / len(blocks), 3)
        self.ocr_results[sample_id] = OcrResult(
            id=_id("ocr"),
            document_id=sample_id,
            engine="placeholder-ocr",
            language="en",
            pages=2,
            blocks=blocks,
            overall_confidence=overall,
            processed_at=_utc(),
            edited=False,
            approved=False,
        )
        self.documents[sample_id].confidence = overall

        # Seed editable field values for the sample (used by apply_template)
        self.seed_field_values[sample_id] = {
            "testing_institute": "SGS Myanmar Ltd.",
            "reception_no": "RC-2024-0418",
            "sample_date": "2024-03-12",
            "mix_design": "C30/37",
            "client": "Brillar Construction Pte Ltd",
            "strength_28d": "33.1",
            "compliant": "true",
        }

        # ---- Templates ----
        tpl1 = ExtractionTemplate(
            id="tpl-001",
            name="Construction Test Reports",
            description="Extract header metadata and compressive-strength results from concrete test reports.",
            document_type=DocumentType.REPORT,
            ocr_method="advanced-ocr-standard",
            chunking_strategy="page-by-page",
            max_pages=10,
            loop_condition="EOF",
            version="1.2.0",
            success_rate=0.93,
            usage_count=42,
            source_document_id=sample_id,
            fields=[
                TemplateFieldDefinition(id="f1", label="Testing Institute", key="testing_institute", type=FieldType.TEXT, example_value="SGS Myanmar Ltd.", required=True, validation_rule=None, notes="Lab that performed the test."),
                TemplateFieldDefinition(id="f2", label="Reception No.", key="reception_no", type=FieldType.TEXT, example_value="RC-2024-0418", required=True, validation_rule=r"^RC-\d{4}-\d{4}$", notes="Unique sample reception identifier."),
                TemplateFieldDefinition(id="f3", label="Sample Date", key="sample_date", type=FieldType.DATE, example_value="2024-03-12", required=True, validation_rule=r"^\d{4}-\d{2}-\d{2}$", notes="ISO date."),
                TemplateFieldDefinition(id="f4", label="Mix Design", key="mix_design", type=FieldType.TEXT, example_value="C30/37", required=False, extraction_hint="Look for 'Mix Design' label."),
                TemplateFieldDefinition(id="f5", label="Client", key="client", type=FieldType.TEXT, example_value="Brillar Construction Pte Ltd", required=True),
                TemplateFieldDefinition(id="f6", label="28-day Strength (MPa)", key="strength_28d", type=FieldType.NUMBER, example_value="33.1", required=True, validation_rule=r"^\d+(\.\d+)?$"),
                TemplateFieldDefinition(id="f7", label="Compliant", key="compliant", type=FieldType.BOOLEAN, example_value="true", required=True, options=["true", "false"]),
            ],
        )
        self.templates[tpl1.id] = tpl1

        tpl2 = ExtractionTemplate(
            id="tpl-002",
            name="Invoice Processing",
            description="Extract vendor, line items, totals and tax from supplier invoices.",
            document_type=DocumentType.INVOICE,
            ocr_method="advanced-ocr-standard",
            chunking_strategy="page-by-page",
            max_pages=5,
            loop_condition="EOF",
            version="2.0.1",
            success_rate=0.97,
            usage_count=318,
            fields=[
                TemplateFieldDefinition(id="f1", label="Vendor", key="vendor", type=FieldType.TEXT, required=True),
                TemplateFieldDefinition(id="f2", label="Invoice Number", key="invoice_no", type=FieldType.TEXT, required=True, validation_rule=r"^INV-\d{4}-\d+$"),
                TemplateFieldDefinition(id="f3", label="Invoice Date", key="invoice_date", type=FieldType.DATE, required=True),
                TemplateFieldDefinition(id="f4", label="Subtotal", key="subtotal", type=FieldType.CURRENCY, required=True),
                TemplateFieldDefinition(id="f5", label="Tax", key="tax", type=FieldType.CURRENCY, required=True),
                TemplateFieldDefinition(id="f6", label="Total", key="total", type=FieldType.CURRENCY, required=True, validation_rule=r"^\d+(\.\d+)?$"),
            ],
        )
        self.templates[tpl2.id] = tpl2

        tpl3 = ExtractionTemplate(
            id="tpl-003",
            name="PO Identifier",
            description="Identify purchase order numbers and vendor references.",
            document_type=DocumentType.OTHER,
            ocr_method="advanced-ocr-standard",
            chunking_strategy="page-by-page",
            max_pages=3,
            loop_condition="EOF",
            version="1.0.0",
            success_rate=0.89,
            usage_count=76,
            fields=[
                TemplateFieldDefinition(id="f1", label="PO Number", key="po_no", type=FieldType.TEXT, required=True, validation_rule=r"^PO-\d+$"),
                TemplateFieldDefinition(id="f2", label="Vendor", key="vendor", type=FieldType.TEXT, required=True),
                TemplateFieldDefinition(id="f3", label="Issue Date", key="issue_date", type=FieldType.DATE, required=False),
            ],
        )
        self.templates[tpl3.id] = tpl3

        # ---- Seed field values for some corporate docs so benchmarking has data ----
        self.seed_field_values["doc-corp-001"] = {
            "testing_institute": "SGS Myanmar Ltd.", "reception_no": "RC-2024-0419",
            "sample_date": "2024-03-14", "mix_design": "C30/37", "client": "Brillar Construction Pte Ltd",
            "strength_28d": "32.8", "compliant": "true",
        }
        self.seed_field_values["doc-corp-002"] = {
            "testing_institute": "BV Cambodia", "reception_no": "RC-2024-0420",
            "sample_date": "2024-03-15", "mix_design": "C25/30", "client": "Mekong Builders",
            "strength_28d": "28.4", "compliant": "true",
        }
        self.seed_field_values["doc-corp-003"] = {
            "testing_institute": "Intertek", "reception_no": "RC-2024-0421",
            "sample_date": "2024-03-16", "mix_design": "C35/45", "client": "Skyline Group",
            "strength_28d": "36.2", "compliant": "true",
        }

        # ---- Seed a completed benchmark run so the dashboard is populated ----
        from app.models.benchmark import BenchmarkMetric, BenchmarkRun, BenchmarkStatus
        from app.models.document import utcnow

        benchmark = BenchmarkRun(
            id=self.gen_id("bm"),
            run_id=self.gen_run_id(),
            template_id=tpl1.id,
            template_name=tpl1.name,
            document_ids=["doc-upl-001", "doc-corp-001", "doc-corp-002", "doc-corp-003"],
            status=BenchmarkStatus.COMPLETED,
            finished_at=utcnow(),
            metrics=[
                BenchmarkMetric(name="field_level_accuracy", label="Field-level Accuracy", value=0.91, target=0.95),
                BenchmarkMetric(name="processing_latency", label="Processing Latency", value=220.0, unit="ms", target=1500.0),
            ],
            consistency_samples=[0.91, 0.908, 0.912, 0.91, 0.909],
        )
        self.benchmarks[benchmark.id] = benchmark


store = _Store()
store.seed()

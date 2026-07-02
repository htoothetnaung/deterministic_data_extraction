# ExtractIQ — Architectural Workflow

```mermaid
flowchart TD
    %% ── Frontend ──
    subgraph FE["Next.js Frontend (:3000)"]
        direction LR
        EV["Extraction Lab View<br/>(extraction-lab-view.tsx)"]
        PLV["Parser Lab View<br/>(parser-lab-view.tsx)"]
        APICL["API Client<br/>(lib/api.ts)"]
    end

    %% ── Gateway ──
    GW["Caddy Gateway (:81)"]

    %% ── Backend ──
    subgraph BE["FastAPI Backend (:8000)"]
        direction TB

        subgraph APILayer["API Endpoints (backend/app/api/endpoints/)"]
            EL_EP["extraction_lab.py<br/>POST /run<br/>GET /inputs /schemas /report"]
            DOC_EP["documents.py, cases.py,<br/>extraction.py, review.py,<br/>batch.py, benchmarks.py"]
            PB_EP["parser_benchmarks.py"]
        end

        subgraph Services["Service Layer (backend/app/services/)"]
            direction TB
            EL["extraction_lab.py<br/>── core extraction pipeline ──<br/>run_extraction()<br/>_build_chunks()<br/>_extract_field()<br/>_candidate_chunks()<br/>_extract_raw_value()<br/>_coerce_value()<br/>_build_pydantic_model()"]
            EP["extraction_platform.py<br/>case-based extraction pipeline<br/>parse_case / search_case<br/>run_case_extraction<br/>review / finalize / export"]
            EC["evidence_cleaner.py<br/>deterministic cleanup<br/>normalize blocks, recover tables<br/>flag financial risk content"]
            ORCH["parsers/orchestrator.py<br/>run_parser_benchmark()<br/>PARSERS registry"]
        end

        subgraph Parsers["14 Parser Modules (backend/app/services/parsers/)"]
            MO["mistral_ocr<br/>REST API to Mistral AI"]
            LP["layout_pdfplumber<br/>table/text region detection"]
            PO_VLLM["paddleocr_vl_vllm<br/>vLLM server"]
            DL["docling<br/>local or microservice"]
            P10["+ 10 more:<br/>paddle_ocr, pymupdf, pdfplumber,<br/>pypdf, pillow, unstructured,<br/>pdf_extract_kit, doclayout_yolo_demo,<br/>paddleocr_vl, paddleocr_vl_local"]
        end

        subgraph Models["Pydantic Models (backend/app/models/)"]
            direction LR
            SM["schema.py<br/>ExtractionSchema<br/>FieldExtractionHints"]
            EM_LAB["extraction_lab.py<br/>ExtractionLabSchema<br/>ExtractionRunResponse<br/>ExtractionFieldResult"]
            EM["extraction.py<br/>ExtractionCase<br/>ExtractionResult"]
            PM["parser_benchmark.py<br/>ParserRunResult<br/>ParserStatus"]
        end
    end

    %% ── External Services ──
    subgraph External["External Services"]
        MISTRAL["Mistral AI API"]
        OPENAI["OpenAI API<br/>(GPT-5-mini)"]
        DOCLING_SVC["atenxion-docling<br/>microservice"]
    end

    %% ── Data Stores ──
    subgraph Data["Data Layer"]
        SCHEMAS["data/extraction_schemas/<br/>proxy_form.json,<br/>financial_table_extraction.json,<br/>cross_page_extract.json,<br/>2_corporate_profile.json"]
        PARSER_OUT["backend/parser_outputs/<br/>(cached parser results on disk)"]
        UPLOADS["backend/uploads/"]
        MOCK["backend/app/data/mock.py<br/>(in-memory data store)"]
    end

    %% ── Connections ──
    EV --> APICL
    PLV --> APICL
    APICL --> GW --> EL_EP
    APICL --> GW --> DOC_EP
    APICL --> GW --> PB_EP

    EL_EP --> EL
    DOC_EP --> EP
    PB_EP --> ORCH

    EL --> EC
    EL --> ORCH
    EP --> EC
    EP --> ORCH

    ORCH --> MO --> MISTRAL
    ORCH --> PO_VLLM
    ORCH --> DL --> DOCLING_SVC
    ORCH --> LP
    ORCH --> P10

    EL -->|LLM reconstruction mode| OPENAI
    EL -->|generate report| OPENAI

    EL --> SCHEMAS
    EP --> MOCK
    ORCH --> PARSER_OUT
    ORCH --> UPLOADS

    %% ── Model references ──
    EL -.-> EM_LAB
    EP -.-> EM
    EP -.-> SM
    ORCH -.-> PM
    EC -.-> PM

    %% ── Detailed extraction pipeline ──
    subgraph Pipeline["Extraction Pipeline Detail"]
        direction TB
        P1["1. Input File<br/>(PDF / Image / Document)"]
        P2["2. Parser<br/>(Mistral OCR / Layout pdfplumber / etc.)"]
        P3["3. ParserRunResult<br/>(raw_text + structured_preview blocks)"]
        P4["4. Evidence Cleaner<br/>cleaned_items_for_extraction()"]
        P5["5. SourceChunks<br/>(page + block + table chunks with metadata)"]
        P6["6. Optional: LLM Reconstruction<br/>GPT-5-mini repairs top chunks"]
        P7["7. Per-Field Extraction"]
        P7a["For each schema field:<br/>- _candidate_chunks(): rank by token overlap,<br/>  label matching, type pattern presence<br/>- _extract_raw_value(): labeled value, regex<br/>  (EMAIL/PHONE/DATE/etc), best text line,<br/>  table rows, list bullets<br/>- _coerce_value(): convert to correct type<br/>  (str, int, float, date, bool, list, dict)"]
        P8["8. Dynamic Pydantic Model<br/>create_model() from schema → validate"]
        P9["9. ExtractionRunResponse<br/>{ data: {...}, fields: [...],<br/>  chunks: [...], validation_errors, stats }"]
        P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7 --> P7a --> P8 --> P9
    end
```

## Input → Output Alignment

| Level                | Mechanism                                                                                                | Location                        |
| -------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------- |
| **Structure**  | Schema field keys become output dict keys (1:1 mapping).`data[field.key] = result.value`               | `extraction_lab.py:182`       |
| **Types**      | `_coerce_value()` converts raw strings to the field's declared type                                    | `extraction_lab.py:944-971`   |
| **Validation** | Dynamic Pydantic model is built from the same schema fields;`model_validate()` rejects type mismatches | `extraction_lab.py:1036-1057` |

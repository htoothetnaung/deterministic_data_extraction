import sys
sys.path.append(r'c:\Users\austin\Documents\Brillar_job\genai\data_extraction\backend')
import asyncio
from app.db.engine import get_factory, create_engine
from app.models.extraction_lab import ExtractionRunRequest, ExtractionLabSchema
from app.services.extraction_lab import run_extraction_db

async def main():
    create_engine()
    payload = ExtractionRunRequest(
        input_id="upload:MaybankIB 2016_Rationale_FINAL.pdf",
        output_schema=ExtractionLabSchema(
            name="test_schema",
            fields=[
                {"key": "analysts", "label": "Analysts", "type": "text", "required": False}
            ]
        ),
        natural_language_query=None,
        parser_id="mistral_ocr",
        chunking_strategy="page",
        chunk_size=500,
        chunk_overlap=80,
        max_pages=50,
        max_candidates_per_field=5,
        preview_chars=8000,
        extraction_tier="agentic"
    )
    async with get_factory()() as session:
        response = await run_extraction_db(session, payload)
        print("Response data:", response.data)
        print("Response fields:", response.fields)

if __name__ == '__main__':
    asyncio.run(main())

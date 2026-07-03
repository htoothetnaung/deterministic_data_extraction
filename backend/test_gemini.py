import sys
sys.path.append(r'c:\Users\austin\Documents\Brillar_job\genai\data_extraction\backend')
import json
import traceback
from app.core.config import runtime_env_value
from google import genai

def test():
    api_key = runtime_env_value('GOOGLE_API_KEY') or runtime_env_value('GEMINI_API_KEY')
    print("API Key loaded:", bool(api_key))
    client = genai.Client(api_key=api_key)
    prompt = {
        'field_path': 'test',
        'field_schema': {'type': 'string'},
        'evidence': [{'evidence_id': '1', 'source_type': 'text', 'text': 'The analyst is Liang Huey Jean.'}],
        'instructions': 'Return strict JSON: {"value": any, "confidence": number, "evidence_ids": [string]}. Use null when unsupported.'
    }
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=json.dumps(prompt),
        )
        print("Response text:", response.text)
    except Exception as e:
        print("Error details:")
        traceback.print_exc()

if __name__ == '__main__':
    test()

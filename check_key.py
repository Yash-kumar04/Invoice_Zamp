import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
for m in (os.environ["GEMINI_MODEL"], os.environ["GEMINI_FALLBACK_MODEL"]):
    try:
        r = client.models.generate_content(model=m, contents="Reply with the word OK")
        print(m, "->", r.text)
    except Exception as e:
        print(m, "FAILED:", e)
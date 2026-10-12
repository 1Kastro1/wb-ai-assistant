"""Smoke-test the private Ollama service from inside the backend container."""

import json
import urllib.request


payload = json.dumps({
    "model": "qwen3:4b",
    "prompt": "Ответь одним словом: готово.",
    "stream": False,
    "options": {"num_predict": 12},
}).encode("utf-8")
request = urllib.request.Request(
    "http://ollama:11434/api/generate",
    data=payload,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=180) as response:
    result = json.load(response)
print(json.dumps({
    "model": result.get("model"),
    "done": result.get("done"),
    "response": result.get("response"),
}, ensure_ascii=False))

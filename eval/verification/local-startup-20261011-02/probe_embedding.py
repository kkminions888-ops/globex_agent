"""用单条短文本核对模型可调用性与向量维度，不输出凭据或向量。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from dotenv import dotenv_values


root = Path(__file__).resolve().parents[3]
key = dotenv_values(root / ".env").get("LLM_API_KEY")
if not key:
    print(json.dumps({"configured": False}))
    raise SystemExit(2)

model = "BAAI/bge-m3"
url = "https://api.siliconflow.cn/v1/embeddings"
try:
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "input": "轻便背包"},
        )
except httpx.HTTPError as exc:
    print(json.dumps({"model": model, "transport_error": type(exc).__name__}))
    raise SystemExit(1) from None

result = {
    "endpoint": url,
    "model": model,
    "status_code": response.status_code,
    "content_type": response.headers.get("content-type", "").split(";")[0],
    "redirect_host": httpx.URL(response.headers["location"]).host
    if "location" in response.headers
    else None,
}
try:
    payload = response.json()
except ValueError:
    payload = None
if response.status_code == 200 and isinstance(payload, dict):
    data = payload.get("data")
    if isinstance(data, list) and data:
        vector = data[0].get("embedding")
        result["vector_dimension"] = len(vector) if isinstance(vector, list) else None
elif isinstance(payload, dict):
    error = payload.get("error")
    if isinstance(error, dict):
        result["error_code"] = error.get("code")
print(json.dumps(result, ensure_ascii=False, indent=2))

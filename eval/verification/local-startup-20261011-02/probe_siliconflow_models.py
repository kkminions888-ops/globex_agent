"""只读取已配置凭据，检查 SiliconFlow 官方 API 的模型目录。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from dotenv import dotenv_values


root = Path(__file__).resolve().parents[3]
key = dotenv_values(root / ".env").get("LLM_API_KEY")
if not key:
    print(json.dumps({"configured": False}, ensure_ascii=False))
    raise SystemExit(2)

url = "https://api.siliconflow.cn/v1/models"
try:
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get(url, headers={"Authorization": f"Bearer {key}"})
except httpx.HTTPError as exc:
    print(json.dumps({"configured": True, "transport_error": type(exc).__name__}))
    raise SystemExit(1) from None

result = {
    "configured": True,
    "endpoint": url,
    "status_code": response.status_code,
    "content_type": response.headers.get("content-type", "").split(";")[0],
    "redirect_host": httpx.URL(response.headers["location"]).host
    if "location" in response.headers
    else None,
}
if response.status_code == 200:
    try:
        payload = response.json()
    except ValueError:
        result["valid_json"] = False
    else:
        models = payload.get("data", []) if isinstance(payload, dict) else []
        ids = sorted(m["id"] for m in models if isinstance(m, dict) and isinstance(m.get("id"), str))
        result["model_count"] = len(ids)
        result["configured_llm_listed"] = "qwen3-max" in ids
        result["configured_embedding_listed"] = "text-embedding-v4" in ids
        result["candidate_ids"] = [
            model for model in ids
            if model in {"Qwen/Qwen3-32B", "BAAI/bge-m3", "Qwen/Qwen3-Embedding-8B", "deepseek-ai/DeepSeek-V3.2"}
        ]
        result["first_qwen_ids"] = [model for model in ids if model.startswith("Qwen/Qwen3-")][:12]
        result["first_embedding_ids"] = [
            model for model in ids
            if "Embedding" in model or "bge-m3" in model
        ][:12]
print(json.dumps(result, ensure_ascii=False, indent=2))

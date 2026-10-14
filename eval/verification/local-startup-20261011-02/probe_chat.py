"""最小聊天请求：只记录状态和是否有输出，不保存回复正文。"""

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

model = "Qwen/Qwen3-30B-A3B-Instruct-2507"
url = "https://api.siliconflow.cn/v1/chat/completions"
try:
    with httpx.Client(timeout=45, follow_redirects=False) as client:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": "请仅回复好"}],
                  "stream": False, "max_tokens": 32},
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
    choices = payload.get("choices")
    message = choices[0].get("message", {}) if isinstance(choices, list) and choices else {}
    result["has_content"] = bool(message.get("content"))
    result["has_tool_calls"] = bool(message.get("tool_calls"))
    result["finish_reason"] = choices[0].get("finish_reason") if choices else None
elif isinstance(payload, dict):
    error = payload.get("error")
    if isinstance(error, dict):
        result["error_code"] = error.get("code")
print(json.dumps(result, ensure_ascii=False, indent=2))

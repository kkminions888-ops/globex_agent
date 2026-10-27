"""使用原始 data 和现有环境验证 FastAPI 生命周期，随后正常关闭。"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


async def main() -> None:
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "port": 8001,
              "account": subprocess.check_output(["whoami"], text=True).strip(),
              "administrator_token": bool(ctypes.windll.shell32.IsUserAnAdmin()),
              "external_model_request_submitted": False}
    original_env = (ROOT / ".env").read_bytes()
    original_key = (ROOT / "data/.secrets/model_registry.key").read_bytes()
    server = None
    task = None
    try:
        if result["account"].casefold() != r"izhou\14580" or result["administrator_token"]:
            raise RuntimeError("必须使用普通账户 IZHOU\\14580 验证")
        from app.infrastructure.settings import load_settings
        if Path(load_settings().data_dir).resolve() != ROOT / "data":
            raise RuntimeError("启动目标必须是原始 data 目录")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 8001))
        config = uvicorn.Config("app.presentation.server:app", host="127.0.0.1", port=8001,
                                workers=1, log_level="info")
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        for _ in range(360):
            if server.started or task.done():
                break
            await asyncio.sleep(0.5)
        result["startup_completed"] = server.started
        if not server.started:
            if task.done():
                await task
            raise RuntimeError("后端没有完成启动，详见原始日志")
        async with httpx.AsyncClient() as client:
            response = await client.get("http://127.0.0.1:8001/health", timeout=15)
            health = response.json()
            result.update(http_status=response.status_code, health_status=health.get("status"),
                          database=health.get("database"), trade_database=health.get("trade_database"),
                          redis=health.get("redis"), runtime=health.get("runtime"))
        result["passed"] = response.status_code == 200 and health.get("status") == "ok"
    except Exception as error:
        result.update(passed=False, error_type=type(error).__name__, error=str(error))
    finally:
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=30)
                result["diagnostic_server_stopped"] = True
            except (Exception, asyncio.CancelledError) as error:
                result["shutdown_error_type"] = type(error).__name__
        result["env_bytes_unchanged"] = (ROOT / ".env").read_bytes() == original_env
        result["key_bytes_unchanged"] = (ROOT / "data/.secrets/model_registry.key").read_bytes() == original_key
        output = Path(__file__).with_name("backend-startup-result.json")
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())

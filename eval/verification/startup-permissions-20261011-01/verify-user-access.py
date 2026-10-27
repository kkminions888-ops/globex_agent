"""在真实启动账户下验证权限；不输出密钥、凭据或数据库内容。"""
from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.infrastructure.model_registry import ModelRegistry
from app.infrastructure.settings import load_settings


def main() -> None:
    account = subprocess.check_output(["whoami"], text=True).strip()
    result: dict = {"timestamp": datetime.now(timezone.utc).isoformat(), "account": account,
                    "administrator_token": bool(ctypes.windll.shell32.IsUserAnAdmin())}
    try:
        if account.casefold() != r"izhou\14580":
            raise RuntimeError("验证进程不是实际启动账户 IZHOU\\14580")
        settings = load_settings()
        directory = Path(settings.data_dir).resolve()
        if directory != ROOT / "data":
            raise RuntimeError("验证目标不是原始 data 目录")
        result["data_dir"] = str(directory)
        registry = ModelRegistry(settings)
        cipher = registry._load_cipher()
        result["key_load_passed"] = True
        database = directory / "models.sqlite3"
        with sqlite3.connect(database.as_uri() + "?mode=rw", uri=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result["database_write_transaction_passed"] = True
            rows = connection.execute("SELECT ciphertext FROM model_secrets").fetchall()
            for (ciphertext,) in rows:
                cipher.decrypt(ciphertext)
            result["stored_credentials_decryptable"] = True
            result["stored_credential_count"] = len(rows)
            connection.rollback()
        # SQLite 的 WAL 等运行文件需要父目录写权限；创建后立即删除本次临时文件。
        with tempfile.NamedTemporaryFile(prefix="permission-probe-", suffix=".tmp", dir=directory):
            result["data_directory_temporary_write_passed"] = True
        result["passed"] = True
    except Exception as error:
        result.update(passed=False, error_type=type(error).__name__, error=str(error))
    output = Path(__file__).with_name("user-access-result.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""读取固定目录内的检索评测证据；不访问业务数据、不执行评测。"""
from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any


REPORT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_REPORTS = 64


class EvaluationReadError(ValueError):
    """消息只使用固定说明，避免原始报告或本机路径进入 HTTP 响应。"""


def valid_report_id(value: str) -> bool:
    return bool(REPORT_ID_PATTERN.fullmatch(value))


class RetrievalEvaluationStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.report_root = self.root / "eval" / "verification" / "retrieval-standard"

    @staticmethod
    def _linked(path: Path) -> bool:
        """Windows junction 同样属于重解析点，不能借此绕过固定目录。"""
        if path.is_symlink():
            return True
        if path.exists():
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        return False

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            relative = path.relative_to(self.root)
            cursor = self.root
            for part in relative.parts:
                cursor = cursor / part
                if self._linked(cursor):
                    raise EvaluationReadError("拒绝读取链接文件或目录")
            if not path.resolve().is_relative_to(self.root):
                raise EvaluationReadError("评测文件超出允许目录")
            if not path.is_file():
                raise EvaluationReadError("评测文件不存在")
            if path.stat().st_size > MAX_JSON_BYTES:
                raise EvaluationReadError("评测文件超过大小限制")
            with path.open("rb") as stream:
                content = stream.read(MAX_JSON_BYTES + 1)
            if len(content) > MAX_JSON_BYTES:
                raise EvaluationReadError("评测文件超过大小限制")
            raw = json.loads(content.decode("utf-8-sig"))
            if not isinstance(raw, dict):
                raise EvaluationReadError("评测文件必须为 JSON 对象")
            return raw
        except EvaluationReadError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as error:
            raise EvaluationReadError("评测文件无法读取或格式无效") from error

    def standard(self) -> dict[str, Any]:
        return self._read(self.root / "eval" / "retrieval" / "standard-v1.json")

    def baseline_definition(self) -> dict[str, Any]:
        return self._read(self.root / "eval" / "retrieval" / "baseline-v1.json")

    def reports(self) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
        reports: list[tuple[str, dict[str, Any]]] = []
        warnings: list[str] = []
        try:
            # 先检查固定根目录本身；即使目录中尚无报告也拒绝路径逃逸。
            cursor = self.root
            for part in self.report_root.relative_to(self.root).parts:
                cursor = cursor / part
                if self._linked(cursor):
                    return [], ["评测报告目录是链接，已拒绝读取"]
            if not self.report_root.exists():
                return [], []
            if not self.report_root.resolve().is_relative_to(self.root):
                return [], ["评测报告目录超出允许范围"]
            # 限制扫描数量，避免把评测界面变成无界磁盘扫描入口。
            entries: list[Path] = []
            for entry in self.report_root.iterdir():
                if len(entries) >= MAX_REPORTS:
                    warnings.append(f"评测目录扫描上限为 {MAX_REPORTS}，请归档多余目录")
                    break
                entries.append(entry)
            for directory in sorted(entries, key=lambda item: item.name, reverse=True):
                if not valid_report_id(directory.name):
                    warnings.append("已忽略名称不合规的评测目录")
                    continue
                if self._linked(directory):
                    warnings.append(f"报告 {directory.name} 是链接，已拒绝读取")
                    continue
                if not directory.is_dir():
                    continue
                try:
                    reports.append((directory.name, self._read(directory / "report.json")))
                except EvaluationReadError as error:
                    warnings.append(f"报告 {directory.name}：{error}")
        except OSError:
            warnings.append("评测报告目录无法读取")
        return reports, warnings

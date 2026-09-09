# -*- coding: utf-8 -*-
"""发布形态与评测命令的回归契约。"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_category_recall_help_is_renderable() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/eval/run_category_recall.py", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "品类知识库召回评测" in result.stdout


def test_docker_image_packages_catalog_outside_mutable_data_volume() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY data/catalog-v1.jsonl ./catalog/catalog-v1.jsonl" in dockerfile


def test_compose_passes_reranker_configuration_to_app_and_worker() -> None:
    compose = (PROJECT_ROOT / "docker/docker-compose.yaml").read_text(encoding="utf-8")

    assert compose.count("RERANKER_BASE_URL: ${RERANKER_BASE_URL-}") == 2
    assert compose.count("RERANKER_MODEL: ${RERANKER_MODEL-}") == 2


def test_qdrant_server_matches_locked_client_minor_version() -> None:
    lock = tomllib.loads((PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    client = next(package for package in lock["package"] if package["name"] == "qdrant-client")
    compose = (PROJECT_ROOT / "docker/docker-compose.yaml").read_text(encoding="utf-8")
    match = re.search(r"image: qdrant/qdrant:v(\d+\.\d+)\.\d+", compose)

    assert match is not None
    assert match.group(1) == ".".join(client["version"].split(".")[:2])

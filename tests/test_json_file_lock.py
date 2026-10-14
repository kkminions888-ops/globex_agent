"""偏好文件锁的跨进程互斥测试。"""

import subprocess
import sys
from pathlib import Path

import pytest

from app.infrastructure.persistence.json_file_stores import JsonFilePreferenceStore


def test_preference_lock_blocks_another_process(tmp_path: Path) -> None:
    store = JsonFilePreferenceStore(tmp_path)
    child_code = """
import sys
from pathlib import Path
from app.infrastructure.persistence.json_file_stores import JsonFilePreferenceStore

store = JsonFilePreferenceStore(Path(sys.argv[1]))
print("ready", flush=True)
with store._lock("buyer"):
    print("acquired", flush=True)
"""
    child = None
    try:
        with store._lock("buyer"):
            child = subprocess.Popen(
                [sys.executable, "-u", "-c", child_code, str(tmp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=0.2)

        output, error = child.communicate(timeout=5)
        assert child.returncode == 0, error
        assert output.strip() == "acquired"
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate()

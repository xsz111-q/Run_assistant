# -*- coding: utf-8 -*-
"""引擎运行态读写

每个引擎（Streamlit 进程）把自己任务的进度写到
  logs/runtime/{engine}/{task_id}.json
Flask 只读这些文件，从而与引擎进程彻底解耦。
"""

import json
import os
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(_ROOT))
import config

ENGINES = ("insight", "query", "media")


def runtime_root() -> Path:
    return _ROOT / config.RUNTIME_DIR


def status_path(engine: str, task_id: str) -> Path:
    return runtime_root() / engine / f"{task_id}.json"


def read_status(engine: str, task_id: str) -> dict:
    p = status_path(engine, task_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_status(engine: str, task_id: str, **fields) -> dict:
    """写入（合并）状态字段，原子替换避免读到半个 JSON"""
    p = status_path(engine, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)

    data = read_status(engine, task_id)
    data.setdefault("engine", engine)
    data.setdefault("task_id", task_id)
    data.update(fields)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")

    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return data


def append_phase(engine: str, task_id: str, text: str) -> dict:
    """追加一条阶段性输出（子引擎页面实时展示用）"""
    p = status_path(engine, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)

    data = read_status(engine, task_id)
    data.setdefault("engine", engine)
    data.setdefault("task_id", task_id)
    phases = data.setdefault("phases", [])
    phases.append({"ts": datetime.now().isoformat(timespec="seconds"), "text": text})
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")

    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return data


def clear_status(engine: str, task_id: str) -> None:
    p = status_path(engine, task_id)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass

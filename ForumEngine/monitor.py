# -*- coding: utf-8 -*-
"""论坛日志（JSON Lines）读写

三个 Streamlit 引擎进程各自追加发言，Flask 用 tail 方式增量读取并推到前端。
一条发言的结构：
    ts / agent / agent_name / round / kind / content / report_path / task_id
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import config

AGENT_NAMES = {
    "insight": "运动科学家",
    "query": "理论专家",
    "media": "实用情报专家",
    "host": "总教练 HOST",
}


def forum_log_path() -> Path:
    p = _ROOT / config.FORUM_LOG
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def ensure_forum() -> Path:
    """确保日志文件存在"""
    p = forum_log_path()
    if not p.exists():
        p.touch()
    return p


def append_post(agent: str, content: str, task_id: str = "", kind: str = "report",
                round_no: int = 1, report_path: str = "", agent_name: str = "") -> dict:
    """追加一条论坛发言（跨进程安全：单行追加 + 失败重试）"""
    post = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "agent": agent,
        "agent_name": agent_name or AGENT_NAMES.get(agent, agent),
        "round": round_no,
        "kind": kind,
        "content": content,
        "report_path": str(report_path or ""),
        "task_id": task_id or "",
    }
    line = json.dumps(post, ensure_ascii=False) + "\n"

    p = ensure_forum()
    last_err = None
    for _ in range(20):
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
            return post
        except PermissionError as e:      # Windows 下偶发占用，重试
            last_err = e
            time.sleep(0.1)
    raise last_err


def read_posts() -> list:
    p = forum_log_path()
    if not p.exists():
        return []
    posts = []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                posts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return posts


def read_from_offset(offset: int) -> tuple:
    """从字节偏移读取新增发言，返回 (posts, 新偏移)"""
    p = ensure_forum()
    size = p.stat().st_size
    if offset > size:          # 文件被清空/重建
        offset = 0
    if offset == size:
        return [], size

    with open(p, "rb") as f:
        f.seek(offset)
        chunk = f.read()
        new_offset = f.tell()

    text = chunk.decode("utf-8", errors="replace")
    # 只处理完整行，最后不完整的半行留待下次
    if not text.endswith("\n"):
        cut = text.rfind("\n")
        if cut == -1:
            return [], offset
        text = text[:cut + 1]
        new_offset = offset + len(text.encode("utf-8"))

    posts = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            posts.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return posts, new_offset

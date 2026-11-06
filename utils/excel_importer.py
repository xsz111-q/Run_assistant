# -*- coding: utf-8 -*-
"""Excel 训练记录导入

表格格式（8 个核心列，其余列如“心率记录”“运动轨迹”直接忽略）：
  运动类型 | 运动时长(秒) | 开始时间 | 结束时间 | 卡路里 | 运动距离(米) | 平均心率 | 最大心率

两种来源：
  - 页面上传的临时 .xlsx
  - data/ 固定目录下的所有 .xlsx
判重键：(开始时间, 运动类型, 运动时长, 运动距离) —— 重复行直接跳过。
"""

import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook

_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(_ROOT))
import config
from models.training_record import SessionLocal, TrainingRecordExcel, ensure_table


# ---------------- 表头别名 ----------------
HEADER_ALIASES = {
    "exercise_type": ["运动类型", "类型", "运动项目", "exercise_type", "sport_type"],
    "duration_seconds": ["运动时长(秒)", "时长(秒)", "运动时长", "时长",
                         "duration_seconds", "duration"],
    "start_time": ["开始时间", "开始日期", "start_time", "start"],
    "end_time": ["结束时间", "结束日期", "end_time", "end"],
    "calories": ["卡路里", "消耗卡路里", "热量", "calories", "kcal"],
    "distance_meters": ["运动距离(米)", "距离(米)", "运动距离", "距离",
                        "distance_meters", "distance"],
    "avg_heart_rate": ["平均心率", "avg_heart_rate", "平均心率(bpm)"],
    "max_heart_rate": ["最大心率", "max_heart_rate", "最大心率(bpm)"],
}


def _norm(value) -> str:
    """表头归一化：去空格、全角括号转半角、去单位后缀、小写"""
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = s.replace("（", "(").replace("）", ")").replace("：", ":")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\([a-z]+\)$", "", s)   # 去掉 (bpm) 之类的单位后缀
    return s


_HEADER_LOOKUP = {}
for _field, _aliases in HEADER_ALIASES.items():
    for _a in _aliases:
        _HEADER_LOOKUP[_norm(_a)] = _field


def _parse_datetime(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Excel 序列日期
        return datetime(1899, 12, 30) + timedelta(days=float(value))
    s = str(value).strip().replace("T", " ")
    s = s.split("+")[0].strip()          # 去掉时区
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    s = re.sub(r"[^\d.\-]", "", s)
    if not s or s in ("-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(value):
    n = _parse_number(value)
    return int(round(n)) if n is not None else None


def _iter_sheet_rows(path: Path):
    """读取第一个工作表，返回 (记录列表, 错误列表)"""
    records, errors = [], []
    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)

        header = None
        col_map = {}
        for row in rows:
            if row is None:
                continue
            if all(v is None or str(v).strip() == "" for v in row):
                continue
            if header is None:
                header = [_norm(v) for v in row]
                for idx, name in enumerate(header):
                    field = _HEADER_LOOKUP.get(name)
                    if field and field not in col_map:
                        col_map[field] = idx
                missing = [f for f in ("exercise_type", "start_time", "end_time")
                           if f not in col_map]
                if missing:
                    errors.append(f"{path.name}: 缺少必要列 {missing}，已跳过该文件")
                    return [], errors
                continue

            def cell(field):
                idx = col_map.get(field)
                return row[idx] if idx is not None and idx < len(row) else None

            start = _parse_datetime(cell("start_time"))
            end = _parse_datetime(cell("end_time"))
            exercise_type = cell("exercise_type")
            exercise_type = str(exercise_type).strip() if exercise_type is not None else ""
            if not exercise_type and start is None and end is None:
                continue
            if not exercise_type:
                errors.append(f"{path.name}: 有一行缺少「运动类型」，已跳过")
                continue
            if start is None or end is None:
                errors.append(f"{path.name}: 「{exercise_type}」时间无法解析，已跳过")
                continue

            duration = _parse_int(cell("duration_seconds"))
            if duration is None or duration <= 0:
                duration = int((end - start).total_seconds())
            if duration <= 0:
                errors.append(f"{path.name}: 「{exercise_type}」时长为 0，已跳过")
                continue

            records.append({
                "exercise_type": exercise_type,
                "duration_seconds": duration,
                "start_time": start,
                "end_time": end,
                "calories": _parse_int(cell("calories")),
                "distance_meters": _parse_number(cell("distance_meters")),
                "avg_heart_rate": _parse_int(cell("avg_heart_rate")),
                "max_heart_rate": _parse_int(cell("max_heart_rate")),
            })
    finally:
        wb.close()
    return records, errors


def _dedupe_key(start_time, exercise_type, duration_seconds, distance_meters):
    dist = round(float(distance_meters), 2) if distance_meters is not None else None
    return (start_time, exercise_type, int(duration_seconds), dist)


def import_files(paths) -> dict:
    """导入若干 Excel 文件，返回统计信息"""
    ensure_table()
    session = SessionLocal()
    added = skipped = 0
    errors = []
    files = []

    try:
        existing = {
            _dedupe_key(r.start_time, r.exercise_type, r.duration_seconds, r.distance_meters)
            for r in session.query(
                TrainingRecordExcel.start_time,
                TrainingRecordExcel.exercise_type,
                TrainingRecordExcel.duration_seconds,
                TrainingRecordExcel.distance_meters,
            )
        }

        now_ts = int(time.time())
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists():
                errors.append(f"{path.name}: 文件不存在")
                continue
            try:
                records, file_errors = _iter_sheet_rows(path)
                errors.extend(file_errors)
            except Exception as e:
                errors.append(f"{path.name}: 解析失败 {type(e).__name__}: {e}")
                continue

            file_added = 0
            for rec in records:
                key = _dedupe_key(rec["start_time"], rec["exercise_type"],
                                  rec["duration_seconds"], rec["distance_meters"])
                if key in existing:
                    skipped += 1
                    continue
                existing.add(key)
                session.add(TrainingRecordExcel(
                    user_id="default_user",
                    add_ts=now_ts,
                    last_modify_ts=now_ts,
                    data_source="excel_import",
                    **rec,
                ))
                added += 1
                file_added += 1
            files.append({"file": path.name, "added": file_added, "total": len(records)})

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return {"added": added, "skipped": skipped, "errors": errors[:30], "files": files}


def import_directory(directory=None) -> dict:
    """扫描固定目录下的所有 .xlsx"""
    directory = Path(directory) if directory else _ROOT / config.EXCEL_DATA_DIR
    if not directory.exists():
        return {"added": 0, "skipped": 0,
                "errors": [f"目录不存在：{directory}"], "files": []}
    paths = sorted([p for p in directory.iterdir()
                    if p.is_file() and p.suffix.lower() == config.EXCEL_ALLOWED_EXT])
    if not paths:
        return {"added": 0, "skipped": 0,
                "errors": [f"目录里没有 {config.EXCEL_ALLOWED_EXT} 文件：{directory}"],
                "files": []}
    return import_files(paths)

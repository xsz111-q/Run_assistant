# -*- coding: utf-8 -*-
"""数据库初始化与热切换测试（不依赖 MySQL）。"""

import pytest
from sqlalchemy import create_engine, inspect, text

from models import training_record
from utils import db_bootstrap


@pytest.fixture(autouse=True)
def restore_global_state():
    """测试后恢复全局会话绑定和状态，避免影响其他用例。"""
    original_bind = training_record.SessionLocal.kw.get("bind")
    original_state = dict(db_bootstrap._state)
    yield
    training_record.SessionLocal.configure(bind=original_bind)
    db_bootstrap._state.update(original_state)


def _sqlite_engine(path):
    return create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False},
    )


def test_refresh_creates_tables_and_switches_session(tmp_path):
    engine = _sqlite_engine(tmp_path / "bootstrap.db")
    try:
        assert db_bootstrap.refresh(engine) is True
        assert db_bootstrap.is_ready() is True
        assert db_bootstrap.last_error() == ""
        assert training_record.SessionLocal.kw.get("bind") is engine

        tables = set(inspect(engine).get_table_names())
        assert {"users", "user_profiles", "training_records_excel"} <= tables

        db = training_record.SessionLocal()
        try:
            assert db.execute(text("SELECT 1")).scalar() == 1
        finally:
            db.close()
    finally:
        engine.dispose()


def test_failed_refresh_preserves_working_binding(tmp_path):
    working = _sqlite_engine(tmp_path / "working.db")
    broken = _sqlite_engine(tmp_path / "missing" / "broken.db")
    try:
        assert db_bootstrap.refresh(working) is True
        assert db_bootstrap.refresh(broken) is False

        assert db_bootstrap.is_ready() is False
        assert "OperationalError" in db_bootstrap.last_error()
        assert training_record.SessionLocal.kw.get("bind") is working

        db = training_record.SessionLocal()
        try:
            assert db.execute(text("SELECT 1")).scalar() == 1
        finally:
            db.close()
    finally:
        broken.dispose()
        working.dispose()

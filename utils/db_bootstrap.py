# -*- coding: utf-8 -*-
"""数据库初始化与热切换（未配置 MySQL 也能启动应用）。

用法：
- 应用启动时调用一次 refresh()，失败不阻断启动，页面据此引导用户去 /setup/；
- 配置页保存数据库配置后再次调用 refresh()，成功即把全局 SessionLocal
  切到新的引擎（sessionmaker.configure 原地换绑，所有旧 import 自动生效），
  无需重启 Flask。

状态用 is_ready() / last_error() 查询；refresh() 绝不抛异常。
"""

import threading

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

import config
from models import training_record

_lock = threading.Lock()
_state = {"ready": False, "error": "尚未初始化"}


def is_ready() -> bool:
    return _state["ready"]


def last_error() -> str:
    return _state["error"]


def _fail(candidate, err, injected: bool) -> bool:
    """记录失败状态；候选引擎由本函数创建时及时释放，不污染旧绑定。"""
    if not injected:
        try:
            candidate.dispose()
        except Exception:
            pass
    _state.update(ready=False, error=f"{type(err).__name__}: {err}")
    return False


def _is_unknown_database(err: Exception) -> bool:
    msg = str(err)
    return "1049" in msg or "Unknown database" in msg


def _ensure_database_exists():
    """MySQL 报未知数据库(1049)时，连到不带库名的服务器上建库。"""
    url = URL.create(
        "mysql+pymysql",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host=config.DB_HOST,
        port=int(config.DB_PORT),
        query={"charset": config.DB_CHARSET},
    )
    server_engine = create_engine(url, pool_pre_ping=True)
    try:
        with server_engine.begin() as conn:
            conn.execute(text(
                f"CREATE DATABASE IF NOT EXISTS `{config.DB_NAME}` CHARACTER SET utf8mb4"))
    finally:
        server_engine.dispose()


def _create_tables(candidate):
    training_record.ensure_table(candidate)
    # 惰性 import，避免 models 之间的循环依赖
    from models.user import ensure_table as ensure_user_table
    from models.user_profile import ensure_table as ensure_profile_table
    ensure_user_table(candidate)
    ensure_profile_table(candidate)


def refresh(engine=None) -> bool:
    """尝试连接数据库并建表；成功才切换全局 SessionLocal 的绑定。

    engine 参数供测试注入（如 SQLite）；正式调用一律不传，按 config 新建。
    """
    with _lock:
        injected = engine is not None
        candidate = engine or training_record.get_engine()

        try:
            with candidate.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:
            if not injected and _is_unknown_database(e):
                try:
                    _ensure_database_exists()
                    with candidate.connect() as conn:
                        conn.execute(text("SELECT 1"))
                except Exception as retry_err:
                    return _fail(candidate, retry_err, injected)
            else:
                return _fail(candidate, e, injected)

        try:
            _create_tables(candidate)
        except Exception as e:
            return _fail(candidate, e, injected)

        training_record.SessionLocal.configure(bind=candidate)
        _state.update(ready=True, error="")
        return True

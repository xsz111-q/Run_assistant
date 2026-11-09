# -*- coding: utf-8 -*-
"""pytest 公共夹具（Issue #3–#6 的自动化验收检查）

测试不依赖 MySQL：用临时 SQLite 建出 users / user_profiles 两张表，
再把 routes.auth 与 utils.persona 里的 SessionLocal 替换为测试会话工厂。
"""

import sys
from pathlib import Path

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import models.training_record as training_record          # noqa: E402
from models.user import User                              # noqa: E402
from models.user_profile import UserProfile               # noqa: E402
from routes import auth as auth_module                    # noqa: E402
from utils import persona as persona_module               # noqa: E402


@pytest.fixture()
def session_factory(tmp_path, monkeypatch):
    """临时 SQLite 会话工厂，替换生产 MySQL 会话。"""
    db_path = (tmp_path / "test.db").as_posix()
    engine = create_engine(f"sqlite:///{db_path}",
                           connect_args={"check_same_thread": False})
    training_record.Base.metadata.create_all(
        bind=engine, tables=[User.__table__, UserProfile.__table__])
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    monkeypatch.setattr(auth_module, "SessionLocal", test_session)
    monkeypatch.setattr(persona_module, "SessionLocal", test_session)
    return test_session


@pytest.fixture()
def client(session_factory):
    """只挂 auth 蓝图的最小 Flask 应用（不含主页守卫，守卫另见 test_auth）。"""
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.secret_key = "test-secret"
    app.register_blueprint(auth_module.auth_bp)

    @app.route("/")                       # 登录成功后 url_for('index') 需要该端点
    def index():
        return "ok"

    with app.test_client() as test_client:
        yield test_client


def register(client, username="runner01", password="secret123"):
    """注册一个用户，返回响应。"""
    return client.post("/auth/register",
                       json={"username": username, "password": password})


def login(client, username="runner01", password="secret123"):
    """登录，返回响应。"""
    return client.post("/auth/login",
                       json={"username": username, "password": password})


def user_id_of(session_factory, username="runner01"):
    """按用户名取 id（档案接口以 str(id) 关联）。"""
    db = session_factory()
    try:
        return db.query(User).filter(User.username == username).first().id
    finally:
        db.close()

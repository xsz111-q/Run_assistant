# -*- coding: utf-8 -*-
"""配置页 API 的路由级回归测试（Iteration 2 变更记录）

只验证接口契约：状态上报、保存后的数据库状态、数字字段校验。
不写真实 config.py，也不连接真实 MySQL。
"""

import pytest
from flask import Flask

from routes import setup as setup_routes


@pytest.fixture()
def client():
    app = Flask(__name__, template_folder=str(setup_routes._ROOT / "templates"))
    app.secret_key = "test-secret"
    app.register_blueprint(setup_routes.setup_bp)
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def saved(monkeypatch):
    """拦截写文件，避免测试改动真实 config.py。"""
    calls = {}
    monkeypatch.setattr(setup_routes, "_write_config", lambda updates: calls.update(updates))
    return calls


def _database(monkeypatch, ready, error=""):
    """固定数据库初始化结果，避免依赖真实 MySQL。"""
    monkeypatch.setattr(setup_routes.db_bootstrap, "refresh", lambda: ready)
    monkeypatch.setattr(setup_routes.db_bootstrap, "is_ready", lambda: ready)
    monkeypatch.setattr(setup_routes.db_bootstrap, "last_error", lambda: error)


def test_get_config_reports_database_state(client, monkeypatch):
    _database(monkeypatch, False, "OperationalError: access denied")

    response = client.get("/setup/api/config")
    body = response.get_json()

    assert response.status_code == 200
    assert set(body["groups"]) == {"llm", "db", "search", "scheduler"}
    assert "DB_NAME" in body["values"]
    assert body["database"] == {"ready": False,
                                "error": "OperationalError: access denied"}


def test_invalid_credentials_keep_setup_page_reachable(client, saved, monkeypatch):
    _database(monkeypatch, False, "OperationalError: access denied")

    response = client.post("/setup/api/config", json={
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "3306",
        "DB_USER": "root",
        "DB_PASSWORD": "wrong-password",
        "DB_NAME": "run",
    })
    body = response.get_json()

    assert response.status_code == 200
    assert body["success"] is True
    assert body["database"]["ready"] is False
    assert "仍不可用" in body["message"]
    assert saved["DB_PORT"] == 3306          # 数字字段已转换
    assert client.get("/setup/").status_code == 200


def test_valid_credentials_mark_database_ready(client, saved, monkeypatch):
    _database(monkeypatch, True)

    response = client.post("/setup/api/config", json={"DB_HOST": "127.0.0.1"})
    body = response.get_json()

    assert body["success"] is True
    assert body["database"]["ready"] is True
    assert "注册" in body["message"] and "登录" in body["message"]


def test_non_numeric_field_is_rejected(client, saved):
    response = client.post("/setup/api/config", json={"DB_PORT": "abc"})

    assert response.status_code == 400
    assert response.get_json()["success"] is False

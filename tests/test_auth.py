# -*- coding: utf-8 -*-
"""Issue #3 验收标准的自动化检查：注册、登录、会话、未登录拦截。"""

from werkzeug.security import check_password_hash

from conftest import login, register, user_id_of
from models.user import User


def test_register_writes_hashed_password(client, session_factory):
    """标准 1：注册写入 users 表，密码哈希存储（非明文）。"""
    resp = register(client)
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    db = session_factory()
    try:
        user = db.query(User).filter(User.username == "runner01").first()
        assert user is not None
        assert user.password_hash != "secret123"
        assert check_password_hash(user.password_hash, "secret123")
    finally:
        db.close()


def test_register_duplicate_username_returns_409(client):
    """标准 2：重复用户名注册返回 409。"""
    register(client)
    resp = register(client)
    assert resp.status_code == 409
    assert resp.get_json()["success"] is False


def test_register_rejects_short_password(client):
    """边界：密码少于 6 位返回 400。"""
    resp = register(client, password="123")
    assert resp.status_code == 400


def test_login_with_wrong_password_returns_401(client):
    """标准 3：错误密码返回 401，且不建立会话。"""
    register(client)
    resp = login(client, password="wrong-password")
    assert resp.status_code == 401
    assert client.get("/auth/api/me").get_json()["logged_in"] is False


def test_login_with_correct_password_creates_session(client):
    """标准 3：正确密码登录后写 session。"""
    register(client)
    resp = login(client)
    assert resp.status_code == 200
    me = client.get("/auth/api/me").get_json()
    assert me["logged_in"] is True
    assert me["username"] == "runner01"


def test_login_updates_last_login_at(client, session_factory):
    """登录会写入 last_login_at。"""
    register(client)
    login(client)
    db = session_factory()
    try:
        user = db.query(User).filter(User.username == "runner01").first()
        assert user.last_login_at is not None
    finally:
        db.close()


def test_anonymous_profile_api_returns_401(client):
    """标准 4（API 分支）：未登录访问档案接口返回 401。"""
    assert client.get("/auth/api/profile").status_code == 401
    assert client.post("/auth/api/profile", json={"age": 30}).status_code == 401


def test_logout_clears_session(client):
    """退出后会话失效。"""
    register(client)
    login(client)
    client.get("/auth/logout")
    assert client.get("/auth/api/me").get_json()["logged_in"] is False

# -*- coding: utf-8 -*-
"""Issue #4 验收标准的自动化检查：档案 8 字段、持久化、越权 403、输入校验。"""

from conftest import login, register, user_id_of
from models.user_profile import UserProfile

FULL_PROFILE = {
    "gender": "男",
    "age": 28,
    "occupation": "软件工程师",
    "height_cm": 178,
    "weight_kg": 72.3,
    "medical_conditions": "哮喘",
    "schedule": "工作日晚 7-9 点，周末上午",
    "goal": "半马破 2",
}


def login_as(client, username="runner01"):
    register(client, username=username)
    login(client, username=username)


def test_profile_keeps_all_eight_fields(client, session_factory):
    """标准 1 与 2：8 个字段写入后可原样读回（刷新仍在）。"""
    login_as(client)
    resp = client.post("/auth/api/profile", json=FULL_PROFILE)
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    reloaded = client.get("/auth/api/profile").get_json()["profile"]
    for field, value in FULL_PROFILE.items():
        expected = value if field not in ("height_cm", "weight_kg") else round(float(value), 1)
        assert reloaded[field] == expected


def test_profile_is_persisted_in_database(client, session_factory):
    """档案确实落到 user_profiles 表，而不只是留在会话里。"""
    login_as(client)
    client.post("/auth/api/profile", json=FULL_PROFILE)

    uid = user_id_of(session_factory)
    db = session_factory()
    try:
        row = db.query(UserProfile).filter(
            UserProfile.user_id == str(uid)).first()
        assert row is not None
        assert row.goal == "半马破 2"
        assert float(row.height_cm) == 178.0
        assert row.add_ts > 0 and row.last_modify_ts > 0
    finally:
        db.close()


def test_editing_another_user_profile_returns_403(client, session_factory):
    """标准 3：只能改自己的档案，改别人的返回 403。"""
    login_as(client)
    uid = user_id_of(session_factory)
    resp = client.post(f"/auth/api/profile/{uid + 1}", json={"age": 40})
    assert resp.status_code == 403


def test_own_profile_route_accepts_own_id(client, session_factory):
    """user_id 等于自己时，带 id 的路由等价于 /auth/api/profile。"""
    login_as(client)
    uid = user_id_of(session_factory)
    assert client.post(f"/auth/api/profile/{uid}",
                       json={"goal": "10 公里破 50"}).status_code == 200


def test_partial_update_keeps_other_fields(client):
    """部分更新语义：只提交目标，其他字段保持原值。"""
    login_as(client)
    client.post("/auth/api/profile", json=FULL_PROFILE)
    client.post("/auth/api/profile", json={"goal": "全马 4 小时"})

    reloaded = client.get("/auth/api/profile").get_json()["profile"]
    assert reloaded["goal"] == "全马 4 小时"
    assert reloaded["age"] == 28
    assert reloaded["occupation"] == "软件工程师"


def test_invalid_age_and_height_rejected(client):
    """输入校验：年龄越界、身高非正数返回 400。"""
    login_as(client)
    assert client.post("/auth/api/profile", json={"age": 200}).status_code == 400
    assert client.post("/auth/api/profile", json={"age": "abc"}).status_code == 400
    assert client.post("/auth/api/profile", json={"height_cm": -1}).status_code == 400


def test_empty_profile_returns_null(client):
    """未填写档案时返回 null，而不是报错。"""
    login_as(client)
    body = client.get("/auth/api/profile").get_json()
    assert body["success"] is True
    assert body["profile"] is None

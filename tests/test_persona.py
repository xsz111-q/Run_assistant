# -*- coding: utf-8 -*-
"""Issue #5 验收标准的自动化检查：画像生成、群体判断、默认降级。"""

import pytest

from models.user_profile import UserProfile
from utils.persona import DEFAULT_PERSONA, _classify_group, load_persona, persona_block


@pytest.mark.parametrize("age,expected", [
    (17, "青少年跑者"),
    (18, "青年跑者"),
    (28, "青年跑者"),
    (39, "青年跑者"),
    (45, "中年跑者"),
    (59, "中年跑者"),
    (65, "老年跑者"),
    (None, "成年业余跑者"),
])
def test_classify_group_by_age(age, expected):
    """规则式群体判断：年龄段边界。"""
    assert _classify_group(age, None) == expected


@pytest.mark.parametrize("medical", ["哮喘", "高血压"])
def test_medical_condition_adds_caution(medical):
    """有特殊疾病时追加谨慎提示。"""
    assert "特殊健康状况" in _classify_group(30, medical)


@pytest.mark.parametrize("medical", ["无", "没有", "否", "none", None, ""])
def test_no_medical_condition_keeps_group_clean(medical):
    """填写「无」等写法时不追加谨慎提示。"""
    assert "特殊健康状况" not in _classify_group(30, medical)


def test_load_persona_without_user_returns_default():
    """标准 3：未登录用户拿到默认画像，不抛异常。"""
    assert load_persona(None) == DEFAULT_PERSONA


def test_load_persona_without_profile_returns_default(session_factory):
    """标准 3：有账号但没填档案时同样降级为默认画像。"""
    assert load_persona(999999) == DEFAULT_PERSONA


def test_load_persona_builds_text_from_profile(session_factory):
    """标准 1：画像文本包含群体、目标、BMI 等字段。"""
    db = session_factory()
    try:
        db.add(UserProfile(user_id="1", gender="男", age=28, occupation="软件工程师",
                           height_cm=178, weight_kg=72.3,
                           medical_conditions="哮喘", schedule="工作日晚 7-9 点",
                           goal="半马破 2", add_ts=1, last_modify_ts=1))
        db.commit()
    finally:
        db.close()

    text = load_persona(1)
    assert text.startswith("【用户画像】")
    assert "青年跑者" in text
    assert "BMI 22.8" in text
    assert "目标：半马破 2" in text
    assert "特殊健康状况" in text


def test_persona_block_wraps_heading():
    """标准 2：报告头部区块带「## 用户画像」标题。"""
    block = persona_block("【用户画像】测试")
    assert "## 用户画像" in block
    assert "【用户画像】测试" in block

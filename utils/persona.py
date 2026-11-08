# -*- coding: utf-8 -*-
"""用户画像生成（Issue #5）

从 user_profiles 表读取档案，规则式判断用户群体，生成注入引擎 prompt 的
画像文本。无档案用户返回默认画像，任何异常都降级为默认画像（不报错）。
"""

from models.training_record import SessionLocal
from models.user_profile import UserProfile

DEFAULT_PERSONA = (
    "【用户画像】用户未填写个人档案，按普通成年业余跑者给出通用性建议；"
    "如建议涉及强度/频率安排，请提示用户先完善档案以获得个性化方案。"
)


def _classify_group(age, medical):
    """规则式群体判断：年龄段 + 健康状况"""
    if age is None:
        group = "成年业余跑者"
    elif age < 18:
        group = "青少年跑者"
    elif age < 40:
        group = "青年跑者"
    elif age < 60:
        group = "中年跑者"
    else:
        group = "老年跑者"

    has_condition = bool(medical) and medical.strip() not in ("无", "没有", "否", "none", "None")
    if has_condition:
        group += "（有特殊健康状况，训练建议需保守谨慎）"
    return group


def load_persona(user_id) -> str:
    """读取用户档案并生成画像文本；无档案或异常时返回默认画像。"""
    if not user_id:
        return DEFAULT_PERSONA
    db = SessionLocal()
    try:
        p = db.query(UserProfile).filter(
            UserProfile.user_id == str(user_id)).first()
        if p is None:
            return DEFAULT_PERSONA

        parts = [f"用户群体：{_classify_group(p.age, p.medical_conditions)}"]
        if p.gender:
            parts.append(f"性别：{p.gender}")
        if p.age:
            parts.append(f"年龄：{p.age} 岁")
        if p.occupation:
            parts.append(f"职业：{p.occupation}")
        if p.height_cm and p.weight_kg:
            h = float(p.height_cm) / 100
            bmi = float(p.weight_kg) / (h * h) if h > 0 else 0
            parts.append(
                f"身高 {float(p.height_cm):.0f}cm / 体重 {float(p.weight_kg):.1f}kg"
                f"（BMI {bmi:.1f}）")
        elif p.height_cm:
            parts.append(f"身高：{float(p.height_cm):.0f}cm")
        elif p.weight_kg:
            parts.append(f"体重：{float(p.weight_kg):.1f}kg")
        if p.medical_conditions:
            parts.append(f"特殊疾病：{p.medical_conditions}")
        if p.schedule:
            parts.append(f"可训练时间：{p.schedule}")
        if p.goal:
            parts.append(f"目标：{p.goal}")

        return ("【用户画像】" + "；".join(parts) +
                "。请在分析中结合以上画像给出个性化、可执行的建议。")
    except Exception as e:
        print(f"[persona] 画像生成失败，使用默认画像: {e}")
        return DEFAULT_PERSONA
    finally:
        db.close()


def persona_block(persona: str) -> str:
    """报告头部拼接用的画像段落（保证报告中确定出现「用户画像」段落）。"""
    return f"## 用户画像\n\n{persona}\n\n"

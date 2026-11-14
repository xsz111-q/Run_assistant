# -*- coding: utf-8 -*-
"""
用户档案 ORM 模型：user_profiles 表（对齐数据库中已存在的表结构）。

对应 Issue #4 验收标准的 8 个字段：
  性别 gender / 年龄 age / 职业 occupation / 身高 height_cm / 体重 weight_kg
  特殊疾病 diseases（API 属性名 medical_conditions）
  个人时间安排 weekly_schedule（API 属性名 schedule）
  目标 goal

表结构约定（与现有数据一致）：
  - 主键 id 自增；user_id 为 VARCHAR(64) 唯一索引，存 users.id 的字符串形式
  - add_ts / last_modify_ts 为 NOT NULL 的 epoch 秒，写入时需手动填
"""

import time

from sqlalchemy import Column, Integer, BigInteger, String, DECIMAL, Text
from models.training_record import Base, get_engine


class UserProfile(Base):
    __tablename__ = 'user_profiles'
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), unique=True, index=True)   # str(users.id)
    gender = Column(String(8))                # 性别
    age = Column(Integer)                     # 年龄
    occupation = Column(String(100))          # 职业
    height_cm = Column(DECIMAL(5, 1))         # 身高(cm)
    weight_kg = Column(DECIMAL(5, 1))         # 体重(kg)
    medical_conditions = Column('diseases', Text)        # 特殊疾病
    schedule = Column('weekly_schedule', Text)           # 个人时间安排
    goal = Column(String(64))                 # 目标
    add_ts = Column(BigInteger, nullable=False, default=lambda: int(time.time()))
    last_modify_ts = Column(BigInteger, nullable=False,
                            default=lambda: int(time.time()),
                            onupdate=lambda: int(time.time()))

    def to_dict(self):
        return {
            'user_id': self.user_id,
            'gender': self.gender,
            'age': self.age,
            'occupation': self.occupation,
            'height_cm': float(self.height_cm) if self.height_cm is not None else None,
            'weight_kg': float(self.weight_kg) if self.weight_kg is not None else None,
            'medical_conditions': self.medical_conditions,
            'schedule': self.schedule,
            'goal': self.goal,
        }


def ensure_table(engine=None):
    """启动时建表（幂等；表已存在则跳过）。可传入候选引擎。"""
    Base.metadata.create_all(engine or get_engine(), tables=[UserProfile.__table__])

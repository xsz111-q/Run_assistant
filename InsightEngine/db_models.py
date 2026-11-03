# -*- coding: utf-8 -*-
"""训练数据库 ORM 模型（与 original_project 保持一致，方便复用）"""

from sqlalchemy import Column, Integer, String, Float, DateTime, BigInteger, Text
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class TrainingRecordKeep(Base):
    __tablename__ = 'training_records_keep'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=False, default='default_user', index=True)
    exercise_type = Column(String(100), nullable=False, index=True)
    duration_seconds = Column(Integer, nullable=False)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False)
    calories = Column(Integer, nullable=True)
    distance_meters = Column(Float, nullable=True)
    avg_heart_rate = Column(Integer, nullable=True, index=True)
    max_heart_rate = Column(Integer, nullable=True)
    heart_rate_data = Column(Text, nullable=True)
    add_ts = Column(BigInteger, nullable=False)
    last_modify_ts = Column(BigInteger, nullable=False)
    data_source = Column(String(100), nullable=False, default='keep_import')

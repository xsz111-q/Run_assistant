# -*- coding: utf-8 -*-
"""
训练记录 ORM 模型（Iter 2）

数据层只保留「本地 Excel 导入」一条路径：
  用户上传 .xlsx 或把 .xlsx 放进 data/ 目录 → 解析 8 个核心列 → 写入 training_records_excel
不再有 Keep / Garmin 数据源。
"""

from sqlalchemy import create_engine, Column, Integer, String, DateTime, BigInteger
from sqlalchemy.dialects.mysql import DECIMAL
from sqlalchemy.orm import declarative_base, sessionmaker

import config

Base = declarative_base()


def get_engine():
    """获取数据库引擎（每次调用读取最新 config）"""
    return create_engine(
        f"mysql+pymysql://{config.DB_USER}:{config.DB_PASSWORD}"
        f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
        f"?charset={config.DB_CHARSET}",
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )


def get_session_local():
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


engine = get_engine()
SessionLocal = get_session_local()


class TrainingRecordExcel(Base):
    """训练记录 —— Excel 导入数据源"""

    __tablename__ = 'training_records_excel'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), default='default_user', index=True)
    exercise_type = Column(String(64), nullable=False, index=True)
    duration_seconds = Column(Integer, nullable=False)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False)
    calories = Column(Integer, nullable=True)
    distance_meters = Column(DECIMAL(10, 2), nullable=True)
    avg_heart_rate = Column(Integer, nullable=True)
    max_heart_rate = Column(Integer, nullable=True)
    add_ts = Column(BigInteger, nullable=False)
    last_modify_ts = Column(BigInteger, nullable=False)
    data_source = Column(String(64), default='excel_import', nullable=False)

    def __repr__(self):
        return (f"<TrainingRecordExcel(id={self.id}, exercise_type={self.exercise_type}, "
                f"start_time={self.start_time})>")

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'exercise_type': self.exercise_type,
            'duration_seconds': self.duration_seconds,
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S') if self.start_time else None,
            'end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S') if self.end_time else None,
            'calories': self.calories,
            'distance_meters': float(self.distance_meters) if self.distance_meters is not None else None,
            'avg_heart_rate': self.avg_heart_rate,
            'max_heart_rate': self.max_heart_rate,
            'add_ts': self.add_ts,
            'last_modify_ts': self.last_modify_ts,
            'data_source': self.data_source,
        }


def ensure_table():
    """按需建表（首次导入前调用）"""
    Base.metadata.create_all(bind=engine, tables=[TrainingRecordExcel.__table__])
    return True


def get_db_session():
    return SessionLocal()

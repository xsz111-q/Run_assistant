# -*- coding: utf-8 -*-
"""用户账号 ORM 模型（Issue #3）

users 表：id / username / password_hash
密码用 werkzeug.security 哈希存储，不存明文。
created_at 用 datetime 兼容已有表结构。
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime

from models.training_record import Base, get_engine


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(16), nullable=False, default='user')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username})>"

    def to_dict(self):
        return {'id': self.id, 'username': self.username, 'role': self.role,
                'created_at': self.created_at.isoformat() if self.created_at else None}


def ensure_table(engine=None):
    """按需建表；可传入候选引擎（db_bootstrap 初始化时使用）"""
    Base.metadata.create_all(bind=engine or get_engine(), tables=[User.__table__])
    return True

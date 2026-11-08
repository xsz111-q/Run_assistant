# -*- coding: utf-8 -*-
"""注册 / 登录 / 退出（Issue #3）

验收标准：
  1. 注册写入 users 表，密码哈希存储（werkzeug pbkdf2，非明文）
  2. 重复用户名注册返回 409
  3. 正确密码登录写 session，错误密码返回 401
  4. 未登录访问受保护页面重定向到 /auth/login（API 返回 401）
"""

import functools
from datetime import datetime

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   session, url_for)
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from models.training_record import SessionLocal
from models.user import User

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# 无需登录即可访问的端点（endpoint 名）
PUBLIC_ENDPOINTS = {
    'auth.login', 'auth.register', 'static',
}


def login_required(view):
    """页面守卫：未登录重定向到登录页"""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login', next=request.path))
        return view(*args, **kwargs)
    return wrapper


def current_user():
    """返回当前登录用户的 User 对象，未登录返回 None"""
    uid = session.get('user_id')
    if not uid:
        return None
    db = SessionLocal()
    try:
        return db.query(User).filter(User.id == uid).first()
    finally:
        db.close()


# ---------------- 注册 ----------------
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('auth/register.html')

    data = request.get_json(force=True) if request.is_json else request.form
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'}), 400
    if len(username) > 64 or len(password) < 6:
        return jsonify({'success': False, 'message': '用户名过长或密码少于 6 位'}), 400

    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == username).first():
            return jsonify({'success': False, 'message': '用户名已存在'}), 409

        user = User(username=username,
                    password_hash=generate_password_hash(password),
                    created_at=datetime.utcnow())
        db.add(user)
        db.commit()
        return jsonify({'success': True, 'message': '注册成功', 'username': username})
    except IntegrityError:          # 并发下唯一索引兜底
        db.rollback()
        return jsonify({'success': False, 'message': '用户名已存在'}), 409
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'注册失败: {e}'}), 500
    finally:
        db.close()


# ---------------- 登录 ----------------
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('auth/login.html')

    data = request.get_json(force=True) if request.is_json else request.form
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or not check_password_hash(user.password_hash, password):
            return jsonify({'success': False, 'message': '用户名或密码错误'}), 401

        user.last_login_at = datetime.utcnow()
        db.commit()

        session.clear()
        session['user_id'] = user.id
        session['username'] = user.username
        return jsonify({'success': True, 'message': '登录成功',
                        'redirect': url_for('index')})
    finally:
        db.close()


# ---------------- 退出 ----------------
@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


# ---------------- 当前登录态（前端导航用） ----------------
@auth_bp.route('/api/me')
def api_me():
    if 'user_id' in session:
        return jsonify({'logged_in': True, 'username': session.get('username')})
    return jsonify({'logged_in': False})


# ---------------- 用户档案（Issue #4） ----------------
from models.user_profile import UserProfile

_PROFILE_FIELDS = ('gender', 'age', 'occupation', 'height_cm', 'weight_kg',
                   'medical_conditions', 'schedule', 'goal')


def _get_own_profile(db, uid):
    return db.query(UserProfile).filter(UserProfile.user_id == str(uid)).first()


def _parse_profile_payload(data):
    """解析并校验档案字段，返回 (values_dict, error_message)。

    只处理请求中实际出现的字段——未提交的字段保持原值（部分更新语义），
    显式提交空值的字段才被置为 None。
    """
    values = {}
    for field in _PROFILE_FIELDS:
        if field not in data:
            continue
        raw = data.get(field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            values[field] = None
            continue
        if field == 'age':
            try:
                age = int(raw)
                if not (1 <= age <= 120):
                    return None, '年龄需在 1-120 之间'
                values[field] = age
            except (TypeError, ValueError):
                return None, '年龄必须是数字'
        elif field in ('height_cm', 'weight_kg'):
            try:
                num = float(raw)
                if num <= 0:
                    return None, '身高/体重必须为正数'
                values[field] = round(num, 1)
            except (TypeError, ValueError):
                return None, '身高/体重必须是数字'
        else:
            values[field] = str(raw).strip()[:64 if field == 'goal' else 255]
    return values, None


@auth_bp.route('/profile', methods=['GET'])
def profile_page():
    """档案填写/查看页（需登录，由 app.py 守卫拦截）。"""
    uid = session.get('user_id')
    db = SessionLocal()
    try:
        profile = _get_own_profile(db, uid)
        return render_template('auth/profile.html',
                               profile=profile.to_dict() if profile else {})
    finally:
        db.close()


@auth_bp.route('/api/profile', methods=['GET', 'POST'])
def api_profile():
    """读/写自己的档案。未登录 401（保险丝，正常已被守卫拦截）。"""
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'message': '未登录'}), 401

    db = SessionLocal()
    try:
        if request.method == 'GET':
            profile = _get_own_profile(db, uid)
            return jsonify({'success': True,
                            'profile': profile.to_dict() if profile else None})

        data = request.get_json(force=True) if request.is_json else request.form
        values, err = _parse_profile_payload(data)
        if err:
            return jsonify({'success': False, 'message': err}), 400

        profile = _get_own_profile(db, uid)
        if profile is None:
            profile = UserProfile(user_id=str(uid), **values)
            db.add(profile)
        else:
            for field, val in values.items():
                setattr(profile, field, val)
        db.commit()
        return jsonify({'success': True, 'message': '档案已保存',
                        'profile': profile.to_dict()})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'保存失败: {e}'}), 500
    finally:
        db.close()


@auth_bp.route('/api/profile/<int:user_id>', methods=['GET', 'POST', 'PUT'])
def api_profile_other(user_id):
    """访问/修改他人档案的防护路由：一律 403（验收标准 3）。"""
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'message': '未登录'}), 401
    if user_id != uid:
        return jsonify({'success': False, 'message': '无权修改他人档案'}), 403
    # user_id == uid 时与 /api/profile 等价，直接复用
    return api_profile()


"""
鉴权模块 — JWT 认证 + Flask Session 双重鉴权
- 页面访问: 基于 Flask session (cookie)，未登录重定向到 /login
- API 访问: 基于 JWT token，未认证返回 401 JSON
"""

import os
import jwt
import time
from functools import wraps
from flask import request, jsonify, g, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User

JWT_SECRET = os.environ.get("JWT_SECRET", "astock-signal-system-2026-secret")
JWT_EXPIRE = 7 * 24 * 3600  # 7天


def generate_token(user_id, username):
    """生成 JWT token"""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": int(time.time()) + JWT_EXPIRE,
        "iat": int(time.time()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return token


def verify_token(token):
    """验证 JWT token，返回 user_id 或 None"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("user_id"), payload.get("username")
    except jwt.ExpiredSignatureError:
        return None, "token_expired"
    except jwt.InvalidTokenError:
        return None, "invalid_token"


def token_required(f):
    """装饰器：保护需要登录的路由"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "未提供认证token"}), 401

        token = auth_header[7:]
        user_id, msg = verify_token(token)

        if user_id is None:
            if msg == "token_expired":
                return jsonify({"error": "token已过期，请重新登录"}), 401
            return jsonify({"error": "无效的token"}), 401

        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "用户不存在"}), 401

        g.current_user = user
        g.token = token
        return f(*args, **kwargs)

    return decorated


def set_session(user_id, username):
    """设置 Flask session（用于页面访问鉴权）"""
    session["user_id"] = user_id
    session["username"] = username
    session.permanent = True


def clear_session():
    """清除 Flask session"""
    session.clear()


def page_login_required(f):
    """装饰器：页面路由鉴权，未登录重定向到 /login"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def register_user(username, password):
    """注册新用户"""
    if not username or not password:
        return {"error": "用户名和密码不能为空"}, 400
    if len(username) < 2 or len(username) > 20:
        return {"error": "用户名长度2-20个字符"}, 400
    if len(password) < 6:
        return {"error": "密码至少6个字符"}, 400

    existing = User.query.filter_by(username=username).first()
    if existing:
        return {"error": "用户名已存在"}, 409

    user = User(username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    token = generate_token(user.id, user.username)
    set_session(user.id, user.username)
    return {
        "message": "注册成功",
        "user": user.to_dict(),
        "token": token,
    }, 201


def login_user(username, password):
    """用户登录"""
    if not username or not password:
        return {"error": "用户名和密码不能为空"}, 400

    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return {"error": "用户名或密码错误"}, 401

    token = generate_token(user.id, user.username)
    set_session(user.id, user.username)
    return {
        "message": "登录成功",
        "user": user.to_dict(),
        "token": token,
    }, 200

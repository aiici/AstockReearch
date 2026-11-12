"""
数据库模型 — SQLite + SQLAlchemy
存储股票数据、用户、信号、自选股
"""

import os
from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "stock_system.db")
DB_URI = f"sqlite:///{DB_PATH}"

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    watchlist = db.relationship("Watchlist", backref="user", lazy="dynamic",
                                cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {"id": self.id, "username": self.username,
                "created_at": self.created_at.isoformat() if self.created_at else None}


class Stock(db.Model):
    __tablename__ = "stocks"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    name = db.Column(db.String(50))
    market = db.Column(db.String(4))           # sh / sz / bj
    cap_group = db.Column(db.String(10))        # 大盘/中盘/小盘/指数/ETF/未知
    corr_mean = db.Column(db.Float)             # 20日滚动相关系数均值
    latest_update = db.Column(db.String(10))    # 最新数据日期 YYYY-MM-DD
    latest_price = db.Column(db.Float)
    latest_pct = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    daily_data = db.relationship("DailyData", backref="stock", lazy="dynamic",
                                 cascade="all, delete-orphan")
    signals = db.relationship("Signal", backref="stock", lazy="dynamic",
                              cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "code": self.code, "name": self.name,
            "market": self.market, "cap_group": self.cap_group,
            "corr_mean": round(self.corr_mean, 3) if self.corr_mean else None,
            "latest_update": self.latest_update,
            "latest_price": self.latest_price,
            "latest_pct": round(self.latest_pct, 2) if self.latest_pct else None,
        }


class DailyData(db.Model):
    __tablename__ = "daily_data"
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.id"), nullable=False, index=True)
    date = db.Column(db.String(10), nullable=False, index=True)    # YYYY-MM-DD
    open = db.Column(db.Float)
    high = db.Column(db.Float)
    low = db.Column(db.Float)
    close = db.Column(db.Float)
    volume = db.Column(db.Float)           # 成交量(手)
    pct_change = db.Column(db.Float)       # 日涨跌幅%
    main_net = db.Column(db.Float)         # 主力净流入(元)
    super_net = db.Column(db.Float)        # 超大单净额
    large_net = db.Column(db.Float)        # 大单净额
    mid_net = db.Column(db.Float)          # 中单净额
    small_net = db.Column(db.Float)        # 小单净额
    corr_same_day = db.Column(db.Float)    # 20日滚动相关系数

    __table_args__ = (db.UniqueConstraint("stock_id", "date"),)


class Signal(db.Model):
    __tablename__ = "signals"
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.id"), nullable=False, index=True)
    date = db.Column(db.String(10), nullable=False)               # 触发日期
    signal_type = db.Column(db.String(50), nullable=False)        # 规则名称
    direction = db.Column(db.String(10), nullable=False)          # buy / sell / hold
    rule_name = db.Column(db.String(100))
    win_rate = db.Column(db.Float)                                 # 历史胜率%
    hold_days = db.Column(db.Integer)                             # 建议持有天数
    details = db.Column(db.Text)                                   # JSON 详情
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        import json
        return {
            "id": self.id, "date": self.date,
            "signal_type": self.signal_type,
            "direction": self.direction,
            "rule_name": self.rule_name,
            "win_rate": self.win_rate,
            "hold_days": self.hold_days,
            "details": json.loads(self.details) if self.details else {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Watchlist(db.Model):
    __tablename__ = "watchlist"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    stock = db.relationship("Stock")

    __table_args__ = (db.UniqueConstraint("user_id", "stock_id"),)


def init_db(app):
    """初始化数据库连接并建表"""
    app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()

"""
信号引擎 — 对任意股票应用回测得到的买卖规则
返回当前是否触发信号 + 历史触发统计
"""

import json
import numpy as np
import pandas as pd
from datetime import date
from models import db, Stock, DailyData, Signal


# ═══════════════════════════════════════════════════════════════════════
# 买卖规则定义（来自 signal_finder.py 回测结果）
# ═══════════════════════════════════════════════════════════════════════

BUY_RULES = [
    {
        "id": "buy1",
        "name": "连续3日温和流入",
        "rule_name": "连续3日主力净流入 + 每日<1亿(温和流入)",
        "win_rate": 80.0,
        "hold_days": 3,
        "avg_return": 3.17,
        "profit_factor": 10.17,
        "sample": 10,
    },
    {
        "id": "buy2",
        "name": "连续3日流入+大单强势",
        "rule_name": "连续3日主力净流入 + 大单占比>30%",
        "win_rate": 75.0,
        "hold_days": 3,
        "avg_return": 2.69,
        "profit_factor": 3.07,
        "sample": 24,
    },
    {
        "id": "buy3",
        "name": "连续4日超大单流入",
        "rule_name": "连续4日超大单净流入",
        "win_rate": 76.9,
        "hold_days": 5,
        "avg_return": 4.24,
        "profit_factor": 3.82,
        "sample": 13,
    },
    {
        "id": "buy4",
        "name": "连续3日流入+大单占比>20%",
        "rule_name": "连续3日主力净流入 + 大单占比>20%",
        "win_rate": 71.4,
        "hold_days": 3,
        "avg_return": 1.98,
        "profit_factor": 2.25,
        "sample": 28,
    },
    {
        "id": "buy5",
        "name": "连续4日流入+高相关",
        "rule_name": "连续4日主力净流入 + 相关系数>0.7",
        "win_rate": 85.7,
        "hold_days": 3,
        "avg_return": 6.23,
        "profit_factor": 6.3,
        "sample": 7,
    },
]

SELL_RULES = [
    {
        "id": "sell1",
        "name": "止盈信号",
        "rule_name": "连续3日流入后首日流出",
        "win_rate": 70.0,
        "hold_days": 5,
        "avg_return": 1.77,
        "sample": 30,
    },
]


# ═══════════════════════════════════════════════════════════════════════
# 信号检测函数
# ═══════════════════════════════════════════════════════════════════════

def _is_inflow(row):
    return row.get("main_net") is not None and not np.isnan(row.get("main_net", 0)) and row["main_net"] > 0

def _is_outflow(row):
    return row.get("main_net") is not None and not np.isnan(row.get("main_net", 0)) and row["main_net"] < 0

def _is_super_inflow(row):
    v = row.get("super_net")
    return v is not None and not np.isnan(v) and v > 0

def _get_large_ratio(row):
    s = row.get("super_net") or 0
    l = row.get("large_net") or 0
    m = row.get("mid_net") or 0
    sm = row.get("small_net") or 0
    if not all(v is not None and not np.isnan(v) for v in [s, l, m, sm]):
        return None
    total = abs(s) + abs(l) + abs(m) + abs(sm)
    if total <= 0:
        return 0
    return (s + l) / total * 100


def _check_consecutive_inflow(df, idx, n):
    """检查idx及前n-1天是否连续主力净流入，且之前一天不流入（避免重复触发）"""
    if idx < n - 1:
        return False
    for j in range(n):
        row = df.iloc[idx - j]
        if not _is_inflow(row):
            return False
    # 前一天不应流入（避免重复）
    if idx >= n:
        prev = df.iloc[idx - n]
        if _is_inflow(prev):
            return False
    return True


def _check_consecutive_super_inflow(df, idx, n):
    if idx < n - 1:
        return False
    for j in range(n):
        row = df.iloc[idx - j]
        if not _is_super_inflow(row):
            return False
    if idx >= n:
        prev = df.iloc[idx - n]
        if _is_super_inflow(prev):
            return False
    return True


def _check_stop_profit(df, idx):
    """止盈信号：连续3日流入后首日流出"""
    if idx < 3:
        return False
    if not _is_outflow(df.iloc[idx]):
        return False
    for j in range(1, 4):
        if not _is_inflow(df.iloc[idx - j]):
            return False
    return True


# ═══════════════════════════════════════════════════════════════════════
# 信号引擎主函数
# ═══════════════════════════════════════════════════════════════════════

def analyze_stock(stock_id, save_signals=True):
    """
    对指定股票运行信号引擎。

    返回: {
        "current_signals": [...],     # 当日触发的信号
        "recommendation": "buy/sell/hold",
        "history": [...],             # 最近信号历史
        "stats": {...},               # 统计
        "timeseries": [...],          # K线+资金流数据
    }
    """
    from data_service import get_stock_data

    df = get_stock_data(stock_id, days=120)
    if df.empty or len(df) < 5:
        return {"error": "数据不足", "current_signals": [], "recommendation": "hold"}

    stock = Stock.query.get(stock_id)
    corr_mean = stock.corr_mean if stock.corr_mean else 0

    current_signals = []
    last_idx = len(df) - 1

    # ── 检查买入信号 ──
    for rule in BUY_RULES:
        triggered = False
        detail = {}

        if rule["id"] == "buy1":
            # 连续3日温和流入
            if _check_consecutive_inflow(df, last_idx, 3):
                # 检查每日<1亿
                all_mild = True
                for j in range(3):
                    mn = df.iloc[last_idx - j].get("main_net", 0)
                    if mn is not None and not np.isnan(mn) and mn > 1e8:
                        all_mild = False
                        break
                if all_mild:
                    triggered = True
                    detail = {"daily_flows": [float(df.iloc[last_idx-j]["main_net"]) for j in range(3)]}

        elif rule["id"] == "buy2":
            # 连续3日流入 + 大单占比>30%
            if _check_consecutive_inflow(df, last_idx, 3):
                ratio = _get_large_ratio(df.iloc[last_idx])
                if ratio is not None and ratio > 30:
                    triggered = True
                    detail = {"large_ratio": round(ratio, 1)}

        elif rule["id"] == "buy3":
            # 连续4日超大单净流入
            if _check_consecutive_super_inflow(df, last_idx, 4):
                triggered = True
                detail = {"super_flows": [float(df.iloc[last_idx-j]["super_net"]) for j in range(4)]}

        elif rule["id"] == "buy4":
            # 连续3日流入 + 大单占比>20%
            if _check_consecutive_inflow(df, last_idx, 3):
                ratio = _get_large_ratio(df.iloc[last_idx])
                if ratio is not None and ratio > 20:
                    triggered = True
                    detail = {"large_ratio": round(ratio, 1)}

        elif rule["id"] == "buy5":
            # 连续4日流入 + 相关系数>0.7
            if _check_consecutive_inflow(df, last_idx, 4) and corr_mean > 0.7:
                triggered = True
                detail = {"corr_mean": round(corr_mean, 3)}

        if triggered:
            sig = {
                "rule_id": rule["id"],
                "signal_type": rule["name"],
                "rule_name": rule["rule_name"],
                "direction": "buy",
                "win_rate": rule["win_rate"],
                "hold_days": rule["hold_days"],
                "avg_return": rule["avg_return"],
                "profit_factor": rule["profit_factor"],
                "details": detail,
                "action": f"买入，持有{rule['hold_days']}个交易日",
            }
            current_signals.append(sig)

    # ── 检查卖出信号 ──
    for rule in SELL_RULES:
        triggered = False
        detail = {}

        if rule["id"] == "sell1":
            if _check_stop_profit(df, last_idx):
                triggered = True
                detail = {"reason": "连续3日流入趋势被打破，首日出现净流出"}

        if triggered:
            sig = {
                "rule_id": rule["id"],
                "signal_type": rule["name"],
                "rule_name": rule["rule_name"],
                "direction": "sell",
                "win_rate": rule["win_rate"],
                "hold_days": rule["hold_days"],
                "avg_return": rule["avg_return"],
                "profit_factor": None,
                "details": detail,
                "action": f"卖出/止盈",
            }
            current_signals.append(sig)

    # ── 综合建议 ──
    buy_signals = [s for s in current_signals if s["direction"] == "buy"]
    sell_signals = [s for s in current_signals if s["direction"] == "sell"]

    if sell_signals:
        recommendation = "sell"
    elif buy_signals:
        recommendation = "buy"
    else:
        recommendation = "hold"

    # ── 保存当日信号到数据库 ──
    latest_date = str(df.iloc[-1].get("date", ""))
    if save_signals and current_signals:
        for sig in current_signals:
            existing = Signal.query.filter_by(
                stock_id=stock_id, date=latest_date,
                signal_type=sig["signal_type"]
            ).first()
            if not existing:
                signal = Signal(
                    stock_id=stock_id,
                    date=latest_date,
                    signal_type=sig["signal_type"],
                    direction=sig["direction"],
                    rule_name=sig["rule_name"],
                    win_rate=sig["win_rate"],
                    hold_days=sig["hold_days"],
                    details=json.dumps(sig["details"], ensure_ascii=False),
                )
                db.session.add(signal)
        db.session.commit()

    # ── 历史信号回扫：首次分析时，扫描全部日线的信号触发点 ──
    if save_signals:
        existing_count = Signal.query.filter_by(stock_id=stock_id).count()
        if existing_count == 0:
            _backfill_historical_signals(df, stock_id)

    # ── 历史信号 ──
    history_signals = Signal.query.filter_by(stock_id=stock_id).order_by(
        Signal.date.desc()).limit(20).all()

    # ── 历史回测统计（该股）──
    stats = _compute_stock_stats(df, stock)

    # ── 时间序列（用于前端图表）──
    timeseries = []
    for _, row in df.iterrows():
        timeseries.append({
            "date": str(row.get("date", "")),
            "close": float(row["close"]) if not pd.isna(row.get("close")) else None,
            "main_net": float(row["main_net"]) / 1e8 if not pd.isna(row.get("main_net")) else None,
            "pct_change": float(row["pct_change"]) if not pd.isna(row.get("pct_change")) else None,
            "corr_same_day": float(row["corr_same_day"]) if not pd.isna(row.get("corr_same_day")) else None,
        })

    return {
        "stock": stock.to_dict() if stock else None,
        "current_signals": current_signals,
        "recommendation": recommendation,
        "history": [s.to_dict() for s in history_signals],
        "stats": stats,
        "timeseries": timeseries,
        "latest_price": float(df.iloc[-1]["close"]) if not pd.isna(df.iloc[-1].get("close")) else None,
        "latest_date": latest_date,
        "latest_pct": float(df.iloc[-1]["pct_change"]) if not pd.isna(df.iloc[-1].get("pct_change")) else None,
    }


def _backfill_historical_signals(df, stock_id):
    """回扫历史日线，找出所有信号触发点并保存到数据库"""
    stock = Stock.query.get(stock_id)
    if not stock:
        return
    corr_mean = stock.corr_mean if stock.corr_mean else 0
    saved = 0

    for idx in range(3, len(df)):
        row = df.iloc[idx]
        trade_date = str(row.get("date", ""))
        triggered_signals = []

        # buy1: 连续3日温和流入
        if _check_consecutive_inflow(df, idx, 3):
            all_mild = True
            for j in range(3):
                mn = df.iloc[idx - j].get("main_net", 0)
                if mn is not None and not np.isnan(mn) and mn > 1e8:
                    all_mild = False
                    break
            if all_mild:
                triggered_signals.append(("buy1", "连续3日温和流入",
                    "连续3日主力净流入 + 每日<1亿(温和流入)",
                    80.0, 3, "buy"))

        # buy2: 连续3日流入 + 大单占比>30%
        if _check_consecutive_inflow(df, idx, 3):
            ratio = _get_large_ratio(row)
            if ratio is not None and ratio > 30:
                triggered_signals.append(("buy2", "连续3日流入+大单强势",
                    "连续3日主力净流入 + 大单占比>30%",
                    75.0, 3, "buy"))

        # buy3: 连续4日超大单流入
        if _check_consecutive_super_inflow(df, idx, 4):
            triggered_signals.append(("buy3", "连续4日超大单流入",
                "连续4日超大单净流入",
                76.9, 5, "buy"))

        # buy4: 连续3日流入 + 大单占比>20%
        if _check_consecutive_inflow(df, idx, 3):
            ratio = _get_large_ratio(row)
            if ratio is not None and ratio > 20:
                triggered_signals.append(("buy4", "连续3日流入+大单占比>20%",
                    "连续3日主力净流入 + 大单占比>20%",
                    71.4, 3, "buy"))

        # buy5: 连续4日流入 + 高相关
        if corr_mean > 0.7 and _check_consecutive_inflow(df, idx, 4):
            triggered_signals.append(("buy5", "连续4日流入+高相关",
                "连续4日主力净流入 + 相关系数>0.7",
                85.7, 3, "buy"))

        # sell1: 止盈信号
        if _check_stop_profit(df, idx):
            triggered_signals.append(("sell1", "止盈信号",
                "连续3日流入后首日流出",
                70.0, 5, "sell"))

        for rule_id, name, rule_name, win_rate, hold_days, direction in triggered_signals:
            existing = Signal.query.filter_by(
                stock_id=stock_id, date=trade_date,
                signal_type=name
            ).first()
            if not existing:
                signal = Signal(
                    stock_id=stock_id,
                    date=trade_date,
                    signal_type=name,
                    direction=direction,
                    rule_name=rule_name,
                    win_rate=win_rate,
                    hold_days=hold_days,
                    details=json.dumps({"backfilled": True}, ensure_ascii=False),
                )
                db.session.add(signal)
                saved += 1

    if saved > 0:
        db.session.commit()
        print(f"  [INFO] 历史信号回扫完成: {stock.code} 保存了 {saved} 条信号")


def _compute_stock_stats(df, stock):
    """计算该股票的历史信号统计"""
    valid = df.dropna(subset=["main_net"]).copy()
    if len(valid) < 20:
        return {"note": "数据不足"}

    # 连续3日流入次数
    inflow_3d_count = 0
    inflow_3d_returns = []
    for i in range(2, len(valid)):
        if _check_consecutive_inflow(valid, i, 3):
            inflow_3d_count += 1
            if i + 3 < len(valid):
                try:
                    ret = (valid.iloc[i+3]["close"] / valid.iloc[i]["close"] - 1) * 100
                    inflow_3d_returns.append(ret)
                except (TypeError, ZeroDivisionError):
                    pass

    # 连续3日流出次数
    outflow_3d_count = 0
    for i in range(2, len(valid)):
        for j in range(3):
            row = valid.iloc[i-j]
            if not (row.get("main_net") is not None and not np.isnan(row.get("main_net", 0)) and row["main_net"] < 0):
                break
        else:
            outflow_3d_count += 1

    # 近20日主力累计
    recent_20 = valid.tail(20)
    main_20d = recent_20["main_net"].sum() if "main_net" in recent_20.columns else 0

    # 近5日主力累计
    recent_5 = valid.tail(5)
    main_5d = recent_5["main_net"].sum() if "main_net" in recent_5.columns else 0

    # 该股信号胜率
    win_rate = None
    if inflow_3d_returns:
        wins = sum(1 for r in inflow_3d_returns if r > 0)
        win_rate = round(wins / len(inflow_3d_returns) * 100, 1)

    return {
        "inflow_3d_count": inflow_3d_count,
        "outflow_3d_count": outflow_3d_count,
        "main_5d": round(float(main_5d), 0),
        "main_20d": round(float(main_20d), 0),
        "stock_win_rate": win_rate,
        "stock_win_sample": len(inflow_3d_returns),
        "corr_mean": round(stock.corr_mean, 3) if stock and stock.corr_mean else None,
    }


def get_watchlist_summary(user_id):
    """获取用户自选股的最新信号汇总"""
    from models import Watchlist
    items = Watchlist.query.filter_by(user_id=user_id).all()
    results = []
    for item in items:
        stock = item.stock
        if not stock or not stock.latest_update:
            results.append({"stock": stock.to_dict() if stock else None, "signal": None,
                            "recommendation": "no_data"})
            continue

        # 获取最近的信号
        latest_signal = Signal.query.filter_by(stock_id=stock.id).order_by(
            Signal.date.desc()).first()

        # 快速判断当前状态
        df_data = DailyData.query.filter_by(stock_id=stock.id).order_by(
            DailyData.date.desc()).limit(10).all()
        df_data = list(reversed(df_data))

        current_state = "hold"
        if len(df_data) >= 3:
            recent_3 = df_data[-3:]
            all_inflow = all(r.main_net and r.main_net > 0 for r in recent_3 if r.main_net is not None)
            if all_inflow:
                current_state = "buy_pending"  # 有流入趋势，可能触发买入

            # 检查止盈
            if len(df_data) >= 4:
                if (df_data[-1].main_net and df_data[-1].main_net < 0 and
                    all(df_data[-1-j].main_net and df_data[-1-j].main_net > 0 for j in range(1, 4))):
                    current_state = "sell"

        results.append({
            "stock": stock.to_dict(),
            "latest_signal": latest_signal.to_dict() if latest_signal else None,
            "recommendation": current_state,
        })

    return results

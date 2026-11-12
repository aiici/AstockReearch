#!/usr/bin/env python3
"""
A股资金流向可视化仪表盘 — Flask 后端
基于 a-stock-data 技能获取实时数据，提供交互式可视化界面
"""

import json
import math
import os
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request, g, redirect, url_for, session

# 导入研究脚本中的所有函数
from capital_flow_research import (
    TARGETS, TODAY, START_DATE,
    fetch_all_data, merge_kline_fundflow, calc_fund_flow_summary,
    calc_rolling_correlation, event_study_consecutive_inflow,
    event_study_large_order_ratio, detect_divergence,
    format_amount, norm_ticker, get_prefix,
    tencent_kline, stock_fund_flow_120d,
    dragon_tiger_board, block_trade,
)

# 导入系统模块
from models import db, init_db, User, Stock, DailyData, Signal, Watchlist
from auth import (
    token_required, register_user, login_user, generate_token, verify_token,
    page_login_required, set_session, clear_session,
)
from data_service import fetch_and_store, get_stock_data, get_or_create_stock, get_realtime_quote, get_realtime_fund_flow
from signal_engine import analyze_stock, get_watchlist_summary, BUY_RULES, SELL_RULES

from datetime import timedelta

app = Flask(__name__)
app.config["SECRET_KEY"] = "astock-signal-system-2026"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# 初始化数据库
init_db(app)

VIZ_DATA_FILE = os.path.join(os.path.dirname(__file__), "viz_data.json")


# ═══════════════════════════════════════════════════════════════════════
# 数据准备：生成可视化所需的完整数据（含时间序列）
# ═══════════════════════════════════════════════════════════════════════

def prepare_viz_data():
    """运行完整分析并保存所有数据（含时间序列）到 viz_data.json"""
    print("=" * 60)
    print("正在准备可视化数据...")
    print("=" * 60)

    all_data = fetch_all_data()

    viz_data = {
        "meta": {
            "research_date": TODAY,
            "start_date": START_DATE,
            "generated_at": datetime.now().isoformat(),
            "targets": [{"code": t["code"], "name": t["name"],
                         "type": t["type"], "cap_group": t["cap_group"]} for t in TARGETS],
        },
        "signals": [],
        "targets": {},
        "cap_group_summary": {},
    }

    signals = []

    for code, data in all_data.items():
        info = data["info"]
        name = info["name"]
        group = info["cap_group"]
        print(f"\n处理: {name} ({code})")

        merged = merge_kline_fundflow(data["kline"], data["fund_flow"])
        summary = calc_fund_flow_summary(merged)
        rolling_corr_df = calc_rolling_correlation(merged, window=20)
        event_3d = event_study_consecutive_inflow(merged, n_days=3, hold_days_list=(5, 10))
        event_5d = event_study_consecutive_inflow(merged, n_days=5, hold_days_list=(5, 10))
        large_order = event_study_large_order_ratio(merged, threshold_pct=20)
        divergences = detect_divergence(merged, window=5)

        # 时间序列数据（用于 K线+资金流图、滚动相关系数图）
        timeseries = []
        if merged is not None and not merged.empty:
            for _, row in merged.iterrows():
                ts = {
                    "date": str(row.get("date", "")),
                    "close": float(row["close"]) if not pd.isna(row.get("close")) else None,
                    "open": float(row["open"]) if not pd.isna(row.get("open")) else None,
                    "high": float(row["high"]) if not pd.isna(row.get("high")) else None,
                    "low": float(row["low"]) if not pd.isna(row.get("low")) else None,
                    "volume": float(row["volume"]) if not pd.isna(row.get("volume")) else None,
                    "pct_change": float(row["pct_change"]) if not pd.isna(row.get("pct_change")) else None,
                    "main_net": float(row["main_net"]) if not pd.isna(row.get("main_net")) else None,
                    "super_net": float(row["super_net"]) if not pd.isna(row.get("super_net")) else None,
                    "large_net": float(row["large_net"]) if not pd.isna(row.get("large_net")) else None,
                    "mid_net": float(row["mid_net"]) if not pd.isna(row.get("mid_net")) else None,
                    "small_net": float(row["small_net"]) if not pd.isna(row.get("small_net")) else None,
                }
                # 滚动相关系数
                if rolling_corr_df is not None and not rolling_corr_df.empty:
                    match = rolling_corr_df[rolling_corr_df["date"] == ts["date"]]
                    if len(match) > 0:
                        ts["corr_same_day"] = float(match["corr_same_day"].iloc[0]) if not pd.isna(match["corr_same_day"].iloc[0]) else None
                        ts["corr_next_day"] = float(match["corr_next_day"].iloc[0]) if not pd.isna(match["corr_next_day"].iloc[0]) else None
                    else:
                        ts["corr_same_day"] = None
                        ts["corr_next_day"] = None
                else:
                    ts["corr_same_day"] = None
                    ts["corr_next_day"] = None
                timeseries.append(ts)

        # 滚动相关系数统计
        corr_stats = {"same_day_latest": None, "same_day_mean": None,
                      "next_day_latest": None, "next_day_mean": None, "sample_size": 0}
        if rolling_corr_df is not None and not rolling_corr_df.empty:
            corr_same = rolling_corr_df["corr_same_day"].dropna()
            corr_next = rolling_corr_df["corr_next_day"].dropna()
            if len(corr_same) > 0:
                corr_stats["same_day_latest"] = round(float(corr_same.iloc[-1]), 3)
                corr_stats["same_day_mean"] = round(float(corr_same.mean()), 3)
            if len(corr_next) > 0:
                corr_stats["next_day_latest"] = round(float(corr_next.iloc[-1]), 3)
                corr_stats["next_day_mean"] = round(float(corr_next.mean()), 3)
            corr_stats["sample_size"] = int(len(corr_same))

        # 信号检测
        target_signals = []
        if merged is not None and not merged.empty and not merged["main_net"].isna().all():
            df_valid = merged.dropna(subset=["main_net"]).tail(5)
            if len(df_valid) >= 3:
                last3_inflow = all(df_valid["main_net"].tail(3) > 0)
                last3_outflow = all(df_valid["main_net"].tail(3) < 0)
                if last3_inflow:
                    target_signals.append({"type": "inflow_3d", "text": "近3日主力连续净流入", "level": "warning"})
                if last3_outflow:
                    target_signals.append({"type": "outflow_3d", "text": "近3日主力连续净流出", "level": "danger"})
                last5_sum = float(df_valid["main_net"].tail(5).sum())
                direction = "流入" if last5_sum > 0 else "流出"
                target_signals.append({"type": "net_5d", "text": f"近5日主力累计净{direction} {format_amount(abs(last5_sum))}", "level": "info"})

            if divergences:
                last_div = divergences[-1]
                days_since = (datetime.strptime(TODAY, "%Y-%m-%d") -
                              datetime.strptime(last_div["date"], "%Y-%m-%d")).days
                if days_since <= 10:
                    target_signals.append({"type": "divergence", "text": f"近{days_since}日出现{last_div['type']}背离", "level": "warning"})

        if target_signals:
            signals.append({"code": code, "name": name, "group": group, "signals": target_signals})

        # 背离数据（用于可视化）
        div_data = []
        for d in divergences:
            div_data.append({
                "date": d["date"],
                "type": d["type"],
                "price_change": round(d["price_change"], 2),
                "fund_flow": d["fund_flow"],
                "future_5d_return": round(d["future_5d_return"], 2) if d["future_5d_return"] is not None else None,
            })

        # 事件回测数据清理（移除不可序列化的字段）
        def clean_event(ev):
            if ev is None:
                return None
            clean = {"n_events": ev.get("n_events", 0), "results": {}}
            for k, v in ev.get("results", {}).items():
                clean["results"][str(k)] = {
                    "sample": v.get("sample", 0),
                    "avg_return": round(v.get("avg_return", 0), 2),
                    "median_return": round(v.get("median_return", 0), 2),
                    "win_rate": round(v.get("win_rate", 0), 1),
                    "max_return": round(v.get("max_return", 0), 2),
                    "min_return": round(v.get("min_return", 0), 2),
                    "avg_max_drawdown": round(v.get("avg_max_drawdown", 0), 2),
                }
            return clean

        def clean_large_order(lo):
            if lo is None:
                return None
            return {
                "n_events": lo.get("n_events", 0),
                "threshold": lo.get("threshold", 20),
                "avg_next_return": round(lo.get("avg_next_return", 0), 2),
                "median_next_return": round(lo.get("median_next_return", 0), 2),
                "win_rate": round(lo.get("win_rate", 0), 1),
                "max_next_return": round(lo.get("max_next_return", 0), 2),
                "min_next_return": round(lo.get("min_next_return", 0), 2),
            }

        # 龙虎榜数据
        dt = data.get("dragon_tiger", {})
        dt_records = []
        if dt and dt.get("records"):
            for rec in dt["records"]:
                dt_records.append({
                    "date": rec["date"],
                    "reason": rec["reason"],
                    "net_buy": rec["net_buy"],
                    "turnover": rec["turnover"],
                })

        # 大宗交易数据
        bt_records = []
        for trade in data.get("block_trades", []):
            bt_records.append({
                "date": trade.get("date", ""),
                "price": trade.get("price"),
                "volume": trade.get("volume"),
                "buyer": trade.get("buyer", ""),
                "seller": trade.get("seller", ""),
                "premium": trade.get("premium"),
            })

        # 汇总
        summary_clean = None
        if summary:
            summary_clean = {}
            for period in ["5日", "20日", "60日"]:
                if period in summary:
                    s = summary[period]
                    summary_clean[period] = {
                        "main_net": s["main_net"],
                        "super_net": s["super_net"],
                        "large_net": s["large_net"],
                        "mid_net": s["mid_net"],
                        "small_net": s["small_net"],
                        "large_ratio": round(s["large_ratio"], 1),
                        "days": s["days"],
                    }

        latest_price = None
        latest_date = None
        latest_pct = None
        if merged is not None and not merged.empty:
            latest_row = merged.dropna(subset=["close"]).iloc[-1]
            latest_price = float(latest_row["close"])
            latest_date = str(latest_row["date"])
            latest_pct = float(latest_row.get("pct_change", 0)) if not pd.isna(latest_row.get("pct_change", 0)) else None

        viz_data["targets"][code] = {
            "info": {"code": code, "name": name, "type": info["type"], "cap_group": group},
            "latest_price": latest_price,
            "latest_date": latest_date,
            "latest_pct_change": latest_pct,
            "summary": summary_clean,
            "corr_stats": corr_stats,
            "event_3d": clean_event(event_3d),
            "event_5d": clean_event(event_5d),
            "large_order": clean_large_order(large_order),
            "divergences": div_data,
            "divergence_count": len(divergences),
            "dragon_tiger": dt_records,
            "block_trades": bt_records,
            "timeseries": timeseries,
            "kline_count": len(merged) if merged is not None else 0,
            "fund_flow_count": len(merged.dropna(subset=["main_net"])) if merged is not None else 0,
        }

    viz_data["signals"] = signals

    # 市值分组统计
    cap_groups = ["大盘", "中盘", "小盘"]
    for group in cap_groups:
        group_items = [(c, r) for c, r in viz_data["targets"].items()
                       if r["info"]["cap_group"] == group and r["summary"] and "20日" in r["summary"]]
        if group_items:
            ratios = [r["summary"]["20日"]["large_ratio"] for _, r in group_items]
            mains = [r["summary"]["20日"]["main_net"] for _, r in group_items]
            viz_data["cap_group_summary"][group] = {
                "avg_large_ratio_20d": round(float(np.mean(ratios)), 1),
                "avg_main_net_20d": float(np.mean(mains)),
                "count": len(group_items),
            }

    # 保存
    with open(VIZ_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(viz_data, f, ensure_ascii=False, default=str)
    print(f"\n可视化数据已保存至: {VIZ_DATA_FILE}")
    print(f"文件大小: {os.path.getsize(VIZ_DATA_FILE) / 1024:.0f} KB")

    return viz_data


def load_viz_data():
    """加载缓存的可视化数据"""
    if os.path.exists(VIZ_DATA_FILE):
        with open(VIZ_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ═══════════════════════════════════════════════════════════════════════
# Flask 路由
# ═══════════════════════════════════════════════════════════════════════

@app.route("/")
@page_login_required
def dashboard():
    """主仪表盘页面（需登录）"""
    return render_template("dashboard.html")


@app.route("/api/data")
@token_required
def api_data():
    """返回完整可视化数据"""
    data = load_viz_data()
    if data is None:
        return jsonify({"error": "数据未准备，请先点击刷新"}), 503
    return jsonify(data)


@app.route("/api/timeseries/<code>")
@token_required
def api_timeseries(code):
    """返回指定标的时间序列数据"""
    data = load_viz_data()
    if data is None:
        return jsonify({"error": "数据未准备"}), 503
    target = data["targets"].get(code)
    if target is None:
        return jsonify({"error": f"未找到标的 {code}"}), 404
    return jsonify({
        "info": target["info"],
        "timeseries": target["timeseries"],
        "latest_price": target["latest_price"],
    })


@app.route("/api/refresh", methods=["POST"])
@token_required
def api_refresh():
    """重新获取数据并分析"""
    try:
        data = prepare_viz_data()
        return jsonify({"status": "ok", "message": "数据刷新成功", "generated_at": data["meta"]["generated_at"]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# 导入规律分析
from pattern_analysis import analyze_patterns

# 导入信号挖掘
from signal_finder import find_best_signals, generate_trading_rules

@app.route("/api/signals")
@token_required
def api_signals():
    """返回交易信号回测结果"""
    try:
        data = load_viz_data()
        if data is None:
            return jsonify({"error": "数据未准备"}), 503
        all_results = find_best_signals(data, min_win_rate=60, min_sample=3)
        rules = generate_trading_rules(all_results)
        buy_70 = [r for r in all_results if r["type"] == "buy" and r["win_rate"] >= 70]
        return jsonify({
            "all_signals": all_results[:30],
            "trading_rules": rules,
            "buy_rules_70": [r for r in rules if r["direction"] == "买入" and r["win_rate"] >= 70],
            "sell_rules": [r for r in rules if r["direction"] == "卖出/回避"],
            "stats": {
                "total_tested": 42,
                "buy_above_70": len(buy_70),
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/patterns")
@token_required
def api_patterns():
    """返回规律分析结果"""
    try:
        results = analyze_patterns()
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/status")
@token_required
def api_status():
    """检查数据状态"""
    data = load_viz_data()
    if data is None:
        return jsonify({"ready": False, "message": "数据未准备"})
    return jsonify({
        "ready": True,
        "research_date": data["meta"]["research_date"],
        "generated_at": data["meta"]["generated_at"],
        "targets_count": len(data["targets"]),
    })


# ═══════════════════════════════════════════════════════════════════════
# 交易信号系统路由
# ═══════════════════════════════════════════════════════════════════════

@app.route("/login")
def login_page():
    """登录/注册页面（已登录则跳转）"""
    if session.get("user_id"):
        return redirect(url_for("system_page"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    """退出登录"""
    clear_session()
    return redirect(url_for("login_page"))


@app.route("/system")
@page_login_required
def system_page():
    """交易信号系统主页（需登录）"""
    return render_template("system.html")


# ── 鉴权路由 ──

@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    result, status = register_user(data.get("username", ""), data.get("password", ""))
    return jsonify(result), status


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    result, status = login_user(data.get("username", ""), data.get("password", ""))
    return jsonify(result), status


@app.route("/api/auth/verify")
@token_required
def api_verify():
    return jsonify({"valid": True, "user": g.current_user.to_dict()})


# ── 实时行情路由 ──

@app.route("/api/realtime/<code>")
@token_required
def api_realtime(code):
    """获取实时行情（价格+资金流）。push2失败时回退到数据库"""
    quote = get_realtime_quote(code)
    fund_flow = get_realtime_fund_flow(code)

    # push2 间歇性不可用，回退到数据库今日数据
    if not fund_flow:
        stock = Stock.query.filter_by(code=code).first()
        if stock:
            today_data = DailyData.query.filter_by(
                stock_id=stock.id, date=date.today().isoformat()
            ).first()
            if today_data and today_data.main_net is not None:
                fund_flow = {
                    "date": today_data.date,
                    "main_net": today_data.main_net,
                    "super_net": today_data.super_net,
                    "large_net": today_data.large_net,
                    "mid_net": today_data.mid_net,
                    "small_net": today_data.small_net,
                    "is_realtime": False,
                }

    return jsonify({
        "quote": quote,
        "fund_flow": fund_flow,
    })


# ── 股票分析路由 ──

@app.route("/api/analyze", methods=["POST"])
@token_required
def api_analyze():
    """输入股票代码，获取买卖信号"""
    data = request.get_json() or {}
    code = data.get("code", "").strip()
    force_refresh = data.get("force_refresh", False)

    if not code:
        return jsonify({"error": "请输入股票代码"}), 400

    try:
        # 获取并存储数据
        result = fetch_and_store(code, force_refresh=force_refresh)
        stock = Stock.query.filter_by(code=result["stock"]["code"]).first()
        if not stock:
            return jsonify({"error": "股票未找到"}), 404

        # 运行信号引擎
        analysis = analyze_stock(stock.id, save_signals=True)
        return jsonify(analysis)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/stocks/<code>/signals")
@token_required
def api_stock_signals(code):
    """获取已存储的股票信号"""
    stock = Stock.query.filter_by(code=code).first()
    if not stock:
        return jsonify({"error": "股票未找到"}), 404

    signals = Signal.query.filter_by(stock_id=stock.id).order_by(
        Signal.date.desc()).limit(50).all()
    return jsonify({
        "stock": stock.to_dict(),
        "signals": [s.to_dict() for s in signals],
    })


@app.route("/api/stocks/search")
@token_required
def api_stock_search():
    """搜索已存储的股票"""
    q = request.args.get("q", "").strip()
    if not q:
        stocks = Stock.query.limit(20).all()
    else:
        stocks = Stock.query.filter(
            (Stock.code.contains(q)) | (Stock.name.contains(q))
        ).limit(20).all()
    return jsonify({"stocks": [s.to_dict() for s in stocks]})


# ── 自选股路由 ──

@app.route("/api/watchlist", methods=["GET", "POST"])
@token_required
def api_watchlist():
    user = g.current_user

    if request.method == "GET":
        items = Watchlist.query.filter_by(user_id=user.id).all()
        result = []
        for item in items:
            stock = item.stock
            # 获取最新信号
            latest_signal = Signal.query.filter_by(stock_id=stock.id).order_by(
                Signal.date.desc()).first() if stock else None
            result.append({
                "stock": stock.to_dict() if stock else None,
                "latest_signal": latest_signal.to_dict() if latest_signal else None,
                "added_at": item.created_at.isoformat() if item.created_at else None,
            })
        return jsonify({"watchlist": result})

    elif request.method == "POST":
        data = request.get_json() or {}
        code = data.get("code", "").strip()
        if not code:
            return jsonify({"error": "请输入股票代码"}), 400

        stock = get_or_create_stock(code)
        existing = Watchlist.query.filter_by(user_id=user.id, stock_id=stock.id).first()
        if existing:
            return jsonify({"message": "已在自选股中", "stock": stock.to_dict()}), 200

        wl = Watchlist(user_id=user.id, stock_id=stock.id)
        db.session.add(wl)
        db.session.commit()
        return jsonify({"message": "添加成功", "stock": stock.to_dict()}), 201


@app.route("/api/watchlist/<code>", methods=["DELETE"])
@token_required
def api_watchlist_delete(code):
    user = g.current_user
    stock = Stock.query.filter_by(code=code).first()
    if not stock:
        return jsonify({"error": "股票未找到"}), 404

    wl = Watchlist.query.filter_by(user_id=user.id, stock_id=stock.id).first()
    if not wl:
        return jsonify({"error": "不在自选股中"}), 404

    db.session.delete(wl)
    db.session.commit()
    return jsonify({"message": "删除成功"})


# ── 自选股仪表盘 ──

@app.route("/api/dashboard")
@token_required
def api_dashboard():
    """获取自选股最新信号汇总"""
    user = g.current_user
    results = get_watchlist_summary(user.id)
    return jsonify({"dashboard": results})


# ── 买卖规则列表 ──

@app.route("/api/rules")
@token_required
def api_rules():
    """返回所有买卖规则"""
    return jsonify({
        "buy_rules": BUY_RULES,
        "sell_rules": SELL_RULES,
    })


# ── 市场扫描 ──

from market_scanner import scan_market, get_stock_name, _fetch_all_stocks, _fetch_fund_flow_batch, _calc_flow_periods

# 扫描结果缓存
SCAN_CACHE_FILE = os.path.join(os.path.dirname(__file__), "scan_results.json")

@app.route("/api/scan", methods=["POST"])
@token_required
def api_scan():
    """全市场扫描买入信号"""
    data = request.get_json() or {}
    max_deep = data.get("max_deep", 50)
    min_main_net_yi = data.get("min_main_net_yi", 0.0)

    try:
        result = scan_market(max_deep=max_deep, min_main_net_yi=min_main_net_yi)

        # 缓存结果
        with open(SCAN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, default=str)

        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan/results")
@token_required
def api_scan_results():
    """获取上次扫描结果"""
    if os.path.exists(SCAN_CACHE_FILE):
        with open(SCAN_CACHE_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({"results": [], "message": "尚未扫描"})


@app.route("/api/scan/preview")
@token_required
def api_scan_preview():
    """快速预览全市场资金流排行（TOP60 + 多周期 + 持续流入分析）"""
    try:
        total, all_stocks = _fetch_all_stocks()
        # 按主力净额排序
        all_stocks.sort(key=lambda x: x["main_net"], reverse=True)
        top60 = all_stocks[:60]

        # 批量获取日级资金流数据（最近60个交易日）
        # Sina降级时日级数据已附带在股票对象中，直接复用
        has_daily = any("_daily_flows" in s for s in top60)
        if has_daily:
            print(f"  复用Sina日级资金流数据...")
            daily_flows = {s["code"]: s.get("_daily_flows", []) for s in top60}
        else:
            print(f"  批量获取TOP60日级资金流...")
            daily_flows = _fetch_fund_flow_batch(top60, lmt=60)

        # 计算各周期累计净流入和持续流入状态
        candidates = []
        for s in top60:
            code = s["code"]
            flows = daily_flows.get(code, [])
            flow_data = _calc_flow_periods(flows)

            periods = flow_data["periods"]
            cont = flow_data["continuous"]

            # 各周期累计净额（亿）
            main_net_yi = round(s["main_net"] / 1e8, 2)
            main_5d_yi = round(s["main_5d"] / 1e8, 2)
            main_10d_yi = round(s.get("main_10d", 0) / 1e8, 2)
            main_15d_yi = round(periods["15d"] / 1e8, 2)
            main_30d_yi = round(periods["30d"] / 1e8, 2)
            main_45d_yi = round(periods["45d"] / 1e8, 2)

            # 资金流方向标记
            period_vals = [main_net_yi, main_5d_yi, main_10d_yi, main_15d_yi, main_30d_yi, main_45d_yi]
            positive_count = sum(1 for v in period_vals if v > 0)

            candidate = {
                "code": code,
                "name": s["name"],
                "price": s["price"],
                "pct_change": s["pct_change"],
                # 今日 + clist API直接提供的周期数据
                "main_net_yi": main_net_yi,
                "main_5d_yi": main_5d_yi,
                "main_10d_yi": main_10d_yi,
                # 从日级数据计算的15/30/45日
                "main_15d_yi": main_15d_yi,
                "main_30d_yi": main_30d_yi,
                "main_45d_yi": main_45d_yi,
                # 超大单/大单（今日）
                "super_net_yi": round(s.get("super_net", 0) / 1e8, 2),
                "large_net_yi": round(s.get("large_net", 0) / 1e8, 2),
                # 持续流入分析
                "inflow_streak": cont["streak"],
                "inflow_3d": cont["3d"],
                "inflow_5d": cont["5d"],
                "inflow_10d": cont["10d"],
                "inflow_15d": cont["15d"],
                "inflow_30d": cont["30d"],
                # 资金流方向标记
                "all_inflow": positive_count == 6,            # 6个周期全部流入
                "today_5d_inflow": main_net_yi > 0 and main_5d_yi > 0,  # 今日+5日双流入
                "multi_period_inflow": positive_count >= 4,    # 至少4个周期流入
                "positive_count": positive_count,
            }
            candidates.append(candidate)

        # 统计各筛选条件的股票数
        inflow_summary = {
            "3d": sum(1 for c in candidates if c["inflow_3d"]),
            "5d": sum(1 for c in candidates if c["inflow_5d"]),
            "10d": sum(1 for c in candidates if c["inflow_10d"]),
            "15d": sum(1 for c in candidates if c["inflow_15d"]),
            "30d": sum(1 for c in candidates if c["inflow_30d"]),
            "all_inflow": sum(1 for c in candidates if c["all_inflow"]),
            "today_5d": sum(1 for c in candidates if c["today_5d_inflow"]),
            "multi_period": sum(1 for c in candidates if c["multi_period_inflow"]),
        }

        return jsonify({
            "total_market": total,
            "total_scanned": total,
            "candidates": len(all_stocks),
            "top_candidates": candidates,
            "inflow_summary": inflow_summary,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "total_market": 0, "top_candidates": [], "inflow_summary": {}}), 200


# ═══════════════════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 启动前检查数据
    data = load_viz_data()
    if data is None:
        print("未找到缓存数据，正在生成...")
        prepare_viz_data()

    print("\n" + "=" * 60)
    print("A股资金流向可视化仪表盘")
    print("研究页面: http://localhost:5000")
    print("登录页面: http://localhost:5000/login")
    print("信号系统: http://localhost:5000/system")
    print("数据库:   stock_system.db")
    print("=" * 60)

    app.run(host="0.0.0.0", port=5000, debug=False)

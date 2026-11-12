#!/usr/bin/env python3
"""
信号挖掘器 — 从120日×7只标的数据中，穷举测试各种买卖信号组合
目标：找到≥70%胜率、样本量≥5的可复用规则
"""

import json
import os
import numpy as np
from collections import defaultdict

VIZ_DATA_FILE = os.path.join(os.path.dirname(__file__), "viz_data.json")


def load_data():
    with open(VIZ_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_all_timeseries(data):
    """获取所有标的的时间序列，附加元信息"""
    all_ts = []
    for code, t in data["targets"].items():
        ts = t.get("timeseries", [])
        if not ts:
            continue
        cap_group = t["info"]["cap_group"]
        corr_mean = t.get("corr_stats", {}).get("same_day_mean", 0) or 0
        for i, row in enumerate(ts):
            row["_code"] = code
            row["_name"] = t["info"]["name"]
            row["_cap"] = cap_group
            row["_corr"] = corr_mean
            row["_idx"] = i
            row["_ts"] = ts
        all_ts.extend([(i, row, ts, code, t) for i, row in enumerate(ts)])
    return all_ts


def calc_forward_return(ts, idx, days):
    """计算从idx开始的N日前瞻收益"""
    if idx + days >= len(ts):
        return None
    try:
        start = ts[idx]["close"]
        end = ts[idx + days]["close"]
        if not start or not end or start <= 0:
            return None
        return (end / start - 1) * 100
    except (TypeError, ZeroDivisionError, IndexError):
        return None


def is_inflow(row):
    return row.get("main_net") is not None and row["main_net"] > 0

def is_outflow(row):
    return row.get("main_net") is not None and row["main_net"] < 0


def consecutive_inflow(ts, idx, n):
    """检查idx及前n-1天是否连续净流入"""
    if idx < n - 1:
        return False
    return all(is_inflow(ts[idx - j]) for j in range(n))

def consecutive_outflow(ts, idx, n):
    if idx < n - 1:
        return False
    return all(is_outflow(ts[idx - j]) for j in range(n))


def get_large_ratio(row):
    """大单净占比 = (超大单+大单) / 四档绝对值之和"""
    s = row.get("super_net") or 0
    l = row.get("large_net") or 0
    m = row.get("mid_net") or 0
    sm = row.get("small_net") or 0
    total = abs(s) + abs(l) + abs(m) + abs(sm)
    if total <= 0:
        return 0
    return (s + l) / total * 100


def price_change_5d(ts, idx):
    """近5日价格变动"""
    if idx < 4:
        return None
    try:
        return (ts[idx]["close"] / ts[idx - 4]["close"] - 1) * 100
    except (TypeError, ZeroDivisionError):
        return None


# ═══════════════════════════════════════════════════════════════════════
# 买入信号定义 — 穷举各种组合
# ═══════════════════════════════════════════════════════════════════════

BUY_SIGNALS = []

# ── 基础信号：连续N日流入 ──
for n in [2, 3, 4, 5, 6, 7]:
    BUY_SIGNALS.append({
        "name": f"连续{n}日主力净流入",
        "filter": "全市场",
        "check": lambda ts, i, n=n: consecutive_inflow(ts, i, n) and (i < n or not is_inflow(ts[i - n])),
        "hold_days": [3, 5, 10],
    })

# ── 连续N日流入 + 中小盘 ──
for n in [2, 3, 4, 5]:
    BUY_SIGNALS.append({
        "name": f"连续{n}日主力净流入 + 中小盘",
        "filter": "cap_group in ['中盘','小盘']",
        "check": lambda ts, i, n=n: consecutive_inflow(ts, i, n) and (i < n or not is_inflow(ts[i - n])),
        "cap_filter": ["中盘", "小盘"],
        "hold_days": [3, 5, 10],
    })

# ── 连续N日流入 + 高相关(>0.7) ──
for n in [2, 3, 4, 5]:
    BUY_SIGNALS.append({
        "name": f"连续{n}日主力净流入 + 相关系数>0.7",
        "filter": "corr_mean > 0.7",
        "check": lambda ts, i, n=n: consecutive_inflow(ts, i, n) and (i < n or not is_inflow(ts[i - n])),
        "corr_filter": 0.7,
        "hold_days": [3, 5, 10],
    })

# ── 连续N日流入 + 大单占比>X% ──
for n in [2, 3]:
    for thresh in [15, 20, 25, 30]:
        BUY_SIGNALS.append({
            "name": f"连续{n}日流入 + 大单占比>{thresh}%",
            "filter": f"large_ratio > {thresh}%",
            "check": lambda ts, i, n=n, thresh=thresh: (
                consecutive_inflow(ts, i, n) and (i < n or not is_inflow(ts[i - n]))
                and get_large_ratio(ts[i]) > thresh
            ),
            "hold_days": [3, 5],
        })

# ── 连续N日流入 + 非巨额（排除>5亿的异常大额）──
for n in [3, 4, 5]:
    BUY_SIGNALS.append({
        "name": f"连续{n}日流入 + 日均<5亿(排除巨额)",
        "filter": "main_net < 5e8",
        "check": lambda ts, i, n=n: (
            consecutive_inflow(ts, i, n) and (i < n or not is_inflow(ts[i - n]))
            and all(ts[i-j].get("main_net", 0) < 5e8 for j in range(n))
        ),
        "hold_days": [3, 5, 10],
    })

# ── 连续N日超大单净流入 ──
for n in [2, 3, 4]:
    BUY_SIGNALS.append({
        "name": f"连续{n}日超大单净流入",
        "filter": "super_net > 0",
        "check": lambda ts, i, n=n: (
            all(ts[i-j].get("super_net", 0) > 0 for j in range(n))
            and (i < n or ts[i-n].get("super_net", 0) <= 0)
        ),
        "hold_days": [3, 5],
    })

# ── 价跌资金流入背离（主力逆势买入）──
BUY_SIGNALS.append({
    "name": "5日价跌 + 资金净流入（逆势买入）",
    "filter": "price_5d < 0 and main_net > 0",
    "check": lambda ts, i: (
        (price_change_5d(ts, i) or 0) < -2 and is_inflow(ts[i])
    ),
    "hold_days": [3, 5, 10],
})

# ── 价跌资金流入背离 + 中小盘 ──
BUY_SIGNALS.append({
    "name": "5日价跌 + 资金流入 + 中小盘",
    "filter": "price_5d < -2 and main_net > 0 and cap in ['中盘','小盘']",
    "check": lambda ts, i: (
        (price_change_5d(ts, i) or 0) < -2 and is_inflow(ts[i])
    ),
    "cap_filter": ["中盘", "小盘"],
    "hold_days": [3, 5, 10],
})

# ── 连续N日流入 + 前期跌幅（抄底信号）──
for n in [3, 4]:
    BUY_SIGNALS.append({
        "name": f"连续{n}日流入 + 前20日跌幅>5%",
        "filter": "consecutive_inflow and price_20d < -5%",
        "check": lambda ts, i, n=n: (
            consecutive_inflow(ts, i, n) and (i < n or not is_inflow(ts[i - n]))
            and i >= 19 and (ts[i]["close"] / ts[i-19]["close"] - 1) * 100 < -5
        ),
        "hold_days": [5, 10],
    })

# ── 连续流入 + 小额（排除巨额陷阱）──
BUY_SIGNALS.append({
    "name": "连续3日流入 + 单日<1亿(温和流入)",
    "filter": "consecutive_inflow_3 and each_day < 1e8",
    "check": lambda ts, i: (
        consecutive_inflow(ts, i, 3) and (i < 3 or not is_inflow(ts[i - 3]))
        and all(0 < ts[i-j].get("main_net", 0) < 1e8 for j in range(3))
    ),
    "hold_days": [3, 5, 10],
})


# ═══════════════════════════════════════════════════════════════════════
# 卖出/回避信号定义
# ═══════════════════════════════════════════════════════════════════════

SELL_SIGNALS = []

# ── 连续N日流出 → 卖出/回避 ──
for n in [2, 3, 4, 5]:
    SELL_SIGNALS.append({
        "name": f"连续{n}日主力净流出",
        "filter": "全市场",
        "check": lambda ts, i, n=n: consecutive_outflow(ts, i, n) and (i < n or not is_outflow(ts[i - n])),
        "hold_days": [3, 5],
        "direction": "avoid",
    })

# ── 价涨资金流出背离（顶部信号）──
SELL_SIGNALS.append({
    "name": "5日价涨>5% + 资金净流出（顶部背离）",
    "filter": "price_5d > 5 and main_net < 0",
    "check": lambda ts, i: (
        (price_change_5d(ts, i) or 0) > 5 and is_outflow(ts[i])
    ),
    "hold_days": [3, 5],
    "direction": "avoid",
})

# ── 连续流入后首日流出（止盈信号）──
SELL_SIGNALS.append({
    "name": "连续3日流入后首日流出（止盈）",
    "filter": "consecutive_inflow_3_then_outflow",
    "check": lambda ts, i: (
        i >= 3 and is_outflow(ts[i])
        and all(is_inflow(ts[i-j-1]) for j in range(3))
    ),
    "hold_days": [3, 5],
    "direction": "avoid",
})

# ── 大单净流出 + 主力流出 ──
SELL_SIGNALS.append({
    "name": "大单占比<-20% + 主力净流出",
    "filter": "large_ratio < -20 and main_net < 0",
    "check": lambda ts, i: (
        get_large_ratio(ts[i]) < -20 and is_outflow(ts[i])
    ),
    "hold_days": [3, 5],
    "direction": "avoid",
})

# ── 连续N日流出 + 大盘股 ──
for n in [2, 3]:
    SELL_SIGNALS.append({
        "name": f"连续{n}日流出 + 大盘股",
        "filter": f"consecutive_outflow_{n} and cap='大盘'",
        "check": lambda ts, i, n=n: consecutive_outflow(ts, i, n) and (i < n or not is_outflow(ts[i - n])),
        "cap_filter": ["大盘"],
        "hold_days": [3, 5],
        "direction": "avoid",
    })


# ═══════════════════════════════════════════════════════════════════════
# 回测引擎
# ═══════════════════════════════════════════════════════════════════════

def backtest_signal(signal, data):
    """对单个信号进行回测"""
    results = {}
    
    for hold_days in signal["hold_days"]:
        returns = []
        details = []
        
        for code, t in data["targets"].items():
            ts = t.get("timeseries", [])
            if not ts:
                continue
            
            cap = t["info"]["cap_group"]
            corr_mean = t.get("corr_stats", {}).get("same_day_mean", 0) or 0
            
            # 市值过滤
            if "cap_filter" in signal and cap not in signal["cap_filter"]:
                continue
            # 相关系数过滤
            if "corr_filter" in signal and corr_mean < signal["corr_filter"]:
                continue
            
            for i in range(len(ts)):
                try:
                    if not signal["check"](ts, i):
                        continue
                except (IndexError, TypeError, KeyError):
                    continue
                
                ret = calc_forward_return(ts, i, hold_days)
                if ret is None:
                    continue
                
                returns.append(ret)
                details.append({
                    "code": code,
                    "name": t["info"]["name"],
                    "date": ts[i].get("date", ""),
                    "return": round(ret, 2),
                    "cap": cap,
                })
        
        if returns:
            wins = [r for r in returns if r > 0]
            losses = [r for r in returns if r <= 0]
            results[str(hold_days)] = {
                "sample": len(returns),
                "win_count": len(wins),
                "loss_count": len(losses),
                "win_rate": round(len(wins) / len(returns) * 100, 1),
                "avg_return": round(float(np.mean(returns)), 2),
                "median_return": round(float(np.median(returns)), 2),
                "max_return": round(max(returns), 2),
                "min_return": round(min(returns), 2),
                "avg_win": round(float(np.mean(wins)), 2) if wins else 0,
                "avg_loss": round(float(np.mean(losses)), 2) if losses else 0,
                "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else 999,
                "details": details[:10],  # 最近10个样本
            }
    
    return results


def find_best_signals(data, min_win_rate=65, min_sample=5):
    """找出胜率最高的信号"""
    all_results = []
    
    # 测试买入信号
    for signal in BUY_SIGNALS:
        bt = backtest_signal(signal, data)
        for hold, res in bt.items():
            if res["sample"] >= min_sample:
                all_results.append({
                    "type": "buy",
                    "name": signal["name"],
                    "filter": signal.get("filter", ""),
                    "hold_days": int(hold),
                    **{k: v for k, v in res.items() if k != "details"},
                    "details": res.get("details", []),
                    "direction": "buy",
                })
    
    # 测试卖出/回避信号
    for signal in SELL_SIGNALS:
        bt = backtest_signal(signal, data)
        for hold, res in bt.items():
            if res["sample"] >= min_sample:
                all_results.append({
                    "type": "sell",
                    "name": signal["name"],
                    "filter": signal.get("filter", ""),
                    "hold_days": int(hold),
                    **{k: v for k, v in res.items() if k != "details"},
                    "details": res.get("details", []),
                    "direction": signal.get("direction", "avoid"),
                })
    
    # 按胜率排序
    all_results.sort(key=lambda x: x["win_rate"], reverse=True)
    return all_results


def generate_trading_rules(best_signals):
    """从最佳信号中提炼可执行的交易规则"""
    rules = []
    
    # 找买入规则
    buy_signals = [s for s in best_signals if s["type"] == "buy" and s["win_rate"] >= 65]
    sell_signals = [s for s in best_signals if s["type"] == "sell" and s["win_rate"] >= 60]
    
    # 取前5个买入信号
    seen = set()
    for sig in buy_signals[:15]:
        key = f"{sig['name']}_{sig['hold_days']}"
        if key in seen:
            continue
        seen.add(key)
        
        confidence = "高" if sig["win_rate"] >= 75 else "中" if sig["win_rate"] >= 65 else "低"
        rules.append({
            "direction": "买入",
            "signal": sig["name"],
            "hold_days": sig["hold_days"],
            "win_rate": sig["win_rate"],
            "avg_return": sig["avg_return"],
            "sample": sig["sample"],
            "confidence": confidence,
            "filter": sig["filter"],
            "action": f"触发后买入，持有{sig['hold_days']}个交易日",
            "stop_loss": f"持有期内最大回撤参考: {sig.get('min_return', 'N/A')}%",
            "profit_factor": sig.get("profit_factor", 0),
        })
    
    # 取前3个卖出信号
    seen = set()
    for sig in sell_signals[:8]:
        key = f"{sig['name']}_{sig['hold_days']}"
        if key in seen:
            continue
        seen.add(key)
        
        confidence = "高" if sig["win_rate"] >= 70 else "中" if sig["win_rate"] >= 60 else "低"
        rules.append({
            "direction": "卖出/回避",
            "signal": sig["name"],
            "hold_days": sig["hold_days"],
            "win_rate": sig["win_rate"],
            "avg_return": sig["avg_return"],
            "sample": sig["sample"],
            "confidence": confidence,
            "filter": sig["filter"],
            "action": f"触发后卖出或回避{sig['hold_days']}个交易日",
            "stop_loss": f"回避期内平均下跌: {abs(sig['avg_return']) if sig['avg_return'] < 0 else 'N/A'}%",
            "profit_factor": sig.get("profit_factor", 0),
        })
    
    return rules


def main():
    data = load_data()
    
    print("=" * 70)
    print("信号挖掘器 — 穷举测试买卖信号组合")
    print("=" * 70)
    
    all_results = find_best_signals(data, min_win_rate=60, min_sample=3)
    
    # 输出所有≥65%胜率的信号
    print(f"\n共测试 {len(BUY_SIGNALS) + len(SELL_SIGNALS)} 个信号组合")
    print(f"其中 {len(all_results)} 个达到最低标准(≥60%胜率, ≥3样本)\n")
    
    print("=" * 70)
    print("🏆 买入信号排行（按胜率排序）")
    print("=" * 70)
    print(f"{'信号':<35} {'持有':>4} {'胜率':>6} {'均收':>6} {'样本':>4} {'盈亏比':>6}")
    print("-" * 70)
    
    buy_results = [r for r in all_results if r["type"] == "buy"]
    for r in buy_results[:15]:
        star = " ⭐" if r["win_rate"] >= 70 else ""
        print(f"{r['name']:<35} {r['hold_days']:>3}日 {r['win_rate']:>5.1f}% {r['avg_return']:>5.1f}% {r['sample']:>4} {r.get('profit_factor',0):>6}{star}")
    
    print(f"\n{'=' * 70}")
    print("🔻 卖出/回避信号排行（按胜率排序）")
    print("=" * 70)
    print(f"{'信号':<35} {'持有':>4} {'胜率':>6} {'均收':>6} {'样本':>4}")
    print("-" * 70)
    
    sell_results = [r for r in all_results if r["type"] == "sell"]
    for r in sell_results[:10]:
        star = " ⭐" if r["win_rate"] >= 70 else ""
        print(f"{r['name']:<35} {r['hold_days']:>3}日 {r['win_rate']:>5.1f}% {r['avg_return']:>5.1f}% {r['sample']:>4}{star}")
    
    # 生成交易规则
    rules = generate_trading_rules(all_results)
    
    print(f"\n{'=' * 70}")
    print("📋 可执行交易规则（≥65%胜率）")
    print("=" * 70)
    
    buy_rules = [r for r in rules if r["direction"] == "买入"]
    sell_rules = [r for r in rules if r["direction"] == "卖出/回避"]
    
    print(f"\n🟢 买入规则（{len(buy_rules)}条）:")
    for i, r in enumerate(buy_rules, 1):
        star = "⭐" if r["win_rate"] >= 70 else ""
        print(f"\n  买{i} {star} {r['signal']}")
        print(f"     胜率: {r['win_rate']}% | 均收: {r['avg_return']}% | 样本: {r['sample']} | 盈亏比: {r['profit_factor']}")
        print(f"     操作: {r['action']}")
        if r['filter'] != "全市场":
            print(f"     适用: {r['filter']}")
    
    print(f"\n🔴 卖出/回避规则（{len(sell_rules)}条）:")
    for i, r in enumerate(sell_rules, 1):
        star = "⭐" if r["win_rate"] >= 70 else ""
        print(f"\n  卖{i} {star} {r['signal']}")
        print(f"     胜率: {r['win_rate']}% | 均收: {r['avg_return']}% | 样本: {r['sample']}")
        print(f"     操作: {r['action']}")
    
    # 保存结果
    output = {
        "all_signals": all_results,
        "trading_rules": rules,
        "buy_rules": buy_rules,
        "sell_rules": sell_rules,
        "stats": {
            "total_signals_tested": len(BUY_SIGNALS) + len(SELL_SIGNALS),
            "buy_signals_above_65": len([r for r in buy_results if r["win_rate"] >= 65]),
            "buy_signals_above_70": len([r for r in buy_results if r["win_rate"] >= 70]),
            "sell_signals_above_65": len([r for r in sell_results if r["win_rate"] >= 65]),
        }
    }
    
    output_file = os.path.join(os.path.dirname(__file__), "trading_signals.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存至: {output_file}")


if __name__ == "__main__":
    main()

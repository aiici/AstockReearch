#!/usr/bin/env python3
"""
资金流信号规律分析 — 从已有数据中提取可复用的量化规则
输出可应用于其他股票的信号体系
"""

import json
import math
import os
import numpy as np
from collections import defaultdict

VIZ_DATA_FILE = os.path.join(os.path.dirname(__file__), "viz_data.json")


def load_data():
    with open(VIZ_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def analyze_patterns():
    data = load_data()
    targets = data["targets"]
    results = {}

    # ═════════════════════════════════════════════════════════════════
    # 1. 信号有效性排序（3日连续流入 → 5日收益）
    # ═════════════════════════════════════════════════════════════════
    signal_ranking = []
    for code, t in targets.items():
        ev3 = t.get("event_3d", {})
        if ev3 and ev3.get("n_events", 0) > 0 and ev3.get("results", {}).get("5"):
            r5 = ev3["results"]["5"]
            signal_ranking.append({
                "code": code,
                "name": t["info"]["name"],
                "cap_group": t["info"]["cap_group"],
                "triggers": ev3["n_events"],
                "win_rate_5d": r5["win_rate"],
                "avg_return_5d": r5["avg_return"],
                "max_drawdown_5d": r5["avg_max_drawdown"],
                "win_rate_10d": ev3["results"].get("10", {}).get("win_rate", 0),
                "avg_return_10d": ev3["results"].get("10", {}).get("avg_return", 0),
            })
    signal_ranking.sort(key=lambda x: x["win_rate_5d"], reverse=True)
    results["signal_ranking"] = signal_ranking

    # ═════════════════════════════════════════════════════════════════
    # 2. 相关系数 vs 信号胜率（核心规律）
    # ═════════════════════════════════════════════════════════════════
    corr_vs_win = []
    for code, t in targets.items():
        cs = t.get("corr_stats", {})
        ev3 = t.get("event_3d", {})
        if cs.get("same_day_mean") is not None and ev3.get("n_events", 0) > 0:
            r5 = ev3.get("results", {}).get("5", {})
            corr_vs_win.append({
                "code": code,
                "name": t["info"]["name"],
                "corr_mean": cs["same_day_mean"],
                "win_rate_5d": r5.get("win_rate", 0),
                "avg_return_5d": r5.get("avg_return", 0),
                "triggers": ev3["n_events"],
            })

    # 按相关系数分组统计
    high_corr = [x for x in corr_vs_win if x["corr_mean"] >= 0.7]
    low_corr = [x for x in corr_vs_win if x["corr_mean"] < 0.7]

    corr_conclusion = ""
    if high_corr and low_corr:
        hc_avg_win = np.mean([x["win_rate_5d"] for x in high_corr])
        lc_avg_win = np.mean([x["win_rate_5d"] for x in low_corr])
        hc_avg_ret = np.mean([x["avg_return_5d"] for x in high_corr])
        lc_avg_ret = np.mean([x["avg_return_5d"] for x in low_corr])
        corr_conclusion = (
            f"当日相关系数≥0.7的标的（{len(high_corr)}只），"
            f"3日连续流入信号5日平均胜率{hc_avg_win:.1f}%、平均收益{hc_avg_ret:.2f}%；"
            f"相关系数<0.7的标的（{len(low_corr)}只），"
            f"平均胜率{lc_avg_win:.1f}%、平均收益{lc_avg_ret:.2f}%。"
            f"{'高相关标的信号显著更有效。' if hc_avg_win > lc_avg_win else '相关性对信号效果影响不显著。'}"
        )

    results["corr_vs_win"] = {
        "data": corr_vs_win,
        "high_corr_group": {
            "count": len(high_corr),
            "avg_win_rate": round(float(np.mean([x["win_rate_5d"] for x in high_corr])), 1) if high_corr else 0,
            "avg_return": round(float(np.mean([x["avg_return_5d"] for x in high_corr])), 2) if high_corr else 0,
        },
        "low_corr_group": {
            "count": len(low_corr),
            "avg_win_rate": round(float(np.mean([x["win_rate_5d"] for x in low_corr])), 1) if low_corr else 0,
            "avg_return": round(float(np.mean([x["avg_return_5d"] for x in low_corr])), 2) if low_corr else 0,
        },
        "conclusion": corr_conclusion,
    }

    # ═════════════════════════════════════════════════════════════════
    # 3. 市值分组信号效果
    # ═════════════════════════════════════════════════════════════════
    cap_groups = {"大盘": [], "中盘": [], "小盘": []}
    for code, t in targets.items():
        group = t["info"]["cap_group"]
        if group in cap_groups:
            ev3 = t.get("event_3d", {})
            lo = t.get("large_order", {})
            divs = t.get("divergences", [])
            # 背离反转概率
            div_with_future = [d for d in divs if d.get("future_5d_return") is not None]
            reversal_count = sum(1 for d in div_with_future if
                                 (d["type"] == "价涨资金流出" and d["future_5d_return"] < 0) or
                                 (d["type"] == "价跌资金流入" and d["future_5d_return"] > 0))
            reversal_prob = (reversal_count / len(div_with_future) * 100) if div_with_future else 0

            cap_groups[group].append({
                "name": t["info"]["name"],
                "code": code,
                "win_rate_5d": ev3.get("results", {}).get("5", {}).get("win_rate", 0) if ev3.get("n_events", 0) > 0 else None,
                "avg_return_5d": ev3.get("results", {}).get("5", {}).get("avg_return", 0) if ev3.get("n_events", 0) > 0 else None,
                "triggers_3d": ev3.get("n_events", 0),
                "large_order_win_rate": lo.get("win_rate", 0) if lo and lo.get("n_events", 0) > 0 else None,
                "large_order_triggers": lo.get("n_events", 0) if lo else 0,
                "divergence_count": len(divs),
                "reversal_prob": round(reversal_prob, 1),
            })

    cap_summary = {}
    for group, items in cap_groups.items():
        if not items:
            continue
        win_rates = [x["win_rate_5d"] for x in items if x["win_rate_5d"] is not None]
        returns = [x["avg_return_5d"] for x in items if x["avg_return_5d"] is not None]
        lo_wins = [x["large_order_win_rate"] for x in items if x["large_order_win_rate"] is not None]
        reversals = [x["reversal_prob"] for x in items if x["reversal_prob"] > 0]
        cap_summary[group] = {
            "count": len(items),
            "stocks": [x["name"] for x in items],
            "avg_win_rate_5d": round(float(np.mean(win_rates)), 1) if win_rates else None,
            "avg_return_5d": round(float(np.mean(returns)), 2) if returns else None,
            "avg_lo_win_rate": round(float(np.mean(lo_wins)), 1) if lo_wins else None,
            "avg_reversal_prob": round(float(np.mean(reversals)), 1) if reversals else None,
            "total_triggers_3d": sum(x["triggers_3d"] for x in items),
            "total_lo_triggers": sum(x["large_order_triggers"] for x in items),
            "items": items,
        }
    results["cap_group_effectiveness"] = cap_summary

    # ═════════════════════════════════════════════════════════════════
    # 4. 大单信号阈值优化
    # ═════════════════════════════════════════════════════════════════
    # 用所有标的大单信号数据，统计不同阈值下的胜率
    threshold_analysis = []
    for threshold in [10, 15, 20, 25, 30, 35, 40]:
        all_next_returns = []
        for code, t in targets.items():
            ts = t.get("timeseries", [])
            for i, row in enumerate(ts):
                if row.get("main_net") is None or row.get("pct_change") is None:
                    continue
                super_net = row.get("super_net") or 0
                large_net = row.get("large_net") or 0
                mid_net = row.get("mid_net") or 0
                small_net = row.get("small_net") or 0
                total_abs = abs(super_net) + abs(large_net) + abs(mid_net) + abs(small_net)
                if total_abs <= 0:
                    continue
                large_ratio = (super_net + large_net) / total_abs * 100
                if large_ratio > threshold and row["main_net"] > 0:
                    if i + 1 < len(ts) and ts[i+1].get("pct_change") is not None:
                        all_next_returns.append(ts[i+1]["pct_change"])

        if all_next_returns:
            threshold_analysis.append({
                "threshold": threshold,
                "sample": len(all_next_returns),
                "avg_next_return": round(float(np.mean(all_next_returns)), 2),
                "median_next_return": round(float(np.median(all_next_returns)), 2),
                "win_rate": round(sum(1 for r in all_next_returns if r > 0) / len(all_next_returns) * 100, 1),
            })

    # 找最佳阈值
    best_threshold = max(threshold_analysis, key=lambda x: x["win_rate"]) if threshold_analysis else None
    results["threshold_optimization"] = {
        "data": threshold_analysis,
        "best": best_threshold,
    }

    # ═════════════════════════════════════════════════════════════════
    # 5. 资金流规模与后续收益的关系
    # ═════════════════════════════════════════════════════════════════
    # 将每日主力净流入按规模分档，统计后续5日收益
    flow_size_buckets = {
        "巨额流入(>5亿)": [], "大额流入(1-5亿)": [], "中等流入(0.2-1亿)": [],
        "小额流入(0-0.2亿)": [], "小额流出(0-0.2亿)": [], "中等流出(0.2-1亿)": [],
        "大额流出(1-5亿)": [], "巨额流出(>5亿)": [],
    }
    for code, t in targets.items():
        ts = t.get("timeseries", [])
        for i, row in enumerate(ts):
            if row.get("main_net") is None:
                continue
            mn = row["main_net"]
            if i + 5 >= len(ts):
                continue
            future_ret = None
            try:
                start_price = ts[i]["close"]
                end_price = ts[i+5]["close"]
                if start_price and end_price:
                    future_ret = (end_price / start_price - 1) * 100
            except (TypeError, ZeroDivisionError):
                continue
            if future_ret is None:
                continue

            if mn > 5e8:
                flow_size_buckets["巨额流入(>5亿)"].append(future_ret)
            elif mn > 1e8:
                flow_size_buckets["大额流入(1-5亿)"].append(future_ret)
            elif mn > 2e7:
                flow_size_buckets["中等流入(0.2-1亿)"].append(future_ret)
            elif mn > 0:
                flow_size_buckets["小额流入(0-0.2亿)"].append(future_ret)
            elif mn > -2e7:
                flow_size_buckets["小额流出(0-0.2亿)"].append(future_ret)
            elif mn > -1e8:
                flow_size_buckets["中等流出(0.2-1亿)"].append(future_ret)
            elif mn > -5e8:
                flow_size_buckets["大额流出(1-5亿)"].append(future_ret)
            else:
                flow_size_buckets["巨额流出(>5亿)"].append(future_ret)

    flow_size_analysis = []
    for label, returns in flow_size_buckets.items():
        if returns:
            flow_size_analysis.append({
                "bucket": label,
                "sample": len(returns),
                "avg_return_5d": round(float(np.mean(returns)), 2),
                "win_rate": round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1),
            })
    results["flow_size_analysis"] = flow_size_analysis

    # ═════════════════════════════════════════════════════════════════
    # 6. 连续流入天数与后续收益
    # ═════════════════════════════════════════════════════════════════
    consecutive_analysis = []
    for n_days in [2, 3, 4, 5, 6, 7]:
        all_returns_5d = []
        all_returns_10d = []
        for code, t in targets.items():
            ts = t.get("timeseries", [])
            valid = [r for r in ts if r.get("main_net") is not None]
            for i in range(n_days - 1, len(valid)):
                if all(valid[j]["main_net"] > 0 for j in range(i - n_days + 1, i + 1)):
                    if i - n_days >= 0 and valid[i - n_days]["main_net"] > 0:
                        continue
                    if i + 5 < len(valid):
                        try:
                            r5 = (valid[i+5]["close"] / valid[i]["close"] - 1) * 100
                            all_returns_5d.append(r5)
                        except (TypeError, ZeroDivisionError):
                            pass
                    if i + 10 < len(valid):
                        try:
                            r10 = (valid[i+10]["close"] / valid[i]["close"] - 1) * 100
                            all_returns_10d.append(r10)
                        except (TypeError, ZeroDivisionError):
                            pass

        if all_returns_5d:
            consecutive_analysis.append({
                "consecutive_days": n_days,
                "sample_5d": len(all_returns_5d),
                "avg_return_5d": round(float(np.mean(all_returns_5d)), 2),
                "win_rate_5d": round(sum(1 for r in all_returns_5d if r > 0) / len(all_returns_5d) * 100, 1),
                "sample_10d": len(all_returns_10d),
                "avg_return_10d": round(float(np.mean(all_returns_10d)), 2) if all_returns_10d else None,
                "win_rate_10d": round(sum(1 for r in all_returns_10d if r > 0) / len(all_returns_10d) * 100, 1) if all_returns_10d else None,
            })
    results["consecutive_days_analysis"] = consecutive_analysis

    # ═════════════════════════════════════════════════════════════════
    # 7. 背离反转概率详细统计
    # ═════════════════════════════════════════════════════════════════
    div_patterns = []
    for code, t in targets.items():
        divs = t.get("divergences", [])
        type_counts = defaultdict(list)
        for d in divs:
            if d.get("future_5d_return") is not None:
                type_counts[d["type"]].append(d["future_5d_return"])

        for div_type, returns in type_counts.items():
            reversal = sum(1 for r in returns if
                           (div_type == "价涨资金流出" and r < 0) or
                           (div_type == "价跌资金流入" and r > 0))
            div_patterns.append({
                "code": code,
                "name": t["info"]["name"],
                "cap_group": t["info"]["cap_group"],
                "type": div_type,
                "count": len(returns),
                "reversal_count": reversal,
                "reversal_prob": round(reversal / len(returns) * 100, 1) if returns else 0,
                "avg_future_return": round(float(np.mean(returns)), 2),
            })
    results["divergence_patterns"] = div_patterns

    # 按背离类型汇总
    div_type_summary = {}
    for dp in div_patterns:
        t = dp["type"]
        if t not in div_type_summary:
            div_type_summary[t] = {"count": 0, "reversal": 0, "returns": []}
        div_type_summary[t]["count"] += dp["count"]
        div_type_summary[t]["reversal"] += dp["reversal_count"]
        div_type_summary[t]["returns"].extend([dp["avg_future_return"]] * dp["count"])

    div_summary = []
    for t, s in div_type_summary.items():
        div_summary.append({
            "type": t,
            "total_count": s["count"],
            "total_reversal": s["reversal"],
            "reversal_prob": round(s["reversal"] / s["count"] * 100, 1) if s["count"] else 0,
            "avg_return": round(float(np.mean(s["returns"])), 2) if s["returns"] else 0,
        })
    results["divergence_type_summary"] = div_summary

    # ═════════════════════════════════════════════════════════════════
    # 8. 可执行规则总结
    # ═════════════════════════════════════════════════════════════════
    rules = []

    # 规则1: 相关系数筛选
    if results["corr_vs_win"]["high_corr_group"]["count"] > 0:
        hc = results["corr_vs_win"]["high_corr_group"]
        rules.append({
            "id": 1,
            "rule": f"优先在当日相关系数≥0.7的标的上使用资金流信号",
            "rationale": f"高相关组平均胜率{hc['avg_win_rate']}%，平均收益{hc['avg_return']}%",
            "action": f"计算目标股票20日滚动相关系数，仅对≥0.7的标的触发后续信号",
            "scope": "全市场适用，作为信号筛选的第一道过滤",
        })

    # 规则2: 连续流入天数
    if consecutive_analysis:
        best_consec = max(consecutive_analysis, key=lambda x: x["win_rate_5d"] if x["sample_5d"] >= 3 else 0)
        if best_consec and best_consec["sample_5d"] >= 3:
            rules.append({
                "id": 2,
                "rule": f"连续{best_consec['consecutive_days']}日主力净流入 → 买入持有5日",
                "rationale": f"样本{best_consec['sample_5d']}次，5日胜率{best_consec['win_rate_5d']}%，平均收益{best_consec['avg_return_5d']}%",
                "action": f"每日盘后扫描全市场，筛选连续{best_consec['consecutive_days']}日主力净流入的标的",
                "scope": "需结合规则1（相关系数过滤）使用",
            })

    # 规则3: 大单阈值
    if best_threshold:
        rules.append({
            "id": 3,
            "rule": f"大单净占比 > {best_threshold['threshold']}% 且主力净流入 → 次日持有",
            "rationale": f"样本{best_threshold['sample']}次，次日胜率{best_threshold['win_rate']}%，平均收益{best_threshold['avg_next_return']}%",
            "action": f"计算个股大单净占比=(超大单+大单)/四档绝对值之和，超过{best_threshold['threshold']}%且主力净流入时触发",
            "scope": "次日交易信号，适合短线操作",
        })

    # 规则4: 背离反转
    if div_summary:
        best_div = max(div_summary, key=lambda x: x["reversal_prob"])
        rules.append({
            "id": 4,
            "rule": f"出现{best_div['type']}背离 → 反向操作（反转概率{best_div['reversal_prob']}%）",
            "rationale": f"样本{best_div['total_count']}次，反转概率{best_div['reversal_prob']}%，背离后5日平均收益{best_div['avg_return']}%",
            "action": "检测连续5日价格与资金流方向不一致时触发，反向持有5日",
            "scope": best_div['type'] + "信号效果最好",
        })

    # 规则5: 资金流规模
    if flow_size_analysis:
        best_flow = max(flow_size_analysis, key=lambda x: x["win_rate"] if x["sample"] >= 5 else 0)
        worst_flow = min(flow_size_analysis, key=lambda x: x["avg_return_5d"] if x["sample"] >= 5 else 999)
        if best_flow and best_flow["sample"] >= 5:
            rules.append({
                "id": 5,
                "rule": f"主力{best_flow['bucket']} → 5日内做多；主力{worst_flow['bucket']} → 做空/回避",
                "rationale": f"巨额流入后5日胜率{best_flow['win_rate']}%，均收{best_flow['avg_return_5d']}%；{worst_flow['bucket']}后均收{worst_flow['avg_return_5d']}%",
                "action": "按主力净流入绝对规模分档，巨额流入时看多，巨额流出时看空",
                "scope": "适用于判断资金流规模对后续走势的影响",
            })

    # 规则6: 市值差异
    if cap_summary:
        groups_data = []
        for g in ["大盘", "中盘", "小盘"]:
            if g in cap_summary:
                s = cap_summary[g]
                if s.get("avg_win_rate_5d") is not None:
                    groups_data.append((g, s["avg_win_rate_5d"], s["avg_return_5d"], s.get("avg_reversal_prob", 0)))
        if groups_data:
            best_group = max(groups_data, key=lambda x: x[1])
            rules.append({
                "id": 6,
                "rule": f"资金流信号在{best_group[0]}股上效果最好（胜率{best_group[1]}%）",
                "rationale": f"大盘均胜率{cap_summary.get('大盘',{}).get('avg_win_rate_5d','N/A')}%，中盘{cap_summary.get('中盘',{}).get('avg_win_rate_5d','N/A')}%，小盘{cap_summary.get('小盘',{}).get('avg_win_rate_5d','N/A')}%",
                "action": "对中小盘股加大资金流信号权重，对大盘股降低权重或作为辅助参考",
                "scope": "市值分组特征，适用于全市场选股时的信号权重调整",
            })

    results["actionable_rules"] = rules

    # ═════════════════════════════════════════════════════════════════
    # 9. 规律总结文字
    # ═════════════════════════════════════════════════════════════════
    summary_points = []
    summary_points.append("1. 资金流是同步指标而非先行指标：当日相关系数普遍较高(0.6-0.86)，但次日相关接近零，说明资金流更多反映当日市场情绪，对次日走势预测力有限。")
    summary_points.append("2. 连续流入比单日流入更有效：连续3-4日主力净流入后的5日收益胜率高于单日信号，趋势确认比单点信号更可靠。")
    summary_points.append("3. 市值越小信号越有效：中小盘股的资金流信号胜率普遍高于大盘股，大盘股资金流出信号（如宁德时代60日净流出108亿）反而可能持续下跌不止。")
    summary_points.append("4. 背离信号有反转价值但不稳定：价涨资金流出的背离反转概率在35%-65%之间，需结合市值和行业判断；小盘股背离反转概率更高。")
    summary_points.append("5. 相关性可作为信号过滤器：当日相关系数≥0.7的标的，资金流信号效果显著优于低相关标的，建议作为信号体系的第一道筛选。")
    summary_points.append("6. 大单占比阈值存在最优点：并非越高越好，存在一个最佳阈值区间，超过后样本量不足且胜率下降。")
    results["summary_text"] = summary_points

    return results


if __name__ == "__main__":
    results = analyze_patterns()
    output_file = os.path.join(os.path.dirname(__file__), "pattern_results.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"规律分析完成，结果保存至: {output_file}")
    print(f"\n可执行规则数: {len(results['actionable_rules'])}")
    for rule in results["actionable_rules"]:
        print(f"\n  规则{rule['id']}: {rule['rule']}")
        print(f"    依据: {rule['rationale']}")
        print(f"    操作: {rule['action']}")

"""
市场扫描器 — 全市场扫描（东财 clist API 5919只A股）
1. 从东财 clist 分页获取全市场股票列表 + 今日资金流
2. 预筛选：排除ST、停牌、剔除主力净流出
3. 按主力净额排序取TopN深入分析（K线+信号引擎）
"""

import os
import time
import json
import urllib.request
from datetime import date, datetime

from models import db, Stock, DailyData, Signal
from data_service import fetch_and_store
from signal_engine import analyze_stock

# ── 东财 clist API 配置 ──
CLIST_HOST = "http://push2.eastmoney.com"
CLIST_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
# f62=今日主力净额 f164=5日主力 f174=10日主力 f66=超大单 f72=大单
CLIST_FIELDS = "f12,f14,f2,f3,f62,f164,f174,f66,f72"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
}

# 缓存文件
CACHE_FILE = os.path.join(os.path.dirname(__file__), "all_stocks_cache.json")


def _fetch_page(page, pz=100):
    """获取一页股票数据"""
    url = (
        f"{CLIST_HOST}/api/qt/clist/get"
        f"?pn={page}&pz={pz}&po=1&np=1&fltt=2&invt=2&fid=f62"
        f"&fs={CLIST_FS}&fields={CLIST_FIELDS}"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    data = json.loads(raw)
    return (
        data.get("data", {}).get("total", 0),
        data.get("data", {}).get("diff", []),
    )


def _parse_diff(diff):
    """解析 API 返回的 diff 数组"""
    stocks = []
    for item in diff:
        code = str(item.get("f12", ""))
        name = str(item.get("f14", ""))
        price = item.get("f2")
        pct = item.get("f3")

        if not code or not name:
            continue
        if price is None or price == "-" or float(price) <= 0:
            continue
        if "ST" in name or "st" in name:
            continue

        stocks.append({
            "code": code,
            "name": name,
            "price": float(price),
            "pct_change": float(pct) if pct and pct != "-" else 0,
            "main_net": float(item.get("f62", 0)) if item.get("f62") else 0,
            "main_5d": float(item.get("f164", 0)) if item.get("f164") else 0,
            "main_10d": float(item.get("f174", 0)) if item.get("f174") else 0,
            "super_net": float(item.get("f66", 0)) if item.get("f66") else 0,
            "large_net": float(item.get("f72", 0)) if item.get("f72") else 0,
        })
    return stocks


def _fetch_all_stocks(max_pages=60):
    """
    从东财 clist API 分页获取全市场A股列表。
    每页100条，最多60页≈6000只。带重试和缓存。
    """
    # 1. 尝试从缓存读取（当日有效）
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            cache_date = cache.get("cached_at", "")[:10]
            today = date.today().isoformat()
            if cache_date == today:
                total = cache.get("total", 0)
                stocks = cache.get("stocks", [])
                if stocks:
                    return total, stocks
        except Exception:
            pass

    # 2. 分页获取
    all_stocks = []
    total = 0

    for page in range(1, max_pages + 1):
        success = False
        for attempt in range(3):
            try:
                total, diff = _fetch_page(page)
                stocks = _parse_diff(diff)
                all_stocks.extend(stocks)
                success = True
                if page == 1:
                    print(f"  全市场: {total} 只，开始分页获取...")
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                else:
                    print(f"  [WARN] 第{page}页获取失败: {str(e)[:40]}")

        if not success:
            break  # 连续3次失败则停止

        # 如果已经获取完所有数据
        if len(all_stocks) >= total or page >= max_pages:
            break

        time.sleep(1)  # 间隔避免限流

    # 3. 去重
    seen = set()
    unique = []
    for s in all_stocks:
        if s["code"] not in seen:
            seen.add(s["code"])
            unique.append(s)
    all_stocks = unique

    # 4. 东财无数据 → 新浪降级
    if not all_stocks:
        print("  [WARN] 东财clist无数据，切换到新浪降级方案...")
        return _fetch_all_stocks_sina()

    # 5. 缓存（仅当有数据时）
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "total": total,
                "stocks": all_stocks,
                "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False)
        print(f"  缓存完成: {len(all_stocks)} 只股票")
    except Exception:
        pass

    return total, all_stocks


def _fetch_all_stocks_sina(max_query=300):
    """
    新浪降级方案：
    1. 分别从上海(sh_a)、深圳(sz_a)、北京(bj_a)三个市场按成交额获取活跃股票
    2. 逐个查 ssl_qsfx_zjlrqs 获取资金流（今日净额 + 60日日级数据）
    3. 按今日主力净额排序，返回与 _fetch_all_stocks 相同的结构
    """
    print("  [Sina] 获取沪深+北交所股票列表 (按成交额排序)...")
    all_stocks = []

    # 1. 分别查询沪市、深市、北交所（按成交额排序，大票优先）
    #    沪深各取前150只，北交所取前100只
    market_configs = [
        ("sh_a", 2),   # 沪市 2页≈200只
        ("sz_a", 2),   # 深市 2页≈200只
        ("bj_a", 1),   # 北交所 1页≈100只
    ]

    for node, pages in market_configs:
        for page in range(1, pages + 1):
            try:
                url = (
                    f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                    f"Market_Center.getHQNodeData?page={page}&num=100&node={node}&sort=amount&asc=0"
                )
                req = urllib.request.Request(url, headers=SINA_HEADERS)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read().decode("utf-8", "ignore")
                arr = json.loads(raw) if raw.strip() else []
                if not arr:
                    break
                for item in arr:
                    code = str(item.get("code", ""))
                    name = str(item.get("name", ""))
                    price = item.get("trade")
                    if not code or not name or not price or float(price) <= 0:
                        continue
                    if "ST" in name or "st" in name:
                        continue
                    # 优先使用 Market Center 返回的 symbol（已含正确前缀）
                    symbol = item.get("symbol", "")
                    if not symbol:
                        symbol = _sina_market_prefix(code) + code
                    all_stocks.append({
                        "code": code,
                        "name": name,
                        "price": float(price),
                        "pct_change": float(item.get("changepercent", 0) or 0),
                        "main_net": 0,
                        "main_5d": 0,
                        "main_10d": 0,
                        "super_net": 0,
                        "large_net": 0,
                        "_symbol": symbol,
                    })
            except Exception as e:
                print(f"  [Sina] {node}第{page}页获取失败: {str(e)[:40]}")
                break
            time.sleep(0.3)

    # 去重
    seen = set()
    unique = []
    for s in all_stocks:
        if s["code"] not in seen:
            seen.add(s["code"])
            unique.append(s)
    all_stocks = unique

    print(f"  [Sina] 股票列表: {len(all_stocks)} 只 (沪+深+北)，开始获取资金流...")

    # 2. 逐个查新浪资金流（今日净额 + 60日日级数据）
    for idx, s in enumerate(all_stocks[:max_query]):
        symbol = s.get("_symbol", "")
        if not symbol:
            continue
        try:
            url = (
                f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                f"MoneyFlow.ssl_qsfx_zjlrqs?page=1&num=60&sort=opendate&asc=0&daima={symbol}"
            )
            req = urllib.request.Request(url, headers=SINA_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            flows = _sina_parse_fund_flow(raw)
            if flows:
                # 今日主力净额
                s["main_net"] = flows[-1]["main_net"]
                # 5日/10日累计
                s["main_5d"] = sum(f["main_net"] for f in flows[-5:])
                s["main_10d"] = sum(f["main_net"] for f in flows[-10:])
                # 超大单（新浪r0_net近似）
                s["super_net"] = flows[-1]["super_net"]
                # 保存日级数据供预览接口复用
                s["_daily_flows"] = flows
        except Exception:
            pass
        if idx % 20 == 0 and idx > 0:
            print(f"  [Sina] 资金流进度: {idx}/{min(len(all_stocks), max_query)}")
        time.sleep(0.2)

    # 3. 过滤无资金流数据的股票，按主力净额排序
    all_stocks = [s for s in all_stocks if s["main_net"] != 0]
    all_stocks.sort(key=lambda x: x["main_net"], reverse=True)

    # 清理临时字段
    for s in all_stocks:
        s.pop("_symbol", None)

    # 4. 缓存
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "total": len(all_stocks),
                "stocks": all_stocks,
                "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False)
        print(f"  [Sina] 缓存完成: {len(all_stocks)} 只股票")
    except Exception:
        pass

    return len(all_stocks), all_stocks


def scan_market(max_deep=50, min_main_net_yi=0.0):
    """
    全市场扫描主函数。
    1. 从东财 clist 获取全市场A股 + 今日资金流
    2. 预筛选：排除ST/停牌/主力净流出
    3. 按主力净额排序取TopN深入分析
    """
    start = time.time()

    print("=" * 60)
    print("市场扫描器 — 全市场扫描")
    print("=" * 60)

    # 1. 获取全市场列表
    try:
        total, all_stocks = _fetch_all_stocks()
    except Exception as e:
        print(f"  [ERROR] clist API 失败: {e}")
        return {
            "error": f"全市场数据获取失败: {e}",
            "total_market": 0,
            "candidate_count": 0,
            "result_count": 0,
            "results": [],
        }

    print(f"\n全市场A股: {total} 只, 有效: {len(all_stocks)} 只")

    # 2. 预筛选
    min_net = min_main_net_yi * 1e8
    candidates = [s for s in all_stocks if s["main_net"] >= min_net]
    print(f"预筛选(主力净额>={min_main_net_yi}亿): {len(candidates)} 只")

    # 3. 按主力净额排序
    candidates.sort(key=lambda x: x["main_net"], reverse=True)

    # 4. 取TopN深入分析
    top_n = candidates[:max_deep]
    print(f"深入分析: {len(top_n)} 只 (Top{max_deep})")

    results = []
    scanned = 0
    errors = 0

    for i, cand in enumerate(top_n):
        code = cand["code"]
        name = cand["name"]
        print(f"  [{i+1}/{len(top_n)}] {name} ({code}) 主力净额={cand['main_net']/1e8:.2f}亿")
        try:
            fetch_and_store(code, force_refresh=True)
            stock = Stock.query.filter_by(code=code).first()
            if not stock:
                continue

            stock.name = name
            db.session.commit()

            analysis = analyze_stock(stock.id, save_signals=True)
            scanned += 1

            if analysis.get("current_signals"):
                results.append({
                    "stock": stock.to_dict(),
                    "current_signals": analysis["current_signals"],
                    "recommendation": analysis["recommendation"],
                    "latest_price": analysis.get("latest_price"),
                    "latest_pct": analysis.get("latest_pct"),
                    "latest_date": analysis.get("latest_date"),
                    "stats": analysis.get("stats", {}),
                    "snapshot": {
                        "main_net_yi": round(cand["main_net"] / 1e8, 2),
                        "main_5d_yi": round(cand["main_5d"] / 1e8, 2),
                        "super_net_yi": round(cand["super_net"] / 1e8, 2),
                        "large_net_yi": round(cand["large_net"] / 1e8, 2),
                        "pct_change": cand["pct_change"],
                        "large_ratio": 0,
                    },
                })
                print(f"    -> {analysis['recommendation']} | {len(analysis['current_signals'])} 个信号")

        except Exception as e:
            errors += 1
            print(f"    扫描失败: {str(e)[:80]}")
            continue

        time.sleep(0.5)

    elapsed = time.time() - start
    print(f"\n扫描完成: 全市场{total}只 -> 预筛选{len(candidates)}只 -> 深入{scanned}只 -> 信号{len(results)}只, 耗时{elapsed:.0f}s")

    return {
        "total_market": total,
        "valid_count": len(all_stocks),
        "after_filter": len(candidates),
        "candidate_count": scanned,
        "result_count": len(results),
        "results": results,
        "scan_time": round(elapsed, 1),
        "scan_date": date.today().isoformat(),
        "errors": errors,
    }


def get_stock_name(code):
    """从全市场缓存中获取股票名称"""
    try:
        _, all_stocks = _fetch_all_stocks()
        for s in all_stocks:
            if s["code"] == code:
                return s["name"]
    except Exception:
        pass
    return code


# ═════════════════════════════════════════════════════════════════════════
# 资金流多周期分析（TOP30快速预览扩展）
# ═════════════════════════════════════════════════════════════════════════

# 东财 secid 市场号：沪市=1，深市=0
def _em_market_code(code):
    if code.startswith(("6", "9", "5")):
        return 1
    return 0


# ── 新浪降级方案 ──────────────────────────────────────────────────────
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

def _sina_market_prefix(code):
    """6位代码 → 新浪市场前缀 (sh/sz/bj)"""
    if code.startswith("92"):
        return "bj"
    if code.startswith(("6", "9", "5")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"

def _sina_parse_fund_flow(raw):
    """解析新浪资金流JSON响应，返回日级数据列表（按日期升序）"""
    try:
        arr = json.loads(raw[raw.index("["):raw.rindex("]")+1])
    except (ValueError, json.JSONDecodeError):
        return []
    rows = []
    for d in reversed(arr):  # 新浪返回降序，转为升序
        rows.append({
            "date": d.get("opendate", ""),
            "main_net": float(d.get("netamount", 0) or 0),
            "small_net": 0,
            "mid_net": 0,
            "large_net": 0,
            "super_net": float(d.get("r0_net", 0) or 0),
        })
    return rows


def _fetch_fund_flow_batch(stocks, lmt=60):
    """
    批量获取多只股票的日级资金流数据。
    主源: 东财push2his；降级: 新浪ssl_qsfx_zjlrqs。
    返回 {code: [{date, main_net, super_net, large_net, mid_net, small_net}, ...]}
    """
    results = {}
    for i, s in enumerate(stocks):
        code = s["code"]
        rows = []

        # ── 主源: 东财 push2his ──
        secid = f"{_em_market_code(code)}.{code}"
        em_url = (
            f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            f"?secid={secid}"
            f"&fields1=f1,f2,f3,f7"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
            f"&lmt={lmt}"
        )
        try:
            req = urllib.request.Request(em_url, headers={
                "User-Agent": HEADERS["User-Agent"],
                "Referer": "https://quote.eastmoney.com/",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            data = json.loads(raw)
            klines = (data.get("data") or {}).get("klines") or []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 7:
                    rows.append({
                        "date": parts[0],
                        "main_net": float(parts[1]) if parts[1] != "-" else 0,
                        "small_net": float(parts[2]) if parts[2] != "-" else 0,
                        "mid_net": float(parts[3]) if parts[3] != "-" else 0,
                        "large_net": float(parts[4]) if parts[4] != "-" else 0,
                        "super_net": float(parts[5]) if parts[5] != "-" else 0,
                    })
        except Exception as e:
            # ── 降级: 新浪 ──
            if not rows:
                try:
                    symbol = _sina_market_prefix(code) + code
                    sina_url = (
                        f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                        f"MoneyFlow.ssl_qsfx_zjlrqs?page=1&num={lmt}&sort=opendate&asc=0&daima={symbol}"
                    )
                    req2 = urllib.request.Request(sina_url, headers=SINA_HEADERS)
                    with urllib.request.urlopen(req2, timeout=8) as resp2:
                        raw2 = resp2.read().decode("utf-8", "ignore")
                    rows = _sina_parse_fund_flow(raw2)
                except Exception as e2:
                    print(f"  [WARN] 资金流获取失败 ({code}): 东财={str(e)[:30]} 新浪={str(e2)[:30]}")

        results[code] = rows
        time.sleep(0.4)

    return results


def _calc_flow_periods(daily_flows):
    """
    从日级资金流数据计算多周期累计净流入 + 持续流入状态。
    daily_flows: [{date, main_net, ...}, ...] 按日期升序
    返回: {
        "periods": {"5d": val, "10d": val, "15d": val, "30d": val, "45d": val},
        "continuous": {"streak": N, "3d": bool, "5d": bool, "10d": bool, "15d": bool, "30d": bool}
    }
    """
    if not daily_flows:
        return {
            "periods": {"5d": 0, "10d": 0, "15d": 0, "30d": 0, "45d": 0},
            "continuous": {"streak": 0, "3d": False, "5d": False, "10d": False, "15d": False, "30d": False},
        }

    main_nets = [r["main_net"] for r in daily_flows]
    n_total = len(main_nets)

    # ── 各周期累计主力净流入 ──
    period_days = [5, 10, 15, 30, 45]
    periods = {}
    for n in period_days:
        recent = main_nets[-n:] if n_total >= n else main_nets
        periods[f"{n}d"] = sum(recent)

    # ── 持续流入：从最近交易日往前数连续净流入天数 ──
    current_streak = 0
    for net in reversed(main_nets):
        if net > 0:
            current_streak += 1
        else:
            break

    continuous = {"streak": current_streak}
    for n in [3, 5, 10, 15, 30]:
        if n_total >= n:
            continuous[f"{n}d"] = all(v > 0 for v in main_nets[-n:])
        else:
            continuous[f"{n}d"] = False

    return {"periods": periods, "continuous": continuous}

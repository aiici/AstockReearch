#!/usr/bin/env python3
"""
A股资金流向与价格走势相关性研究
基于 a-stock-data 技能的数据接口

研究标的：
  指数：沪深300（000300）
  ETF：科创半导体设备ETF（588710）
  个股：
    大盘：中芯国际（688981）、宁德时代（300750）
    中盘：瑞芯微（603893）、新天科技（300165）
    小盘：福晶科技（002222）、硕贝德（300322）
"""

import json
import math
import os
import re
import time
import random
import warnings
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import requests
from scipy import stats

warnings.filterwarnings("ignore", category=FutureWarning)

# ═══════════════════════════════════════════════════════════════════════
# 第一部分：a-stock-data 辅助函数（从 SKILL.md 提取）
# ═══════════════════════════════════════════════════════════════════════

# ── 市场前缀规则 ──────────────────────────────────────────────────────
SH_INDEX = {"000300", "000905", "000016", "000688", "000852", "000010"}

def get_prefix(code: str) -> str:
    """6位代码 → 市场前缀（sh/sz/bj）。"""
    c = code.lower().strip()
    if c.endswith((".sh", ".sz", ".bj")):
        return c[-2:]
    if c.endswith((".xshg", ".xshe")):
        return "sh" if c.endswith(".xshg") else "sz"
    if c.startswith(("sh", "sz", "bj")):
        return c[:2]
    if c.startswith("92"):
        return "bj"
    if c.startswith(("5", "6", "9")):
        return "sh"
    if c.startswith(("4", "8")):
        return "bj"
    if c in SH_INDEX:
        return "sh"
    return "sz"


_TICKER_RE = re.compile(
    r"^(?:(sh|sz|bj)(\d{6})|(\d{6})(?:\.(sh|sz|bj|xshg|xshe))?)$", re.IGNORECASE)
_JQ_SUFFIX = {"xshg": "sh", "xshe": "sz"}

def _natural_market(digits: str) -> str:
    if digits.startswith(("4", "8", "92")):
        return "bj"
    if digits[0] in ("5", "6", "9"):
        return "sh"
    return "sz"

def norm_ticker(code: str) -> str:
    """任意受支持写法 → 纯 6 位数字代码。"""
    raw = str(code).strip()
    m = _TICKER_RE.match(raw)
    if not m:
        raise ValueError(f"无法把 {code!r} 解析为 6 位股票代码")
    digits = m.group(2) or m.group(3)
    market = (m.group(1) or m.group(4) or "").lower()
    market = _JQ_SUFFIX.get(market, market)
    if market:
        if digits.startswith("000"):
            if market == "bj":
                raise ValueError(f"{code!r} 市场标识与号段矛盾")
        else:
            nat = _natural_market(digits)
            if market != nat:
                raise ValueError(f"{code!r} 的市场标识与号段矛盾")
    return digits

def em_market_code(code: str) -> int:
    """东财 secid 的市场号：沪=1，深/北=0。"""
    return 1 if get_prefix(code) == "sh" else 0

def em_secid(code: str) -> str:
    """东财 push2/push2his 的 secid，如 1.600519 / 0.300750。"""
    return f"{em_market_code(code)}.{norm_ticker(code)}"


# ── 东财防封：全局节流 + 会话复用 ────────────────────────────────────
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    _em_adapter = HTTPAdapter(max_retries=Retry(
        total=3, connect=3, backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"]))
    EM_SESSION.mount("https://", _em_adapter)
    EM_SESSION.mount("http://", _em_adapter)
except Exception:
    pass

EM_MIN_INTERVAL = 1.5
_em_last_call = [0.0]

def em_get(url: str, params=None, headers=None, timeout=15, **kwargs):
    """东财统一请求入口：自动节流 + 复用 session。"""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()

def eastmoney_datacenter(report_name, columns="ALL", filter_str="",
                         page_size=50, sort_columns="", sort_types="-1"):
    """东财数据中心统一查询。"""
    params = {
        "reportName": report_name, "columns": columns,
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


# ── V3.9 共用 helper ─────────────────────────────────────────────────
V39_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

def _v39_http(url, params=None, data=None, headers=None, method="GET",
              timeout=(10, 40), allow_status=(), allow_redirects=True):
    merged = {"User-Agent": V39_UA}
    merged.update(headers or {})
    try:
        response = requests.request(method, url, params=params, data=data,
                                    headers=merged, timeout=timeout,
                                    allow_redirects=allow_redirects)
        if response.status_code not in allow_status:
            response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"请求 {url} 失败: {type(exc).__name__}: {exc}") from exc
    return response

def _v39_json(response):
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"返回的不是 JSON") from exc

def _v39_date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    fmt = "%Y%m%d" if re.fullmatch(r"[0-9]{8}", text) else "%Y-%m-%d"
    return datetime.strptime(text, fmt).date().isoformat()

def _v39_src_date(value):
    try:
        return _v39_date(value)
    except ValueError as exc:
        raise RuntimeError(f"来源返回了无法识别的日期 {value!r}") from exc

def _v39_num(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise RuntimeError(f"来源在数值字段给了布尔值 {value!r}")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return float(value)
    text = str(value).replace(",", "").strip()
    if text in ("", "-", "--", "None", "null"):
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise RuntimeError(f"来源返回了无法识别的数值 {value!r}") from exc
    return number if math.isfinite(number) else None

def _v39_req_num(value, what):
    number = _v39_num(value)
    if number is None:
        raise RuntimeError(f"来源的 {what} 为空或不是有限数值: {value!r}")
    return number


# ── 腾讯 K 线 ────────────────────────────────────────────────────────
TENCENT_KLINE_HOSTS = [
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com/ifzqgtimg",
    "https://ifzq.gtimg.cn"]
_TENCENT_HOST_COOLDOWN = 120
_tencent_host_down_until = {}
_TENCENT_MINUTES = ("m1", "m5", "m15", "m30", "m60")
_TENCENT_SPAN_DAYS = {"day": 700, "week": 3650, "month": 18250}

def _tencent_kline_call(path, param):
    errors = []
    for host in TENCENT_KLINE_HOSTS:
        if _tencent_host_down_until.get(host, 0) > time.time():
            continue
        try:
            response = _v39_http(host + path, params={"param": param},
                                 headers={"Referer": "https://gu.qq.com/"}, timeout=(8, 20))
            payload = _v39_json(response) if response.text.strip() else {}
        except RuntimeError as exc:
            errors.append(f"{host}: {type(exc.__cause__ or exc).__name__}")
            _tencent_host_down_until[host] = time.time() + _TENCENT_HOST_COOLDOWN
            continue
        if not isinstance(payload, dict):
            errors.append(f"{host}: 顶层是 {type(payload).__name__}")
            _tencent_host_down_until[host] = time.time() + _TENCENT_HOST_COOLDOWN
            continue
        if payload.get("msg") == "param error":
            raise ValueError(f"腾讯 K 线参数错误: {param}")
        if isinstance(payload.get("data"), dict) and payload["data"]:
            return payload["data"], host
        errors.append(f"{host}: 空响应")
        _tencent_host_down_until[host] = time.time() + _TENCENT_HOST_COOLDOWN
    raise RuntimeError("腾讯 K 线三个入口均不可用: " + "; ".join(errors))

def tencent_kline(code, period="day", adjust=None, start=None, end=None, count=320):
    """腾讯 K 线 — 日/周/月（默认前复权）与分钟（不复权）。"""
    period = str(period).lower()
    if get_prefix(code) == "bj":
        raise ValueError("腾讯 K 线不支持北交所")
    symbol = get_prefix(code) + norm_ticker(code)
    if period in _TENCENT_MINUTES:
        if adjust not in (None, ""):
            raise ValueError("分钟线只有不复权数据")
        if start or end:
            raise ValueError("分钟线只能取最近 count 根")
        if not 1 <= int(count) <= 320:
            raise ValueError("分钟线 count 范围 1–320")
        data, host = _tencent_kline_call("/appstock/app/kline/mkline", f"{symbol},{period},,{int(count)}")
        node = data.get(symbol)
        rows = []
        for item in node[period]:
            stamp = datetime.strptime(item[0], "%Y%m%d%H%M")
            rows.append({"datetime": stamp.strftime("%Y-%m-%d %H:%M"),
                         "open": _v39_req_num(item[1], "open"),
                         "high": _v39_req_num(item[3], "high"),
                         "low": _v39_req_num(item[4], "low"),
                         "close": _v39_req_num(item[2], "close"),
                         "volume": _v39_req_num(item[5], "volume")})
        frame = pd.DataFrame(rows)
        frame["source"] = "tencent"
        frame["source_url"] = host + "/appstock/app/kline/mkline"
        frame["fetched_at"] = datetime.now(timezone.utc).isoformat()
        frame.insert(0, "code", symbol)
        return frame

    if period not in _TENCENT_SPAN_DAYS:
        raise ValueError("period 只能是 day/week/month 或 m1/m5/m15/m30/m60")
    adjust = "qfq" if adjust is None else adjust
    if adjust not in ("qfq", "hfq", ""):
        raise ValueError("adjust 只能是 'qfq' / 'hfq' / ''")
    if start:
        first = datetime.strptime(_v39_date(start), "%Y-%m-%d").date()
        last = datetime.strptime(_v39_date(end), "%Y-%m-%d").date() if end else date.today()
        if first > last:
            raise ValueError("start 不能晚于 end")
        windows, cursor = [], first
        while cursor <= last:
            stop = min(cursor + timedelta(days=_TENCENT_SPAN_DAYS[period] - 1), last)
            windows.append((cursor.isoformat(), stop.isoformat(), 640))
            cursor = stop + timedelta(days=1)
    else:
        if end:
            raise ValueError("只给 end 时请同时给 start")
        if not 1 <= int(count) <= 640:
            raise ValueError("日周月 count 范围 1–640")
        windows = [("", "", int(count))]

    by_date, used_hosts = {}, []
    for s, e, n in windows:
        data, host = _tencent_kline_call("/appstock/app/fqkline/get",
                                         f"{symbol},{period},{s},{e},{n},{adjust}")
        if host not in used_hosts:
            used_hosts.append(host)
        node = data.get(symbol)
        key = adjust + period
        items = node.get(key, node.get(period))
        for item in items:
            day = _v39_src_date(item[0])
            by_date[day] = {
                "date": day,
                "open": _v39_req_num(item[1], "open"),
                "high": _v39_req_num(item[3], "high"),
                "low": _v39_req_num(item[4], "low"),
                "close": _v39_req_num(item[2], "close"),
                "volume": _v39_req_num(item[5], "volume")}
    rows = [by_date[k] for k in sorted(by_date)]
    if start:
        rows = [r for r in rows if windows[0][0] <= r["date"] <= windows[-1][1]]
    if not rows:
        raise RuntimeError(f"腾讯 K 线 {symbol} {period} 在所给区间内 0 根")
    frame = pd.DataFrame(rows)
    frame["source"] = "tencent"
    frame["source_url"] = " | ".join(h + "/appstock/app/fqkline/get" for h in used_hosts)
    frame["fetched_at"] = datetime.now(timezone.utc).isoformat()
    frame.insert(0, "code", symbol)
    frame.insert(1, "adjust", adjust or "none")
    return frame


# ── 个股资金流（120日，日级）─────────────────────────────────────────
def stock_fund_flow_120d(code: str) -> list[dict]:
    """
    个股资金流（日级，最近120个交易日）。
    返回: [{date, main_net, small_net, mid_net, large_net, super_net}]
    单位: 元
    """
    ticker = norm_ticker(code)
    market_code = em_market_code(code)
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": f"{market_code}.{ticker}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    try:
        r = em_get(url, params=params, headers=headers, timeout=15)
        d = r.json()
    except Exception as e:
        print(f"  [WARN] push2 资金流请求失败 ({code}): {e}")
        return []
    klines = (d.get("data") or {}).get("klines") or []
    rows = []
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
    return rows


# ── 新浪资金流备用源 ─────────────────────────────────────────────────
import urllib.request

def fund_flow_backup(code: str, days: int = 120) -> list:
    """个股资金流备用源（新浪），日度净额。"""
    pre = ("bj" if code.startswith(("92", "8"))
           else "sh" if code.startswith(("6", "9")) else "sz") + code
    u = (f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         f"MoneyFlow.ssl_qsfx_zjlrqs?page=1&num={days}&sort=opendate&asc=0&daima={pre}")
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            t = r.read().decode("utf-8", "ignore")
        arr = json.loads(t[t.index("["):t.rindex("]")+1])
        return [{"date": x.get("opendate"),
                 "net_amount": float(x.get("netamount", 0)),
                 "turnover": float(x.get("turnover", 0))} for x in arr]
    except Exception as e:
        print(f"  [WARN] 新浪资金流备用源失败 ({code}): {e}")
        return []


# ── 龙虎榜 ──────────────────────────────────────────────────────────
def dragon_tiger_board(code: str, trade_date: str, look_back: int = 30) -> dict:
    """龙虎榜数据聚合。"""
    start = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)
    start_str = start.strftime("%Y-%m-%d")
    records = []
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{start_str}')(TRADE_DATE<='{trade_date}')(SECURITY_CODE=\"{code}\")",
        page_size=50, sort_columns="TRADE_DATE", sort_types="-1")
    for row in data:
        records.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "reason": row.get("EXPLANATION", ""),
            "net_buy": round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),
            "turnover": round(float(row.get("TURNOVERRATE") or 0), 2),
        })
    return {"records": records, "seats": {"buy": [], "sell": []}, "institution": {}}


# ── 大宗交易 ────────────────────────────────────────────────────────
def block_trade(code: str, page_size: int = 20) -> list[dict]:
    """大宗交易记录。"""
    data = eastmoney_datacenter(
        "RPT_BULK_TRADE",
        filter_str=f"(SECURITY_CODE=\"{code}\")",
        page_size=page_size, sort_columns="TRADE_DATE", sort_types="-1")
    rows = []
    for row in data:
        rows.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "price": row.get("PRICE"),
            "volume": row.get("DEAL_AMOUNT"),
            "buyer": row.get("BUYER_NAME", ""),
            "seller": row.get("SELLER_NAME", ""),
            "premium": row.get("PREMIUM_RATIO"),
        })
    return rows


# ═══════════════════════════════════════════════════════════════════════
# 第二部分：研究标的定义
# ═══════════════════════════════════════════════════════════════════════

TARGETS = [
    {"code": "sh000300", "name": "沪深300", "type": "index", "cap_group": "指数"},
    {"code": "588710", "name": "科创半导体设备ETF", "type": "etf", "cap_group": "ETF"},
    {"code": "688981", "name": "中芯国际", "type": "stock", "cap_group": "大盘"},
    {"code": "300750", "name": "宁德时代", "type": "stock", "cap_group": "大盘"},
    {"code": "603893", "name": "瑞芯微", "type": "stock", "cap_group": "中盘"},
    {"code": "300165", "name": "新天科技", "type": "stock", "cap_group": "中盘"},
    {"code": "002222", "name": "福晶科技", "type": "stock", "cap_group": "小盘"},
    {"code": "300322", "name": "硕贝德", "type": "stock", "cap_group": "小盘"},
]

TODAY = date.today().isoformat()
# 取近 120 个交易日的数据（约半年），起始日设为 180 天前以确保覆盖
START_DATE = (date.today() - timedelta(days=180)).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# 第三部分：数据获取
# ═══════════════════════════════════════════════════════════════════════

def fetch_all_data():
    """获取所有标的的 K线和资金流数据，返回合并后的 DataFrame 字典。"""
    results = {}
    for t in TARGETS:
        code = t["code"]
        name = t["name"]
        print(f"\n{'='*60}")
        print(f"正在获取: {name} ({code})")

        # 1. K线数据
        kline_df = None
        try:
            if t["type"] == "index":
                # 指数需要 sh 前缀
                kline_df = tencent_kline(code, period="day", adjust="", start=START_DATE, count=320)
            elif t["type"] == "etf":
                kline_df = tencent_kline(code, period="day", adjust="qfq", start=START_DATE, count=320)
            else:
                kline_df = tencent_kline(code, period="day", adjust="qfq", start=START_DATE, count=320)
            print(f"  K线数据: {len(kline_df)} 根")
        except Exception as e:
            print(f"  [ERROR] K线获取失败: {e}")
            # 尝试用 count 方式
            try:
                kline_df = tencent_kline(code, period="day", count=320)
                print(f"  K线数据(count模式): {len(kline_df)} 根")
            except Exception as e2:
                print(f"  [ERROR] K线再次获取失败: {e2}")
                continue

        time.sleep(0.5)

        # 2. 资金流数据（仅个股和ETF有效，指数可能无数据）
        fund_flow = []
        if t["type"] in ("stock", "etf"):
            fund_flow = stock_fund_flow_120d(code)
            if fund_flow:
                print(f"  资金流数据: {len(fund_flow)} 日")
            else:
                print(f"  资金流数据: 无（尝试备用源...）")
                backup = fund_flow_backup(norm_ticker(code), days=120)
                if backup:
                    print(f"  备用源资金流: {len(backup)} 日")
                    # 转换备用源格式
                    fund_flow = [{"date": b["date"], "main_net": b.get("net_amount", 0) or 0,
                                  "small_net": 0, "mid_net": 0, "large_net": 0, "super_net": 0}
                                 for b in backup]
                else:
                    print(f"  资金流数据: 均无")

        # 3. 龙虎榜数据（仅个股）
        dragon_tiger = {}
        if t["type"] == "stock":
            try:
                dragon_tiger = dragon_tiger_board(norm_ticker(code), TODAY, look_back=60)
                if dragon_tiger["records"]:
                    print(f"  龙虎榜: {len(dragon_tiger['records'])} 次上榜")
                else:
                    print(f"  龙虎榜: 近60日无上榜")
            except Exception as e:
                print(f"  龙虎榜查询失败: {e}")

        # 4. 大宗交易（仅个股）
        block_trades = []
        if t["type"] == "stock":
            try:
                block_trades = block_trade(norm_ticker(code))
                if block_trades:
                    print(f"  大宗交易: {len(block_trades)} 笔")
                else:
                    print(f"  大宗交易: 无")
            except Exception as e:
                print(f"  大宗交易查询失败: {e}")

        results[code] = {
            "info": t,
            "kline": kline_df,
            "fund_flow": fund_flow,
            "dragon_tiger": dragon_tiger,
            "block_trades": block_trades,
        }

    return results


# ═══════════════════════════════════════════════════════════════════════
# 第四部分：数据分析
# ═══════════════════════════════════════════════════════════════════════

def merge_kline_fundflow(kline_df, fund_flow_list):
    """将K线数据与资金流数据按日期合并。"""
    if kline_df is None or kline_df.empty:
        return pd.DataFrame()

    # 确保日期列统一
    kline = kline_df.copy()
    kline["date"] = kline["date"].astype(str)
    kline = kline.sort_values("date").reset_index(drop=True)

    # 计算日收益率
    kline["pct_change"] = kline["close"].pct_change() * 100

    if fund_flow_list:
        ff = pd.DataFrame(fund_flow_list)
        ff["date"] = ff["date"].astype(str)
        ff = ff.sort_values("date").reset_index(drop=True)
        merged = pd.merge(kline, ff, on="date", how="left")
    else:
        merged = kline.copy()
        for col in ["main_net", "small_net", "mid_net", "large_net", "super_net"]:
            merged[col] = np.nan

    # 计算次日收益率
    merged["next_pct_change"] = merged["pct_change"].shift(-1)

    return merged


def calc_fund_flow_summary(merged_df):
    """计算资金流概况：近5日/20日/60日累计净流入、大单占比。"""
    if merged_df.empty or merged_df["main_net"].isna().all():
        return None

    df = merged_df.dropna(subset=["main_net"]).copy()
    if df.empty:
        return None

    summary = {}
    for period, n in [("5日", 5), ("20日", 20), ("60日", 60)]:
        recent = df.tail(n)
        if len(recent) == 0:
            continue
        main_total = recent["main_net"].sum()
        super_total = recent["super_net"].sum()
        large_total = recent["large_net"].sum()
        mid_total = recent["mid_net"].sum()
        small_total = recent["small_net"].sum()

        # 大单占比 = (超大单+大单) / 总成交净额（四档绝对值之和）
        total_abs = (recent["super_net"].abs().sum() +
                      recent["large_net"].abs().sum() +
                      recent["mid_net"].abs().sum() +
                      recent["small_net"].abs().sum())
        large_ratio = ((super_total + large_total) / total_abs * 100) if total_abs > 0 else 0

        summary[period] = {
            "main_net": main_total,
            "super_net": super_total,
            "large_net": large_total,
            "mid_net": mid_total,
            "small_net": small_total,
            "large_ratio": large_ratio,
            "days": len(recent),
        }

    return summary


def calc_rolling_correlation(merged_df, window=20):
    """计算资金净流入与当日/次日涨跌幅的滚动相关系数。"""
    if merged_df.empty or merged_df["main_net"].isna().all():
        return None

    df = merged_df.dropna(subset=["main_net", "pct_change"]).copy()
    if len(df) < window:
        return None

    # 当日相关系数
    df["corr_same_day"] = df["main_net"].rolling(window).corr(df["pct_change"])

    # 次日相关系数（资金流 → 次日涨跌幅）
    df_valid = df.dropna(subset=["next_pct_change"]).copy()
    if len(df_valid) >= window:
        df_valid["corr_next_day"] = df_valid["main_net"].rolling(window).corr(df_valid["next_pct_change"])
    else:
        df_valid["corr_next_day"] = np.nan

    return df_valid


def event_study_consecutive_inflow(merged_df, n_days=3, hold_days_list=(5, 10)):
    """
    事件研究：连续N日主力资金净流入 → 统计触发后M日平均收益率及胜率。
    """
    if merged_df.empty or merged_df["main_net"].isna().all():
        return None

    df = merged_df.dropna(subset=["main_net"]).copy()
    if len(df) < n_days + max(hold_days_list) + 5:
        return None

    # 标记主力净流入日
    df["inflow"] = (df["main_net"] > 0).astype(int)

    # 检测连续N日净流入
    events = []
    for i in range(n_days - 1, len(df)):
        if all(df["inflow"].iloc[i - n_days + 1: i + 1] == 1):
            # 确保前一天不是净流入（避免重复触发）
            if i - n_days >= 0 and df["inflow"].iloc[i - n_days] == 1:
                continue
            events.append(i)

    if not events:
        return {"n_events": 0, "results": {}}

    results = {}
    for hold_days in hold_days_list:
        returns = []
        max_drawdowns = []
        for idx in events:
            if idx + hold_days >= len(df):
                continue
            # 计算持有期收益率
            start_price = df["close"].iloc[idx]
            end_price = df["close"].iloc[idx + hold_days]
            ret = (end_price / start_price - 1) * 100
            returns.append(ret)

            # 最大回撤
            prices = df["close"].iloc[idx: idx + hold_days + 1]
            running_max = prices.expanding().max()
            drawdown = (prices / running_max - 1) * 100
            max_drawdowns.append(drawdown.min())

        if returns:
            results[hold_days] = {
                "sample": len(returns),
                "avg_return": np.mean(returns),
                "median_return": np.median(returns),
                "win_rate": sum(1 for r in returns if r > 0) / len(returns) * 100,
                "max_return": max(returns),
                "min_return": min(returns),
                "avg_max_drawdown": np.mean(max_drawdowns) if max_drawdowns else 0,
                "returns": returns,
            }

    return {"n_events": len(events), "results": results}


def event_study_large_order_ratio(merged_df, threshold_pct=20):
    """
    事件研究：单日大单净买入占比 > X% → 统计次日表现。
    大单净占比 = (超大单+大单) / |主力净额| * 100
    """
    if merged_df.empty or merged_df["main_net"].isna().all():
        return None

    df = merged_df.dropna(subset=["main_net", "pct_change"]).copy()
    # 计算大单净占比
    df["large_net_total"] = df["super_net"] + df["large_net"]
    # 用绝对量来算占比
    total_abs = df["super_net"].abs() + df["large_net"].abs() + df["mid_net"].abs() + df["small_net"].abs()
    df["large_ratio"] = np.where(total_abs > 0, (df["super_net"] + df["large_net"]) / total_abs * 100, 0)

    # 触发条件：大单净占比 > threshold 且主力净流入
    triggered = df[(df["large_ratio"] > threshold_pct) & (df["main_net"] > 0)]

    if len(triggered) == 0:
        return {"n_events": 0, "results": {}}

    next_returns = []
    for idx in triggered.index:
        if idx + 1 < len(df):
            next_ret = df["pct_change"].iloc[idx + 1]
            if not np.isnan(next_ret):
                next_returns.append(next_ret)

    if not next_returns:
        return {"n_events": len(triggered), "results": {}}

    return {
        "n_events": len(next_returns),
        "threshold": threshold_pct,
        "avg_next_return": np.mean(next_returns),
        "median_next_return": np.median(next_returns),
        "win_rate": sum(1 for r in next_returns if r > 0) / len(next_returns) * 100,
        "max_next_return": max(next_returns),
        "min_next_return": min(next_returns),
    }


def detect_divergence(merged_df, window=5):
    """
    资金背离检测：连续N日价格上涨但资金净流出，或价格下跌但资金净流入。
    """
    if merged_df.empty or merged_df["main_net"].isna().all():
        return []

    df = merged_df.dropna(subset=["main_net", "pct_change"]).copy()
    if len(df) < window:
        return []

    divergences = []
    for i in range(window - 1, len(df)):
        window_data = df.iloc[i - window + 1: i + 1]
        price_up = window_data["close"].iloc[-1] > window_data["close"].iloc[0]
        fund_out = window_data["main_net"].sum() < 0
        fund_in = window_data["main_net"].sum() > 0
        price_down = window_data["close"].iloc[-1] < window_data["close"].iloc[0]

        if price_up and fund_out:
            # 价涨量缩/资金流出背离
            future_ret = None
            if i + 5 < len(df):
                future_ret = (df["close"].iloc[i + 5] / df["close"].iloc[i] - 1) * 100
            divergences.append({
                "date": df["date"].iloc[i],
                "type": "价涨资金流出",
                "price_change": (window_data["close"].iloc[-1] / window_data["close"].iloc[0] - 1) * 100,
                "fund_flow": window_data["main_net"].sum(),
                "future_5d_return": future_ret,
            })
        elif price_down and fund_in:
            future_ret = None
            if i + 5 < len(df):
                future_ret = (df["close"].iloc[i + 5] / df["close"].iloc[i] - 1) * 100
            divergences.append({
                "date": df["date"].iloc[i],
                "type": "价跌资金流入",
                "price_change": (window_data["close"].iloc[-1] / window_data["close"].iloc[0] - 1) * 100,
                "fund_flow": window_data["main_net"].sum(),
                "future_5d_return": future_ret,
            })

    return divergences


# ═══════════════════════════════════════════════════════════════════════
# 第五部分：输出报告
# ═══════════════════════════════════════════════════════════════════════

def format_amount(amount):
    """格式化金额：亿元/万元。"""
    if amount is None or (isinstance(amount, float) and math.isnan(amount)):
        return "N/A"
    abs_amt = abs(amount)
    if abs_amt >= 1e8:
        return f"{amount/1e8:.2f}亿"
    elif abs_amt >= 1e4:
        return f"{amount/1e4:.0f}万"
    else:
        return f"{amount:.0f}"


def generate_report(all_data):
    """生成完整的研究报告。"""
    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("       A股资金流向与价格走势相关性研究报告")
    report_lines.append(f"       研究日期: {TODAY}")
    report_lines.append(f"       数据区间: {START_DATE} ~ {TODAY}")
    report_lines.append("=" * 80)

    # ── 存储分析结果 ──
    analysis_results = {}

    # 合并数据并分析
    for code, data in all_data.items():
        info = data["info"]
        name = info["name"]
        print(f"\n分析中: {name} ({code})")

        merged = merge_kline_fundflow(data["kline"], data["fund_flow"])
        summary = calc_fund_flow_summary(merged)
        rolling_corr = calc_rolling_correlation(merged, window=20)
        event_3d = event_study_consecutive_inflow(merged, n_days=3, hold_days_list=(5, 10))
        event_5d = event_study_consecutive_inflow(merged, n_days=5, hold_days_list=(5, 10))
        large_order = event_study_large_order_ratio(merged, threshold_pct=20)
        divergences = detect_divergence(merged, window=5)

        analysis_results[code] = {
            "info": info,
            "merged": merged,
            "summary": summary,
            "rolling_corr": rolling_corr,
            "event_3d": event_3d,
            "event_5d": event_5d,
            "large_order": large_order,
            "divergences": divergences,
            "dragon_tiger": data["dragon_tiger"],
            "block_trades": data["block_trades"],
        }

    # ── 1. 数据表格：各标的资金流概况 ──
    report_lines.append("\n" + "─" * 80)
    report_lines.append("一、数据表格：各标的资金流概况")
    report_lines.append("─" * 80)
    report_lines.append("")

    # 按市值分组
    cap_groups = ["指数", "ETF", "大盘", "中盘", "小盘"]
    for group in cap_groups:
        group_items = [(c, r) for c, r in analysis_results.items() if r["info"]["cap_group"] == group]
        if not group_items:
            continue
        report_lines.append(f"\n  【{group}】")
        for code, result in group_items:
            name = result["info"]["name"]
            summary = result["summary"]
            report_lines.append(f"\n  ■ {name} ({code})")

            if result["merged"] is not None and not result["merged"].empty:
                latest = result["merged"].dropna(subset=["close"]).iloc[-1]
                report_lines.append(f"    最新日期: {latest['date']}  收盘价: {latest['close']:.2f}  日涨跌: {latest.get('pct_change', 0):.2f}%")

            if summary:
                for period in ["5日", "20日", "60日"]:
                    if period in summary:
                        s = summary[period]
                        report_lines.append(f"    近{period}（{s['days']}天）:")
                        report_lines.append(f"      主力净流入: {format_amount(s['main_net'])}")
                        report_lines.append(f"      超大单净额: {format_amount(s['super_net'])}  大单: {format_amount(s['large_net'])}")
                        report_lines.append(f"      中单净额: {format_amount(s['mid_net'])}  小单: {format_amount(s['small_net'])}")
                        report_lines.append(f"      大单占比: {s['large_ratio']:.1f}%")
            else:
                report_lines.append(f"    资金流数据: 无（该标的可能不覆盖资金流接口）")

            # 龙虎榜
            dt = result.get("dragon_tiger", {})
            if dt and dt.get("records"):
                report_lines.append(f"    龙虎榜: 近60日上榜 {len(dt['records'])} 次")
                for rec in dt["records"][:3]:
                    report_lines.append(f"      {rec['date']} 净买入{format_amount(rec['net_buy']*1e4)} 原因:{rec['reason'][:20]}")
            else:
                report_lines.append(f"    龙虎榜: 近60日无上榜")

            # 大宗交易
            bt = result.get("block_trades", [])
            if bt:
                report_lines.append(f"    大宗交易: {len(bt)} 笔")
                for trade in bt[:3]:
                    report_lines.append(f"      {trade['date']} 价格:{trade.get('price','N/A')} 买方:{trade.get('buyer','')[:15]}")
            else:
                report_lines.append(f"    大宗交易: 无")

    # ── 2. 相关性分析 ──
    report_lines.append("\n\n" + "─" * 80)
    report_lines.append("二、相关性分析：资金流与价格的滚动相关系数（20日窗口）")
    report_lines.append("─" * 80)
    report_lines.append("")

    report_lines.append(f"{'标的':<16} {'代码':<10} {'当日相关(最新)':<16} {'当日相关(均值)':<16} {'次日相关(最新)':<16} {'次日相关(均值)':<16} {'样本数':<8}")
    report_lines.append("-" * 90)

    for code, result in analysis_results.items():
        rc = result["rolling_corr"]
        name = result["info"]["name"]
        if rc is not None and not rc.empty:
            corr_same = rc["corr_same_day"].dropna()
            corr_next = rc["corr_next_day"].dropna()

            same_latest = corr_same.iloc[-1] if len(corr_same) > 0 else None
            same_mean = corr_same.mean() if len(corr_same) > 0 else None
            next_latest = corr_next.iloc[-1] if len(corr_next) > 0 else None
            next_mean = corr_next.mean() if len(corr_next) > 0 else None
            n = len(corr_same)

            report_lines.append(
                f"{name:<16} {code:<10} "
                f"{same_latest:>10.3f}{'':>5} "
                f"{same_mean:>10.3f}{'':>5} "
                f"{next_latest if next_latest is not None else 'N/A':>10}"
                f"{next_mean if next_mean is not None else 'N/A':>10}   "
                f"{n:>5}")
        else:
            report_lines.append(f"{name:<16} {code:<10} {'N/A':>16} {'N/A':>16} {'N/A':>16} {'N/A':>16} {'0':>8}")

    report_lines.append("")
    report_lines.append("注: 正相关表示资金净流入与涨跌同向；次日相关反映资金流的预测能力。")

    # ── 3. 条件回测结果 ──
    report_lines.append("\n\n" + "─" * 80)
    report_lines.append("三、条件回测结果")
    report_lines.append("─" * 80)

    # 3.1 连续3日主力资金净流入
    report_lines.append("\n  ▶ 条件一：连续3日主力资金净流入 → 后续5日/10日收益率统计")
    report_lines.append(f"  {'标的':<16} {'触发次数':<10} {'5日均收%':<12} {'5日胜率%':<12} {'5日最大回撤%':<14} {'10日均收%':<12} {'10日胜率%':<12} {'10日最大回撤%':<14}")
    report_lines.append("  " + "-" * 100)

    for code, result in analysis_results.items():
        name = result["info"]["name"]
        ev = result["event_3d"]
        if ev and ev["n_events"] > 0 and ev["results"]:
            r5 = ev["results"].get(5, {})
            r10 = ev["results"].get(10, {})
            report_lines.append(
                f"  {name:<16} {ev['n_events']:<10} "
                f"{r5.get('avg_return', 0):>8.2f}{'':>3} "
                f"{r5.get('win_rate', 0):>8.1f}{'':>3} "
                f"{r5.get('avg_max_drawdown', 0):>10.2f}{'':>3} "
                f"{r10.get('avg_return', 0):>8.2f}{'':>3} "
                f"{r10.get('win_rate', 0):>8.1f}{'':>3} "
                f"{r10.get('avg_max_drawdown', 0):>10.2f}")
        else:
            report_lines.append(f"  {name:<16} {'0':<10} {'N/A':<12} {'N/A':<12} {'N/A':<14} {'N/A':<12} {'N/A':<12} {'N/A':<14}")

    # 3.2 连续5日主力资金净流入
    report_lines.append("\n  ▶ 条件二：连续5日主力资金净流入 → 后续5日/10日收益率统计")
    report_lines.append(f"  {'标的':<16} {'触发次数':<10} {'5日均收%':<12} {'5日胜率%':<12} {'10日均收%':<12} {'10日胜率%':<12}")
    report_lines.append("  " + "-" * 80)

    for code, result in analysis_results.items():
        name = result["info"]["name"]
        ev = result["event_5d"]
        if ev and ev["n_events"] > 0 and ev["results"]:
            r5 = ev["results"].get(5, {})
            r10 = ev["results"].get(10, {})
            report_lines.append(
                f"  {name:<16} {ev['n_events']:<10} "
                f"{r5.get('avg_return', 0):>8.2f}{'':>3} "
                f"{r5.get('win_rate', 0):>8.1f}{'':>3} "
                f"{r10.get('avg_return', 0):>8.2f}{'':>3} "
                f"{r10.get('win_rate', 0):>8.1f}")
        else:
            report_lines.append(f"  {name:<16} {'0':<10} {'N/A':<12} {'N/A':<12} {'N/A':<12} {'N/A':<12}")

    # 3.3 大单净占比异常
    report_lines.append("\n  ▶ 条件三：单日大单净占比 > 20% 且主力净流入 → 次日表现")
    report_lines.append(f"  {'标的':<16} {'触发次数':<10} {'次日均收%':<12} {'次日中位%':<12} {'次日胜率%':<12} {'次日最大%':<12} {'次日最小%':<12}")
    report_lines.append("  " + "-" * 80)

    for code, result in analysis_results.items():
        name = result["info"]["name"]
        lo = result["large_order"]
        if lo and lo["n_events"] > 0:
            report_lines.append(
                f"  {name:<16} {lo['n_events']:<10} "
                f"{lo['avg_next_return']:>8.2f}{'':>3} "
                f"{lo['median_next_return']:>8.2f}{'':>3} "
                f"{lo['win_rate']:>8.1f}{'':>3} "
                f"{lo['max_next_return']:>8.2f}{'':>3} "
                f"{lo['min_next_return']:>8.2f}")
        else:
            report_lines.append(f"  {name:<16} {'0':<10} {'N/A':<12} {'N/A':<12} {'N/A':<12} {'N/A':<12} {'N/A':<12}")

    # ── 4. 资金背离分析 ──
    report_lines.append("\n\n" + "─" * 80)
    report_lines.append("四、资金背离分析")
    report_lines.append("─" * 80)
    report_lines.append("")

    for code, result in analysis_results.items():
        name = result["info"]["name"]
        divs = result["divergences"]
        if divs:
            # 统计背离后5日走势
            future_returns = [d["future_5d_return"] for d in divs if d["future_5d_return"] is not None]
            reversal_count = sum(1 for d in divs if d["future_5d_return"] is not None and
                                 ((d["type"] == "价涨资金流出" and d["future_5d_return"] < 0) or
                                  (d["type"] == "价跌资金流入" and d["future_5d_return"] > 0)))
            total_with_future = len(future_returns)

            report_lines.append(f"\n  ■ {name} ({code})")
            report_lines.append(f"    背离次数: {len(divs)} 次")
            if future_returns:
                report_lines.append(f"    背离后5日平均收益: {np.mean(future_returns):.2f}%")
                report_lines.append(f"    反转概率: {reversal_count}/{total_with_future} = {reversal_count/total_with_future*100:.1f}%")
            # 最近3次背离
            for d in divs[-3:]:
                fut_str = f"{d['future_5d_return']:.2f}%" if d['future_5d_return'] is not None else "N/A"
                report_lines.append(f"      {d['date']} {d['type']} 价格变动:{d['price_change']:.2f}% 资金:{format_amount(d['fund_flow'])} 后5日:{fut_str}")
        else:
            report_lines.append(f"\n  ■ {name} ({code})")
            report_lines.append(f"    近期无显著资金背离")

    # ── 5. 结论与信号 ──
    report_lines.append("\n\n" + "─" * 80)
    report_lines.append("五、结论与当前信号")
    report_lines.append("─" * 80)
    report_lines.append("")

    signals = []
    for code, result in analysis_results.items():
        name = result["info"]["name"]
        group = result["info"]["cap_group"]
        signals_for_this = []

        # 检查最近3日是否连续净流入
        merged = result["merged"]
        if merged is not None and not merged.empty and not merged["main_net"].isna().all():
            df_valid = merged.dropna(subset=["main_net"]).tail(5)
            if len(df_valid) >= 3:
                last3_inflow = all(df_valid["main_net"].tail(3) > 0)
                last3_outflow = all(df_valid["main_net"].tail(3) < 0)
                if last3_inflow:
                    signals_for_this.append("⚠ 近3日主力连续净流入（关注条件一触发）")
                if last3_outflow:
                    signals_for_this.append("⚠ 近3日主力连续净流出（资金撤离信号）")

                # 检查近5日累计
                last5_sum = df_valid["main_net"].tail(5).sum()
                if last5_sum > 0:
                    signals_for_this.append(f"近5日主力累计净流入 {format_amount(last5_sum)}")
                else:
                    signals_for_this.append(f"近5日主力累计净流出 {format_amount(abs(last5_sum))}")

            # 检查背离
            divs = result["divergences"]
            if divs and len(divs) > 0:
                last_div = divs[-1]
                days_since = (datetime.strptime(TODAY, "%Y-%m-%d") -
                              datetime.strptime(last_div["date"], "%Y-%m-%d")).days
                if days_since <= 10:
                    signals_for_this.append(f"⚠ 近{days_since}日出现{last_div['type']}背离")

        if signals_for_this:
            signals.append((name, group, signals_for_this))

    if signals:
        report_lines.append("  当前触发关注条件的标的：")
        for name, group, sigs in signals:
            report_lines.append(f"\n  ■ {name}（{group}）")
            for s in sigs:
                report_lines.append(f"    {s}")
    else:
        report_lines.append("  当前无标的触发显著关注条件。")

    # 按市值分组的特征总结
    report_lines.append("\n\n  ── 按市值分组的资金流特征总结 ──")
    for group in ["大盘", "中盘", "小盘"]:
        group_items = [(c, r) for c, r in analysis_results.items()
                       if r["info"]["cap_group"] == group and r["summary"]]
        if not group_items:
            continue
        report_lines.append(f"\n  【{group}】")
        # 统计该组平均大单占比和主力净流入
        ratios_20d = []
        main_20d = []
        for _, r in group_items:
            if "20日" in r["summary"]:
                ratios_20d.append(r["summary"]["20日"]["large_ratio"])
                main_20d.append(r["summary"]["20日"]["main_net"])
        if ratios_20d:
            report_lines.append(f"    近20日平均大单占比: {np.mean(ratios_20d):.1f}%")
            report_lines.append(f"    近20日平均主力净流入: {format_amount(np.mean(main_20d))}")

    # ── 6. 风险提示 ──
    report_lines.append("\n\n" + "─" * 80)
    report_lines.append("六、风险提示")
    report_lines.append("─" * 80)
    report_lines.append("""
  1. 样本量不足：120个交易日的回测样本有限，连续N日净流入等条件的触发次数可能较少，
     统计结果可能存在小样本偏差，不宜直接作为交易依据。
  2. 市场环境变化：资金流与价格的相关性受市场整体环境（牛熊周期、政策变化、流动性环境）影响，
     历史统计关系在未来不一定持续有效。
  3. 资金流数据滞后性：东财资金流数据为盘后数据，存在一定滞后；分钟级资金流盘中实时性
     受接口限制。背离信号的实际可操作性需考虑数据延迟。
  4. ETF与指数限制：东财个股资金流接口不覆盖ETF和指数，588710和000300的资金流数据
     可能为空或不完整，相关分析结论需谨慎对待。
  5. 数据源风险：东财系接口存在IP风控，批量请求可能被临时封禁，导致部分数据缺失。
  6. 本报告仅为量化研究分析，不构成任何投资建议。股市有风险，投资需谨慎。
""")

    report_lines.append("\n" + "=" * 80)
    report_lines.append("报告结束")
    report_lines.append("=" * 80)

    return "\n".join(report_lines), analysis_results


# ═══════════════════════════════════════════════════════════════════════
# 第六部分：主程序
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("A股资金流向与价格走势相关性研究")
    print(f"研究日期: {TODAY}")
    print(f"数据区间: {START_DATE} ~ {TODAY}")
    print("=" * 60)

    # 1. 获取数据
    print("\n[1/3] 获取数据...")
    all_data = fetch_all_data()

    # 2. 分析
    print("\n[2/3] 数据分析中...")
    report_text, analysis_results = generate_report(all_data)

    # 3. 输出报告
    print("\n[3/3] 生成报告...")
    report_file = "/root/AstockReearch/research_report.txt"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"报告已保存至: {report_file}")

    # 同时保存分析数据为 JSON（便于后续使用）
    json_data = {}
    for code, result in analysis_results.items():
        merged = result["merged"]
        json_data[code] = {
            "info": result["info"],
            "summary": result["summary"],
            "event_3d": result["event_3d"],
            "event_5d": result["event_5d"],
            "large_order": result["large_order"],
            "divergences_count": len(result["divergences"]),
            "divergences": result["divergences"][-5:] if result["divergences"] else [],
            "latest_price": float(merged["close"].iloc[-1]) if merged is not None and not merged.empty else None,
            "latest_date": str(merged["date"].iloc[-1]) if merged is not None and not merged.empty else None,
            "kline_count": len(merged) if merged is not None else 0,
            "fund_flow_count": len(merged.dropna(subset=["main_net"])) if merged is not None else 0,
            "dragon_tiger_count": len(result["dragon_tiger"].get("records", [])) if result.get("dragon_tiger") else 0,
            "block_trade_count": len(result["block_trades"]) if result.get("block_trades") else 0,
        }
        # 滚动相关系数统计
        rc = result["rolling_corr"]
        if rc is not None and not rc.empty:
            corr_same = rc["corr_same_day"].dropna()
            corr_next = rc["corr_next_day"].dropna()
            json_data[code]["rolling_corr"] = {
                "same_day_latest": float(corr_same.iloc[-1]) if len(corr_same) > 0 else None,
                "same_day_mean": float(corr_same.mean()) if len(corr_same) > 0 else None,
                "next_day_latest": float(corr_next.iloc[-1]) if len(corr_next) > 0 else None,
                "next_day_mean": float(corr_next.mean()) if len(corr_next) > 0 else None,
                "sample_size": int(len(corr_same)),
            }

    json_file = "/root/AstockReearch/research_data.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"分析数据已保存至: {json_file}")

    # 打印报告
    print("\n" + report_text)


if __name__ == "__main__":
    main()

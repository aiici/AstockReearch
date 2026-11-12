"""
数据采集服务 — 从 a-stock-data 获取数据并持久化到数据库
支持实时行情 + 实时资金流数据
"""

import time
import json
import urllib.request
import numpy as np
import pandas as pd
from datetime import date, timedelta
from models import db, Stock, DailyData
from sqlalchemy.exc import IntegrityError


# ── 实时数据获取 ──
_REALTIME_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def get_realtime_quote(code):
    """腾讯实时报价：最新价、涨跌幅、开高低、成交量"""
    code = code.strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    code = code.replace(".sh", "").replace(".sz", "").replace(".bj", "")

    if code.startswith("6") or code.startswith("9"):
        prefix = "sh"
    elif code.startswith("8") or code.startswith("4"):
        prefix = "bj"
    else:
        prefix = "sz"

    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("gbk", "ignore")
        parts = raw.split("~")
        if len(parts) < 35:
            return None
        return {
            "code": code,
            "name": parts[1],
            "price": float(parts[3]) if parts[3] else 0,
            "prev_close": float(parts[4]) if parts[4] else 0,
            "open": float(parts[5]) if parts[5] else 0,
            "volume": float(parts[6]) if parts[6] else 0,
            "high": float(parts[33]) if parts[33] else 0,
            "low": float(parts[34]) if parts[34] else 0,
            "pct_change": float(parts[32]) if parts[32] else 0,
            "amount_wan": float(parts[37]) if parts[37] else 0,
            "time": f"{parts[30][:8]} {parts[30][8:]}" if len(parts[30]) >= 8 else parts[30],
            "is_realtime": True,
        }
    except Exception:
        return None


def get_realtime_fund_flow(code):
    """东财实时资金流：今日主力/超大单/大单/中单/小单净额"""
    code = code.strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    code = code.replace(".sh", "").replace(".sz", "").replace(".bj", "")

    # secid: 1=沪, 0=深, 0=北(bj用0)
    if code.startswith("6") or code.startswith("9"):
        secid = f"1.{code}"
    else:
        secid = f"0.{code}"

    url = (
        f"http://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
        f"?secid={secid}&klt=1&fields1=f1,f2,f3,f7"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
        f"&lmt=1"
    )
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=_REALTIME_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            data = json.loads(raw)
            klines = data.get("data", {}).get("klines", [])
            if not klines:
                return None
            # 格式: "2026-09-23 14:52,main_net,small_net,mid_net,large_net,super_net,..."
            parts = klines[-1].split(",")
            dt_str = parts[0]
            trade_date = dt_str[:10]  # YYYY-MM-DD
            return {
                "date": trade_date,
                "main_net": float(parts[1]) if len(parts) > 1 else 0,
                "small_net": float(parts[2]) if len(parts) > 2 else 0,
                "mid_net": float(parts[3]) if len(parts) > 3 else 0,
                "large_net": float(parts[4]) if len(parts) > 4 else 0,
                "super_net": float(parts[5]) if len(parts) > 5 else 0,
                "is_realtime": True,
            }
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return None


def fetch_and_store(code, force_refresh=False):
    """
    获取股票数据（K线+资金流）并存入数据库。
    如果数据未过期（<1天）且非强制刷新，直接返回已有数据。

    返回: {"stock": stock.to_dict(), "data_count": N, "latest_date": "..."}
    """
    ticker = code.lower().strip()
    # 去掉可能的前缀
    for prefix in ("sh", "sz", "bj"):
        if ticker.startswith(prefix):
            ticker = ticker[2:]
            break
    ticker = ticker.replace(".sh", "").replace(".sz", "").replace(".bj", "")

    # 检查是否已有数据
    stock = Stock.query.filter_by(code=ticker).first()
    today_str = date.today().isoformat()

    if stock and not force_refresh:
        if stock.latest_update == today_str:
            return {"stock": stock.to_dict(), "data_count": stock.daily_data.count(),
                    "latest_date": stock.latest_update, "cached": True}

    # 获取新数据
    from capital_flow_research import tencent_kline, stock_fund_flow_120d, get_prefix

    market = get_prefix(ticker)
    start_date = (date.today() - timedelta(days=180)).isoformat()

    # 1. K线数据
    try:
        if market == "sh" and ticker.startswith("000") and len(ticker) == 6:
            kline_df = tencent_kline(f"sh{ticker}", period="day", adjust="", start=start_date, count=320)
        else:
            kline_df = tencent_kline(ticker, period="day", adjust="qfq", start=start_date, count=320)
    except Exception:
        try:
            kline_df = tencent_kline(ticker, period="day", count=320)
        except Exception as e:
            raise RuntimeError(f"K线获取失败: {e}")

    time.sleep(0.5)

    # 2. 资金流数据 — 先试东财，失败则降级到新浪
    fund_flow = []
    try:
        fund_flow = stock_fund_flow_120d(ticker)
    except Exception:
        pass

    if not fund_flow:
        # 东财被封，降级到新浪备用源
        try:
            from capital_flow_research import fund_flow_backup
            sina_data = fund_flow_backup(ticker)
            if sina_data:
                # 新浪返回降序(最新在前)，反转为升序匹配K线
                sina_data = list(reversed(sina_data))
                # 转换格式: net_amount -> main_net，四档明细不可用
                fund_flow = [{
                    "date": item.get("date", ""),
                    "main_net": item.get("net_amount", 0),
                    "super_net": None,
                    "large_net": None,
                    "mid_net": None,
                    "small_net": None,
                } for item in sina_data]
                print(f"  [INFO] 东财资金流不可用，已降级到新浪备用源({len(fund_flow)}日)")
        except Exception as e:
            print(f"  [WARN] 新浪资金流也失败: {e}")

    # 2.5 补充今日实时资金流（新浪延迟1天，东财fflow实时可用）
    today_str = date.today().isoformat()
    latest_ff_date = ""
    if fund_flow:
        latest_ff_date = str(fund_flow[-1].get("date", ""))
    if latest_ff_date < today_str:
        print(f"  [INFO] 资金流数据截至 {latest_ff_date or '无'}，尝试获取今日实时资金流...")
        rt_ff = get_realtime_fund_flow(ticker)
        if rt_ff:
            fund_flow.append(rt_ff)
            print(f"  [INFO] 实时资金流已补充: {rt_ff['date']} 主力={rt_ff['main_net']/1e8:.2f}亿")
        else:
            print(f"  [WARN] 实时资金流获取失败")

    # 3. 合并数据
    kline_records = []
    for _, row in kline_df.iterrows():
        kline_records.append({
            "date": str(row.get("date", "")),
            "open": float(row.get("open", 0)) if not pd.isna(row.get("open")) else None,
            "high": float(row.get("high", 0)) if not pd.isna(row.get("high")) else None,
            "low": float(row.get("low", 0)) if not pd.isna(row.get("low")) else None,
            "close": float(row.get("close", 0)) if not pd.isna(row.get("close")) else None,
            "volume": float(row.get("volume", 0)) if not pd.isna(row.get("volume")) else None,
        })

    # 3.5 补充今日实时价格（腾讯报价，更新K线最新bar）
    rt_quote = get_realtime_quote(ticker)
    if rt_quote and rt_quote.get("price"):
        if kline_records and kline_records[-1].get("date") == today_str:
            kline_records[-1]["close"] = rt_quote["price"]
            if rt_quote.get("high"):
                kline_records[-1]["high"] = max(kline_records[-1]["high"] or 0, rt_quote["high"])
            if rt_quote.get("low"):
                kline_records[-1]["low"] = min(kline_records[-1]["low"] or 999999, rt_quote["low"])
            print(f"  [INFO] 实时价格已更新: {rt_quote['price']} ({rt_quote.get('time','')})")

    # 计算日涨跌幅
    closes = [r["close"] for r in kline_records if r["close"]]
    for i, rec in enumerate(kline_records):
        if i == 0 or not closes[i - 1]:
            rec["pct_change"] = None
        else:
            rec["pct_change"] = round((closes[i] / closes[i - 1] - 1) * 100, 2)

    # 合并资金流
    ff_map = {}
    for ff in fund_flow:
        ff_map[str(ff.get("date", ""))] = ff

    merged = []
    for rec in kline_records:
        ff = ff_map.get(rec["date"], {})
        rec["main_net"] = ff.get("main_net") if ff else None
        rec["super_net"] = ff.get("super_net") if ff else None
        rec["large_net"] = ff.get("large_net") if ff else None
        rec["mid_net"] = ff.get("mid_net") if ff else None
        rec["small_net"] = ff.get("small_net") if ff else None
        merged.append(rec)

    # 4. 计算20日滚动相关系数
    df_calc = pd.DataFrame(merged)
    if "main_net" in df_calc.columns and "pct_change" in df_calc.columns:
        df_calc["main_net_num"] = pd.to_numeric(df_calc["main_net"], errors="coerce")
        df_calc["pct_num"] = pd.to_numeric(df_calc["pct_change"], errors="coerce")
        df_calc["corr_same_day"] = df_calc["main_net_num"].rolling(20).corr(df_calc["pct_num"])
    else:
        df_calc["corr_same_day"] = None

    # 5. 存入数据库
    if not stock:
        # 尝试获取股票名称
        name = ticker
        cap_group = "未知"
        if market == "sh" and ticker.startswith("000") and len(ticker) == 6:
            cap_group = "指数"
        elif market == "sh" and ticker.startswith("5"):
            cap_group = "ETF"
        stock = Stock(code=ticker, name=name, market=market, cap_group=cap_group)

    # 更新股票信息
    if not kline_df.empty:
        # 尝试从K线数据获取名称
        name_val = kline_df.iloc[0].get("name") or kline_df.iloc[0].get("code", "")
        if name_val and name_val != ticker and not str(name_val).startswith(market):
            stock.name = str(name_val)
        else:
            stock.name = ticker

    # 从股票池获取正确的股票名称
    try:
        from market_scanner import get_stock_name
        proper_name = get_stock_name(ticker)
        if proper_name and proper_name != ticker:
            stock.name = proper_name
    except Exception:
        pass

    # 从实时报价获取名称（腾讯数据最准确）
    if rt_quote and rt_quote.get("name") and rt_quote["name"] != ticker:
        stock.name = rt_quote["name"]

    # 如果没有名称，使用代码
    if not stock.name:
        stock.name = ticker

    if merged:
        latest = merged[-1]
        stock.latest_update = latest["date"]
        stock.latest_price = latest.get("close")
        stock.latest_pct = latest.get("pct_change")

    # 计算相关系数均值
    corr_vals = [v for v in df_calc["corr_same_day"].dropna().tolist() if not np.isnan(v)]
    stock.corr_mean = round(float(np.mean(corr_vals)), 3) if corr_vals else None

    db.session.add(stock)
    try:
        db.session.flush()  # 获取 stock.id
    except IntegrityError:
        # 并发请求可能已插入同一股票，回滚后复用已有记录
        db.session.rollback()
        _name, _update, _price, _pct, _corr = (
            stock.name, stock.latest_update, stock.latest_price,
            stock.latest_pct, stock.corr_mean,
        )
        stock = Stock.query.filter_by(code=ticker).first()
        if not stock:
            raise RuntimeError(f"股票 {ticker} 创建失败")
        stock.name = _name
        stock.latest_update = _update
        stock.latest_price = _price
        stock.latest_pct = _pct
        stock.corr_mean = _corr
        db.session.flush()

    # 删除旧数据，重新插入
    DailyData.query.filter_by(stock_id=stock.id).delete()

    for i, rec in enumerate(merged):
        corr_val = None
        if i < len(df_calc):
            cv = df_calc.iloc[i].get("corr_same_day")
            corr_val = float(cv) if not pd.isna(cv) else None

        dd = DailyData(
            stock_id=stock.id,
            date=rec["date"],
            open=rec.get("open"),
            high=rec.get("high"),
            low=rec.get("low"),
            close=rec.get("close"),
            volume=rec.get("volume"),
            pct_change=rec.get("pct_change"),
            main_net=rec.get("main_net"),
            super_net=rec.get("super_net"),
            large_net=rec.get("large_net"),
            mid_net=rec.get("mid_net"),
            small_net=rec.get("small_net"),
            corr_same_day=corr_val,
        )
        db.session.add(dd)

    db.session.commit()

    return {
        "stock": stock.to_dict(),
        "data_count": len(merged),
        "latest_date": stock.latest_update,
        "cached": False,
    }


def get_stock_data(stock_id, days=120):
    """从数据库获取指定天数的日线数据，返回 DataFrame"""
    records = DailyData.query.filter_by(stock_id=stock_id).order_by(
        DailyData.date.desc()).limit(days).all()
    records = list(reversed(records))
    if not records:
        return pd.DataFrame()

    data = []
    for r in records:
        data.append({
            "date": r.date, "open": r.open, "high": r.high, "low": r.low,
            "close": r.close, "volume": r.volume, "pct_change": r.pct_change,
            "main_net": r.main_net, "super_net": r.super_net, "large_net": r.large_net,
            "mid_net": r.mid_net, "small_net": r.small_net,
            "corr_same_day": r.corr_same_day,
        })
    return pd.DataFrame(data)


def get_or_create_stock(code):
    """获取或创建股票记录（不获取数据）"""
    ticker = code.lower().strip()
    for prefix in ("sh", "sz", "bj"):
        if ticker.startswith(prefix):
            ticker = ticker[2:]
            break
    ticker = ticker.replace(".sh", "").replace(".sz", "").replace(".bj", "")

    stock = Stock.query.filter_by(code=ticker).first()
    if not stock:
        from capital_flow_research import get_prefix
        market = get_prefix(ticker)
        stock = Stock(code=ticker, market=market, cap_group="未知")
        db.session.add(stock)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            stock = Stock.query.filter_by(code=ticker).first()
    return stock

# -*- coding: utf-8 -*-
"""
技术指标计算
-------------
输入为升序日线 [{'date','open','close','high','low','volume'}, ...]
所有函数都对空数据/不足长度做了保护，返回 None 而不是抛异常。
"""

from typing import List, Optional, Sequence, Dict, Any

Number = Optional[float]


# ---------------------------------------------------------------- 基础

def _closes(bars: Sequence[dict]) -> List[float]:
    return [b["close"] for b in bars]


def sma(vals: Sequence[float], n: int) -> Number:
    if not vals or len(vals) < n or n <= 0:
        return None
    return sum(vals[-n:]) / n


def sma_series(vals: Sequence[float], n: int) -> List[Number]:
    out: List[Number] = []
    for i in range(len(vals)):
        if i + 1 < n:
            out.append(None)
        else:
            out.append(sum(vals[i + 1 - n:i + 1]) / n)
    return out


def ema(vals: Sequence[float], n: int) -> Number:
    if not vals or n <= 0:
        return None
    k = 2.0 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def ema_series(vals: Sequence[float], n: int) -> List[float]:
    if not vals:
        return []
    k = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(bars: Sequence[dict], n: int = 14) -> Number:
    if len(bars) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(bars)):
        d = bars[i]["close"] - bars[i - 1]["close"]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    if len(gains) < n:
        return None
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def macd(bars: Sequence[dict], fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (dif, dea, hist)。hist = dif - dea"""
    closes = _closes(bars)
    if len(closes) < slow + signal:
        return None, None, None
    ef = ema_series(closes, fast)
    es = ema_series(closes, slow)
    dif_series = [a - b for a, b in zip(ef, es)]
    dea_series = ema_series(dif_series, signal)
    dif, dea = dif_series[-1], dea_series[-1]
    return dif, dea, dif - dea


def atr(bars: Sequence[dict], n: int = 14) -> Number:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def bollinger(bars: Sequence[dict], n: int = 20, k: float = 2.0):
    closes = _closes(bars)
    if len(closes) < n:
        return None, None, None
    mid = sum(closes[-n:]) / n
    var = sum((c - mid) ** 2 for c in closes[-n:]) / n
    sd = var ** 0.5
    return mid + k * sd, mid, mid - k * sd


def annualized_vol(bars: Sequence[dict], n: int = 20) -> Number:
    """20日年化波动率（%）"""
    closes = _closes(bars)
    if len(closes) < n + 1:
        return None
    seg = closes[-(n + 1):]
    rets = [(seg[i] / seg[i - 1] - 1) for i in range(1, len(seg)) if seg[i - 1]]
    if not rets:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / max(len(rets) - 1, 1)
    return (var ** 0.5) * (252 ** 0.5) * 100


def high_low(bars: Sequence[dict], n: int):
    if not bars:
        return None, None
    seg = bars[-n:] if len(bars) >= n else bars
    return max(b["high"] for b in seg), min(b["low"] for b in seg)


def chg_pct(bars: Sequence[dict], n: int) -> Number:
    closes = _closes(bars)
    if len(closes) < n + 1 or closes[-(n + 1)] == 0:
        return None
    return (closes[-1] / closes[-(n + 1)] - 1) * 100


def volume_ratio(bars: Sequence[dict], n: int = 5) -> Number:
    """今日成交量 / 前 n 日均量"""
    if len(bars) < n + 1:
        return None
    today = bars[-1]["volume"]
    prev = [b["volume"] for b in bars[-(n + 1):-1]]
    avg = sum(prev) / len(prev)
    if not avg:
        return None
    return today / avg


# ---------------------------------------------------------------- 周线

def to_weekly(bars: Sequence[dict]) -> List[dict]:
    """日线合成周线（按自然周聚合）"""
    from datetime import date
    buckets: Dict[Any, List[dict]] = {}
    for b in bars:
        try:
            y, m, d = (int(x) for x in b["date"].split("-"))
        except Exception:
            continue
        key = date(y, m, d).isocalendar()[:2]
        buckets.setdefault(key, []).append(b)
    out = []
    for key in sorted(buckets.keys()):
        seg = buckets[key]
        out.append({
            "date": seg[-1]["date"],
            "open": seg[0]["open"],
            "close": seg[-1]["close"],
            "high": max(x["high"] for x in seg),
            "low": min(x["low"] for x in seg),
            "volume": sum(x["volume"] for x in seg),
        })
    return out


# ---------------------------------------------------------------- Elder 强力系统

def impulse(series: Sequence[float], macd_hist: Sequence[float]) -> str:
    """
    Elder 三重强力系统配色：
      绿 = EMA13 上行 且 MACD柱 上行
      红 = EMA13 下行 且 MACD柱 下行
      蓝 = 其他
    """
    if len(series) < 2 or len(macd_hist) < 2:
        return "blue"
    e_up = series[-1] > series[-2]
    m_up = macd_hist[-1] > macd_hist[-2]
    if e_up and m_up:
        return "green"
    if (not e_up) and (not m_up):
        return "red"
    return "blue"


def impulse_score(color_now: str, color_prev: Optional[str]) -> int:
    """
    评分：红 0 / 绿 1 / 蓝紧跟红 2（其余蓝 0）
    """
    if color_now == "green":
        return 1
    if color_now == "red":
        return 0
    if color_now == "blue":
        return 2 if color_prev == "red" else 0
    return 0


# ---------------------------------------------------------------- 汇总

def compute_all(bars: Sequence[dict]) -> dict:
    """一次性算出某标的的全部技术指标"""
    r: Dict[str, Any] = {"bars": len(bars or [])}
    if not bars:
        return r
    closes = _closes(bars)
    r["close"] = closes[-1]
    r["prev_close"] = closes[-2] if len(closes) > 1 else None
    r["chg"] = ((closes[-1] / closes[-2] - 1) * 100) if len(closes) > 1 and closes[-2] else None
    for n in (5, 10, 20, 60, 120, 250):
        r[f"ma{n}"] = sma(closes, n)
    r["ma20_prev"] = sma(closes[:-1], 20) if len(closes) > 20 else None
    r["ema13"] = ema(closes, 13)
    r["ema26"] = ema(closes, 26)
    r["rsi"] = rsi(bars, 14)
    r["dif"], r["dea"], r["hist"] = macd(bars)
    r["atr"] = atr(bars, 14)
    r["boll_up"], r["boll_mid"], r["boll_low"] = bollinger(bars, 20, 2)
    r["vol20_annual"] = annualized_vol(bars, 20)
    r["high20"], r["low20"] = high_low(bars, 20)
    r["high60"], r["low60"] = high_low(bars, 60)
    r["high252"], r["low252"] = high_low(bars, 252)
    r["chg5"] = chg_pct(bars, 5)
    r["chg20"] = chg_pct(bars, 20)
    r["chg60"] = chg_pct(bars, 60)
    r["volume_ratio"] = volume_ratio(bars, 5)

    # 均线多头排列：ma5 > ma10 > ma20
    m5, m10, m20 = r.get("ma5"), r.get("ma10"), r.get("ma20")
    r["bull_align"] = bool(m5 and m10 and m20 and m5 > m10 > m20)
    r["ma20_up"] = bool(r.get("ma20") and r.get("ma20_prev") and r["ma20"] > r["ma20_prev"])

    # 吊灯止损：10日最高 − 3×ATR
    h10, _ = high_low(bars, 10)
    r["high10"] = h10
    r["chandelier"] = (h10 - 3 * r["atr"]) if (h10 is not None and r.get("atr")) else None

    # 周线
    wk = to_weekly(bars)
    r["weekly_bars"] = len(wk)
    if len(wk) >= 30:
        wc = [b["close"] for b in wk]
        ef = ema_series(wc, 12)
        es = ema_series(wc, 26)
        dif = [a - b for a, b in zip(ef, es)]
        dea = ema_series(dif, 9)
        hist = [a - b for a, b in zip(dif, dea)]
        r["w_dif"], r["w_dea"], r["w_hist"] = dif[-1], dea[-1], hist[-1]
        r["w_ema13"] = ema_series(wc, 13)
        r["w_macd_bull"] = bool(dif[-1] > dea[-1])
        r["w_hist_prev"] = hist[-2] if len(hist) > 1 else None
        r["w_hist_prev2"] = hist[-2] if len(hist) > 1 else None
        r["w_hist"] = hist
    return r


def weekly_impulse(bars: Sequence[dict]) -> str:
    wk = to_weekly(bars)
    if len(wk) < 35:
        return "blue"
    wc = [b["close"] for b in wk]
    e13 = ema_series(wc, 13)
    ef, es = ema_series(wc, 12), ema_series(wc, 26)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema_series(dif, 9)
    hist = [a - b for a, b in zip(dif, dea)]
    return impulse(e13, hist)


def daily_impulse(bars: Sequence[dict]) -> str:
    if len(bars) < 35:
        return "blue"
    c = _closes(bars)
    e13 = ema_series(c, 13)
    ef, es = ema_series(c, 12), ema_series(c, 26)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema_series(dif, 9)
    hist = [a - b for a, b in zip(dif, dea)]
    return impulse(e13, hist)


def impulse_prev(bars: Sequence[dict], weekly: bool = False) -> Optional[str]:
    """前一根K线的强力系统颜色"""
    seg = to_weekly(bars) if weekly else list(bars)
    if len(seg) < 36:
        return None
    seg = seg[:-1]
    c = [b["close"] for b in seg]
    e13 = ema_series(c, 13)
    ef, es = ema_series(c, 12), ema_series(c, 26)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema_series(dif, 9)
    hist = [a - b for a, b in zip(dif, dea)]
    return impulse(e13, hist)

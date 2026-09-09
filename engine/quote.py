# -*- coding: utf-8 -*-
"""
行情获取模块
------------
主数据源：腾讯财经（实时行情 qt.gtimg.cn + 前复权日线 web.ifzq.gtimg.cn）
兜底策略：接口异常时回落到 data/positions.json 中手填的 close（manual provider）

对外接口：
    fetch_realtime(codes) -> {code: {...}}
    fetch_kline(code, days) -> [{date, open, close, high, low, volume}, ...]
    fetch_klines(codes, days) -> {code: [...]}
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Referer": "https://gu.qq.com/"}

REALTIME_URL = "https://qt.gtimg.cn/q="
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="

_TIMEOUT = 15
_RETRIES = 2

# 简单的进程内缓存，避免同一标的重复请求
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 120  # 秒


# ---------------------------------------------------------------- 代码转换

def to_symbol(code: str) -> str:
    """600941.SH -> sh600941 ； 00700.HK -> hk00700 ； HSTECH.HI -> hkHSTECH"""
    code = (code or "").strip()
    if not code:
        return ""
    if "." not in code:
        return code.lower()
    sym, mkt = code.rsplit(".", 1)
    sym = sym.lower()
    mkt = mkt.lower()
    if mkt in ("sh", "ss"):
        return "sh" + sym
    if mkt in ("sz",):
        return "sz" + sym
    if mkt in ("hk",):
        return "hk" + sym
    if mkt in ("hi", "idx"):
        # 恒生系列指数在腾讯的命名空间是 hkXXX
        return "hk" + sym.upper()
    if mkt in ("bj",):
        return "bj" + sym
    return "sh" + sym


def market_of(code: str) -> str:
    s = to_symbol(code)
    if s.startswith("hk"):
        return "hk"
    if s.startswith("sz"):
        return "sz"
    if s.startswith("bj"):
        return "bj"
    return "sh"


def is_hk(code: str) -> bool:
    return market_of(code) == "hk"


# ---------------------------------------------------------------- HTTP

def _http_get(url: str, encoding: str = "utf-8", retries: int = _RETRIES) -> str:
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read()
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                return raw.decode("gbk", "ignore")
        except Exception as e:  # noqa
            last = e
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"请求失败 {url[:80]}... -> {last}")


def _cached(key: str, fn, ttl: int = _CACHE_TTL):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _num(v, default=None):
    try:
        if v is None or v == "" or v == "-":
            return default
        return float(v)
    except Exception:
        return default


# ---------------------------------------------------------------- 实时行情

def _parse_realtime_line(line: str) -> Optional[dict]:
    if "=" not in line or '"' not in line:
        return None
    head = line.split("=", 1)[0].strip().replace("v_", "")
    try:
        body = line.split('"')[1]
    except IndexError:
        return None
    f = body.split("~")
    if len(f) < 10:
        return None

    hk = head.startswith("hk")
    d = {
        "symbol": head,
        "name": f[1],
        "code": f[2],
        "price": _num(f[3]),
        "prev_close": _num(f[4]),
        "open": _num(f[5]),
        "volume": _num(f[6]),
        "error": None,
    }

    if hk:
        d.update({
            "time": f[30] if len(f) > 30 else "",
            "change": _num(f[31]) if len(f) > 31 else None,
            "change_pct": _num(f[32]) if len(f) > 32 else None,
            "high": _num(f[33]) if len(f) > 33 else None,
            "low": _num(f[34]) if len(f) > 34 else None,
            "amount": _num(f[37]) if len(f) > 37 else None,
            "pe": _num(f[39]) if len(f) > 39 else None,
            "pb": None,                      # 腾讯港股接口无稳定 PB 字段，由 config 覆盖或跳过
            "high52": _num(f[48]) if len(f) > 48 else None,
            "low52": _num(f[49]) if len(f) > 49 else None,
            "volume_ratio": _num(f[50]) if len(f) > 50 else None,
        })
    else:
        d.update({
            "time": f[30] if len(f) > 30 else "",
            "change": _num(f[31]) if len(f) > 31 else None,
            "change_pct": _num(f[32]) if len(f) > 32 else None,
            "high": _num(f[33]) if len(f) > 33 else None,
            "low": _num(f[34]) if len(f) > 34 else None,
            "amount": (_num(f[37]) or 0) * 10000 if len(f) > 37 else None,
            "turnover_rate": _num(f[38]) if len(f) > 38 else None,
            "pe": _num(f[39]) if len(f) > 39 else None,
            "amplitude": _num(f[43]) if len(f) > 43 else None,
            "circ_mv": _num(f[44]) if len(f) > 44 else None,
            "total_mv": _num(f[45]) if len(f) > 45 else None,
            "pb": _num(f[46]) if len(f) > 46 else None,
            "volume_ratio": _num(f[49]) if len(f) > 49 else None,
        })

    if d["price"] is None or d["price"] == 0:
        d["error"] = "接口返回空价格"
    # 涨跌幅兜底自算
    if d["change_pct"] is None and d["prev_close"]:
        d["change_pct"] = round((d["price"] - d["prev_close"]) / d["prev_close"] * 100, 2)
    return d


def fetch_realtime(codes: List[str], batch: int = 40) -> Dict[str, dict]:
    """批量拉取实时行情，返回 {原始code: 行情dict}"""
    out: Dict[str, dict] = {}
    codes = [c for c in codes if c]
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        symbols = [to_symbol(c) for c in chunk]
        url = REALTIME_URL + ",".join(symbols)
        try:
            txt = _http_get(url, encoding="gbk")
        except Exception as e:  # noqa
            for c in chunk:
                out[c] = {"code": c, "error": f"行情请求失败: {e}"}
            continue
        parsed = {}
        for line in txt.split(";"):
            d = _parse_realtime_line(line.strip())
            if d and d.get("symbol"):
                parsed[d["symbol"]] = d
        for c in chunk:
            d = parsed.get(to_symbol(c))
            if d is None:
                out[c] = {"code": c, "error": "未返回该标的数据"}
            else:
                d["code"] = c
                out[c] = d
    return out


# ---------------------------------------------------------------- 日线

def _kline_url(symbol: str, days: int) -> str:
    return f"{KLINE_URL}{symbol},day,,,{days},qfq"


def fetch_kline(code: str, days: int = 320) -> List[dict]:
    """返回 [{date, open, close, high, low, volume}]，按日期升序"""
    symbol = to_symbol(code)

    def _do():
        txt = _http_get(_kline_url(symbol, days), encoding="utf-8")
        js = json.loads(txt)
        node = (js.get("data") or {}).get(symbol) or {}
        rows = node.get("qfqday") or node.get("day")
        if not rows and node.get("qt"):
            rows = None
        if not rows:
            return []
        out = []
        for r in rows:
            try:
                out.append({
                    "date": r[0],
                    "open": float(r[1]),
                    "close": float(r[2]),
                    "high": float(r[3]),
                    "low": float(r[4]),
                    "volume": float(r[5]) if len(r) > 5 else 0.0,
                })
            except Exception:
                continue
        out.sort(key=lambda x: x["date"])
        return out

    key = f"kline:{symbol}:{days}"
    try:
        return _cached(key, _do, ttl=600)
    except Exception:
        return []


def fetch_klines(codes: List[str], days: int = 320) -> Dict[str, List[dict]]:
    return {c: fetch_kline(c, days) for c in codes}


# ---------------------------------------------------------------- 兜底

def load_manual_prices(positions_path: str) -> Dict[str, float]:
    """读 positions.json 中手填的 close，作为离线兜底"""
    try:
        with open(positions_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    for h in data.get("holdings", []):
        if h.get("close") is not None:
            out[h["code"]] = float(h["close"])
    for w in data.get("watchlist", []):
        if w.get("close") is not None:
            out[w["code"]] = float(w["close"])
    return out

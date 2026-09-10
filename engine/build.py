# -*- coding: utf-8 -*-
"""
看板构建引擎
-------------
输入：config/*.json + data/positions.json + data/trades.json + data/fundamentals.json + 实时行情
输出：data/dashboard.json（前端直接读取）+ data/history.json（每日评分快照）

run_update.py 调用 build_dashboard()，每天收盘后跑一次即可。
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Any

from . import quote as Q
from . import indicators as ind
from . import scoring as SC

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE, "config")
DATA_DIR = os.path.join(BASE, "data")


def _read(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _round(v, d=2):
    return None if v is None else round(float(v), d)


# ---------------------------------------------------------------- 上下文

def _quality_norm(f: dict) -> Optional[float]:
    """把基本面四项归一到 0~1，缺失项从分母剔除"""
    items = [
        (f.get("roe"), 5, 15),
        (f.get("profit_yoy"), -5, 20),
        (f.get("rev_yoy"), -3, 15),
        (f.get("net_margin"), 5, 20),
    ]
    vals = [SC._norm(v, lo, hi) for v, lo, hi in items]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _detect_divergence(bars: List[dict], weekly: bool = False) -> bool:
    """底背离检测：后一段价格创新低，但 MACD 柱未同步创新低"""
    seg = ind.to_weekly(bars) if weekly else bars
    if len(seg) < 45:
        return False
    closes = [b["close"] for b in seg]
    ef = ind.ema_series(closes, 12)
    es = ind.ema_series(closes, 26)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ind.ema_series(dif, 9)
    hist = [a - b for a, b in zip(dif, dea)]
    n = len(seg)
    seg2 = closes[n - 20:n]
    seg1 = closes[n - 40:n - 20]
    if not seg2 or not seg1:
        return False
    i2 = n - 20 + seg2.index(min(seg2))
    i1 = n - 40 + seg1.index(min(seg1))
    return bool(closes[i2] < closes[i1] and hist[i2] > hist[i1])


def _build_ctx(bars: List[dict], rt: dict, bench: dict, fund: dict, is_etf: bool,
               bench_name: str, bench_bull: bool, fx: float) -> dict:
    t = ind.compute_all(bars) if bars else {}
    if rt and rt.get("price"):
        t["close"] = rt["price"]
        t["prev_close"] = rt.get("prev_close")
        t["chg"] = rt.get("change_pct")
    if rt and rt.get("high52"):
        t["high252"] = rt["high52"]
    if rt and rt.get("low52"):
        t["low252"] = rt["low52"]
    if rt and rt.get("volume_ratio"):
        t["volume_ratio"] = rt["volume_ratio"]

    # 年线方向
    if bars and len(bars) > 251:
        t["ma250_up"] = (t.get("ma250") or 0) > (ind.sma([b["close"] for b in bars[:-1]], 250) or 0)

    rs = None
    if bench and bench.get("chg20") is not None and t.get("chg20") is not None:
        rs = t["chg20"] - bench["chg20"]

    h20 = t.get("high20")
    touched = False
    if h20 and len(bars) >= 11:
        touched = any(b["high"] >= h20 * 0.995 for b in bars[-11:-1])

    f = dict(fund or {})
    if rt:
        if rt.get("pe") and rt["pe"] > 0:
            f["pe"] = rt["pe"]
        if rt.get("pb") and rt["pb"] > 0:
            f["pb"] = rt["pb"]

    return {
        "bars": bars, "t": t, "rt": rt, "fx": fx,
        "close": t.get("close"),
        "rs_vs_benchmark": rs,
        "benchmark_name": bench_name,
        "benchmark_bull": bench_bull,
        "touched_high_recently": touched,
        "is_etf": is_etf,
        "pe": f.get("pe"),
        "fundamentals": f,
        "quality_norm": _quality_norm(f),
        "w_impulse": ind.weekly_impulse(bars) if bars else "blue",
        "w_impulse_prev": ind.impulse_prev(bars, weekly=True) if bars else None,
        "d_impulse": ind.daily_impulse(bars) if bars else "blue",
        "d_impulse_prev": ind.impulse_prev(bars, weekly=False) if bars else None,
        "daily_bullish_divergence": _detect_divergence(bars, False) if bars else False,
        "weekly_bullish_divergence": _detect_divergence(bars, True) if bars else False,
    }


def evaluate(ctx: dict, cfg: dict) -> Dict[str, Dict[str, dict]]:
    """计算一个标的在四套模板下的全部子维度"""
    t = ctx["t"]
    return {
        "trend": SC.trend_dimensions(t, ctx),
        "longterm": SC.longterm_dimensions(t, ctx),
        "divergence": SC.divergence_dimensions(t, ctx),
        "fundamental": SC.fundamental_dimensions(ctx),
    }


def estimate_holding_days(code: str, trades_doc: dict, as_of: str) -> Optional[int]:
    """
    估算持仓天数 —— 从 trades.json 找该 code 第一次 'buy' 的日期（type=baseline 也算）。
    返回 None 表示无建仓日，常见的场景是迁移数据。
    """
    buys = sorted([t["date"] for t in trades_doc.get("trades", [])
                   if t.get("code") == code and t.get("side") == "buy" and t.get("date")])
    if not buys:
        return None
    try:
        from datetime import date
        y0, m0, d0 = (int(x) for x in buys[0].split("-"))
        y1, m1, d1 = (int(x) for x in as_of.split("-"))
        return max(0, (date(y1, m1, d1) - date(y0, m0, d0)).days)
    except Exception:
        return None


def scores_from_dims(all_dims: Dict[str, Dict[str, dict]], cfg: dict) -> Dict[str, Optional[float]]:
    out = {}
    for key, tmpl in cfg["templates"].items():
        if not tmpl.get("dimensions"):
            out[key] = None
            continue
        out[key] = SC.composite(all_dims.get(key) or {}, tmpl)
    return out


# ---------------------------------------------------------------- 历史回算

def _dates_from_bars(bars: List[dict], n: int) -> List[str]:
    return [b["date"] for b in bars[-n:]]


def _light_stats(bars: List[dict], end: int) -> dict:
    """只算广度/趋势需要的轻量指标，避免历史回算时全量计算拖慢速度"""
    sub = bars[:max(end, 2)]
    closes = [b["close"] for b in sub]
    ma20 = ind.sma(closes, 20)
    ma20_prev = ind.sma(closes[:-1], 20)
    h20 = max(b["high"] for b in sub[-20:]) if len(sub) >= 20 else None
    return {
        "close": closes[-1], "ma20": ma20, "high20": h20,
        "ma20_up": bool(ma20 and ma20_prev and ma20 > ma20_prev),
    }


def _light_state(st: dict) -> str:
    close, ma20 = st.get("close"), st.get("ma20")
    if close is None or ma20 is None:
        return "震荡"
    if close > ma20 and st.get("ma20_up"):
        return "多头"
    if close < ma20 and not st.get("ma20_up"):
        return "空头"
    return "震荡"


def _slice_end(bars: List[dict], n: int, i: int) -> int:
    return len(bars) - n + i + 1 if bars else 0


def backfill_history(holdings: List[dict], klines: Dict[str, List[dict]],
                     bench_bars: List[dict], cfg: dict, days: int,
                     fund_map: dict, fx_map: dict,
                     index_codes: Optional[List[str]] = None,
                     pool: Optional[List[dict]] = None) -> List[dict]:
    """用历史K线回算最近 N 天的评分，生成趋势图数据"""
    if not holdings:
        return []
    lens = [len(klines.get(h["code"]) or []) for h in holdings]
    if not lens or max(lens) < 30:
        return []
    n = min(days, min(lens) - 30)
    if n <= 1:
        return []

    ref = klines.get(holdings[0]["code"]) or []
    dates = _dates_from_bars(ref, n)
    snaps = []
    for i, d in enumerate(dates):
        offset = len(ref) - n + i + 1
        row = {"date": d, "scores": {}, "timing": None, "total_mv": None, "total_pnl_pct": None}
        for h in holdings:
            bars = klines.get(h["code"]) or []
            if len(bars) < 30:
                continue
            # 每只股票按自身K线长度对齐，避免因停牌等造成的长度差异
            end = len(bars) - n + i + 1
            sub = bars[:max(end, 30)]
            f = dict(fund_map.get(h["code"]) or {})
            ctx = _build_ctx(sub, None, _bench_at(bench_bars, offset), f,
                             _is_etf(h), "沪深300", False, fx_map.get(h.get("currency", "CNY"), 1.0))
            dims = evaluate(ctx, cfg)
            sc = scores_from_dims(dims, cfg)
            tmpl = h.get("template", "longterm")
            row["scores"][h["code"]] = sc.get(tmpl)

        # 大盘择时（轻量口径）
        if index_codes is not None:
            idx_states = []
            for ic in index_codes:
                bars = klines.get(ic) or []
                if len(bars) < 25:
                    continue
                idx_states.append({"code": ic, "state": _light_state(_light_stats(bars, _slice_end(bars, n, i)))})
            pstats = []
            if pool:
                for item in pool:
                    bars = klines.get(item["code"]) or []
                    if len(bars) < 25:
                        continue
                    st = _light_stats(bars, _slice_end(bars, n, i))
                    pstats.append({
                        "above_ma20": bool(st["ma20"] and st["close"] > st["ma20"]),
                        "near_high20": bool(st["high20"] and st["close"] >= st["high20"] * 0.97),
                    })
            bv = None
            br = None
            if len(bench_bars) > 25:
                e = max(_slice_end(bench_bars, n, i), 25)
                sub = bench_bars[:e]
                bv = ind.annualized_vol(sub, 20)
                if len(sub) > 25:
                    last5 = sum(b["volume"] for b in sub[-5:]) / 5
                    prev20 = sum(b["volume"] for b in sub[-25:-5]) / 20
                    br = (last5 / prev20) if prev20 else None
            tm = SC.market_timing(idx_states, pstats, bv, br, cfg)
            row["timing"] = tm.get("total")

        snaps.append(row)
    return snaps


def _bench_at(bench_bars: List[dict], offset: int) -> dict:
    if not bench_bars:
        return {}
    sub = bench_bars[:max(offset, 5)]
    return ind.compute_all(sub)


def _is_etf(h: dict) -> bool:
    code = h.get("code", "")
    return code.startswith(("51", "56", "58", "15", "159")) or "ETF" in (h.get("name") or "")


# ---------------------------------------------------------------- 主流程

def build_dashboard(verbose: bool = True, backfill: bool = True, force: bool = False) -> dict:
    cfg = _read(os.path.join(CONFIG_DIR, "score_config.json")) or {}
    settings = _read(os.path.join(CONFIG_DIR, "settings.json")) or {}
    positions = _read(os.path.join(DATA_DIR, "positions.json")) or {"holdings": [], "watchlist": []}
    trades_doc = _read(os.path.join(DATA_DIR, "trades.json")) or {"trades": []}
    fund_doc = _read(os.path.join(DATA_DIR, "fundamentals.json")) or {"data": {}}
    history = _read(os.path.join(DATA_DIR, "history.json")) or {"snapshots": []}

    fx_map = settings.get("fx", {"CNY": 1.0})
    kline_days = int(settings.get("quote", {}).get("kline_days", 320))
    hard_stop_cny = float(settings.get("risk", {}).get("hard_stop_loss_cny", 15000))
    bench_code = settings.get("market", {}).get("benchmark", "000300.SH")
    indices_cfg = settings.get("market", {}).get("indices", [])
    fund_map = fund_doc.get("data", {})

    holdings = positions.get("holdings", [])
    watchlist = positions.get("watchlist", [])
    errors: List[str] = []

    def log(*a):
        if verbose:
            print(*a)

    # ---------- 取数 ----------
    all_codes = [h["code"] for h in holdings] + [w["code"] for w in watchlist]
    idx_codes = [i["code"] for i in indices_cfg]
    if bench_code not in idx_codes:
        idx_codes.append(bench_code)

    log(f"[取数] 实时行情 {len(all_codes)} 只 + 指数 {len(idx_codes)} 个 …")
    rt_all = Q.fetch_realtime(all_codes + idx_codes)
    manual = Q.load_manual_prices(os.path.join(DATA_DIR, "positions.json"))

    log(f"[取数] 日线（{kline_days}日）…")
    klines: Dict[str, List[dict]] = {}
    for i, c in enumerate(sorted(set(all_codes + idx_codes))):
        kl = Q.fetch_kline(c, kline_days)
        if not kl:
            errors.append(f"{c} 日线获取失败")
        klines[c] = kl

    # ---------- 基准与指数 ----------
    bench_bars = klines.get(bench_code) or []
    bench_t = ind.compute_all(bench_bars) if bench_bars else {}
    bench_state = SC.index_state(bench_t)
    bench_bull = bench_state == "多头"
    bench_vol = bench_t.get("vol20_annual")
    bench_amt_ratio = None
    if len(bench_bars) > 25:
        last5 = sum(b["volume"] for b in bench_bars[-5:]) / 5
        prev20 = sum(b["volume"] for b in bench_bars[-25:-5]) / 20
        bench_amt_ratio = (last5 / prev20) if prev20 else None

    indexes = []
    idx_states = []
    for ic in indices_cfg:
        bars = klines.get(ic["code"]) or []
        t = ind.compute_all(bars) if bars else {}
        rt = rt_all.get(ic["code"]) or {}
        if rt.get("price"):
            t["close"] = rt["price"]
        st = SC.index_state(t)
        idx_states.append({"code": ic["code"], "state": st})
        indexes.append({
            "code": ic["code"], "name": ic.get("name") or rt.get("name") or ic["code"],
            "role": ic.get("role", ""),
            "close": _round(t.get("close"), 2),
            "state": st,
            "chg": _round(rt.get("change_pct") if rt.get("change_pct") is not None else t.get("chg"), 2),
            "chg20": _round(t.get("chg20"), 2),
        })

    # ---------- 池内广度（持仓 + 自选） ----------
    pool_stats = []
    for item in holdings + watchlist:
        bars = klines.get(item["code"]) or []
        t = ind.compute_all(bars) if bars else {}
        rt = rt_all.get(item["code"]) or {}
        close = rt.get("price") or t.get("close")
        if close is None:
            continue
        ma20, h20 = t.get("ma20"), t.get("high20")
        pool_stats.append({
            "code": item["code"],
            "above_ma20": bool(ma20 and close > ma20),
            "near_high20": bool(h20 and close >= h20 * 0.97),
        })

    timing = SC.market_timing(idx_states, pool_stats, bench_vol, bench_amt_ratio, cfg)

    # 提前算 as_of，供 holding_days 估算使用（持仓循环需要该日期）
    as_of_est = None
    for _c in ([holdings[0]["code"]] if holdings else []) + [bench_code]:
        _b = klines.get(_c) or []
        if _b:
            as_of_est = _b[-1]["date"]
            break
    if not as_of_est:
        as_of_est = datetime.now().strftime("%Y-%m-%d")

    # ---------- 持仓 ----------
    out_holdings = []
    for h in holdings:
        code = h["code"]
        bars = klines.get(code) or []
        rt = rt_all.get(code) or {}
        ccy = h.get("currency") or "CNY"
        fx = float(fx_map.get(ccy, 1.0))
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0)
        etf = _is_etf(h)

        close = rt.get("price")
        if close is None:
            close = manual.get(code) or (bars[-1]["close"] if bars else None)
        prev_close = rt.get("prev_close") or (bars[-2]["close"] if len(bars) > 1 else None)

        f = dict(fund_map.get(code) or {})
        ctx = _build_ctx(bars, rt, bench_t, f, etf, "沪深300", bench_bull, fx)
        if close is not None:
            ctx["t"]["close"] = close
        ctx["close"] = close

        all_dims = evaluate(ctx, cfg)
        sc = scores_from_dims(all_dims, cfg)
        tmpl = h.get("template") or "longterm"
        main_score = sc.get(tmpl)
        tmpl_cfg = cfg["templates"].get(tmpl, {})
        status, advice = SC.status_of(main_score, tmpl_cfg)

        mv = close * shares * fx if close else None
        cost_amt = cost * shares * fx
        pnl_amt = (close - cost) * shares * fx if close else None
        pnl_pct = ((close / cost - 1) * 100) if (close and cost) else None
        day_pnl = ((close - prev_close) * shares * fx) if (close and prev_close) else None

        # 硬止损
        hard_stop = None
        if cost and shares and fx:
            hard_stop = cost - (hard_stop_cny / fx) / shares
        chandelier = ctx["t"].get("chandelier")

        # 持仓天数 → 用于时间止损
        holding_days = estimate_holding_days(code, trades_doc, as_of_est)

        # 新版止盈/止损建议（去成本锚定 / ATR 波动率自适应 / 多支撑取严）
        stop_take = SC.compute_stop_take(ctx["t"], ctx, tmpl_cfg, cost)
        st_state = SC.state_label(stop_take)

        # 把"出场纪律"维度塞到对应模板的 dims，让 composite 自动包含
        dd = SC.discipline_dim(stop_take, ctx["t"], ctx, tmpl_cfg, holding_days)
        if dd.get("score") is not None and tmpl in all_dims:
            all_dims[tmpl]["discipline"] = dd
        # 重新算：补齐 discipline 后的分数
        sc = scores_from_dims(all_dims, cfg)
        main_score = sc.get(tmpl)
        status, advice = SC.status_of(main_score, tmpl_cfg)

        signals = []
        for d in (all_dims.get(tmpl) or {}).values():
            if d.get("detail"):
                signals.append(d["detail"])
        if chandelier:
            signals.append(f"吊灯止损线{chandelier:.2f}(10日最高−3×ATR)")
        if stop_take.get("stop_price"):
            signals.append(f"建议止损{stop_take['stop_price']:.2f}"
                           f"(依据:{stop_take.get('stop_method', '')})")
        if stop_take.get("partial_sell_at"):
            signals.append(f"止盈第一目标{stop_take['partial_sell_at']:.2f}, "
                           f"触发可减半仓")

        out_holdings.append({
            "code": code, "name": h.get("name") or rt.get("name") or code,
            "template": tmpl, "shares": shares, "cost": cost, "currency": ccy, "fx": fx,
            "close": _round(close, close < 10 and 3 or 2) if close else None,
            "prev_close": _round(prev_close, 3),
            "chg": _round(rt.get("change_pct") if rt.get("change_pct") is not None
                          else (((close / prev_close - 1) * 100) if (close and prev_close) else None), 2),
            "mv": _round(mv, 0), "cost_amt": _round(cost_amt, 0),
            "pnl": _round(pnl_pct, 1), "pnl_amt": _round(pnl_amt, 0),
            "day_pnl": _round(day_pnl, 0),
            "score": main_score,
            "scores": {k: _round(v, 1) for k, v in sc.items()},
            "dims": {tk: {k: {"score": _round(v.get("score"), 1), "detail": v.get("detail", "")}
                          for k, v in dv.items()} for tk, dv in all_dims.items()},
            "status": status, "advice": advice, "signals": signals,
            "hard_stop": _round(hard_stop, 3),
            "hard_stop_hit": bool(close and hard_stop and close <= hard_stop),
            "chandelier": _round(chandelier, 3),
            "stop_price": _round(stop_take.get("stop_price"), 3),
            "stop_method": stop_take.get("stop_method"),
            "stop_distance_atr": _round(stop_take.get("stop_distance_atr"), 2),
            "stop_distance_pct": _round(stop_take.get("stop_distance_pct"), 1),
            "stop_breached": bool(close and stop_take.get("stop_price")
                                  and close <= stop_take["stop_price"]),
            "take_profit_1": _round(stop_take.get("partial_sell_at"), 3),
            "take_profit_1_method": stop_take.get("tp1_method"),
            "take_profit_2": _round(stop_take.get("take_profit_2"), 3),
            "take_profit_2_method": stop_take.get("tp2_method"),
            "tp1_distance_atr": _round(stop_take.get("tp1_distance_atr"), 2),
            "tp2_distance_atr": _round(stop_take.get("tp2_distance_atr"), 2),
            "tp1_reached": bool(close and stop_take.get("partial_sell_at")
                                and close >= stop_take["partial_sell_at"]),
            "exit_state": st_state,
            "holding_days": holding_days,
            "time_stop_days": tmpl_cfg.get("stop_take_config", {}).get("time_stop_days"),
            "ma20": _round(ctx["t"].get("ma20"), 3),
            "ma60": _round(ctx["t"].get("ma60"), 3),
            "atr": _round(ctx["t"].get("atr"), 3),
            "rsi": _round(ctx["t"].get("rsi"), 1),
            "pe": _round(f.get("pe"), 2), "pb": _round(f.get("pb"), 2),
            "high252": _round(ctx["t"].get("high252"), 3),
            "low252": _round(ctx["t"].get("low252"), 3),
            "error": rt.get("error"),
        })
        if rt.get("error"):
            errors.append(f"{code} {rt['error']}")

    total_mv = sum(x["mv"] or 0 for x in out_holdings)
    total_cost = sum(x["cost_amt"] or 0 for x in out_holdings)
    for x in out_holdings:
        x["weight"] = _round((x["mv"] or 0) / total_mv * 100, 1) if total_mv else None

    # ---------- 已实现盈亏（FIFO） ----------
    trades = sorted(trades_doc.get("trades", []), key=lambda t: t.get("date", ""))
    lots: Dict[str, List[List[float]]] = {}
    realized = 0.0
    trade_rows = []
    for t in trades:
        code = t.get("code")
        ccy = t.get("currency") or "CNY"
        fx = float(fx_map.get(ccy, 1.0))
        shares = float(t.get("shares") or 0)
        price = float(t.get("price") or 0)
        fee = float(t.get("fee") or 0)
        amount = price * shares * fx
        rl = None
        if t.get("side") == "sell" and t.get("type") != "baseline":
            need = shares
            cost_sum = 0.0
            q = lots.get(code, [])
            while need > 0 and q:
                lot = q[0]
                take = min(lot[0], need)
                cost_sum += take * lot[1]
                lot[0] -= take
                need -= take
                if lot[0] <= 1e-9:
                    q.pop(0)
            lots[code] = q
            rl = (price * (shares - need) - cost_sum) * fx - fee * fx
            realized += rl
        elif t.get("side") == "buy":
            lots.setdefault(code, []).append([shares, price])
        trade_rows.append({
            "date": t.get("date"), "side": t.get("side"), "code": code, "name": t.get("name"),
            "shares": shares, "price": price, "currency": ccy, "fee": fee,
            "amount": _round(amount, 0),
            "realized": _round(rl, 0),
            "type": t.get("type") or "normal",
            "note": t.get("note", ""),
        })
    trade_rows.reverse()

    # ---------- 自选 ----------
    out_watch = []
    for w in watchlist:
        code = w["code"]
        bars = klines.get(code) or []
        rt = rt_all.get(code) or {}
        etf = _is_etf(w)
        f = dict(fund_map.get(code) or {})
        ctx = _build_ctx(bars, rt, bench_t, f, etf, "沪深300", bench_bull, 1.0)
        all_dims = evaluate(ctx, cfg)
        sc = scores_from_dims(all_dims, cfg)
        st, adv = SC.status_of(sc.get("trend"), cfg["templates"].get("trend", {}))
        t_now = ctx["t"]
        # 52 周高低位（取 K 线，回退到实时字段）
        high52 = t_now.get("high252") or rt.get("high52")
        low52 = t_now.get("low252") or rt.get("low52")
        close = rt.get("price") or t_now.get("close")
        pos52 = None
        if close and high52 and low52 and high52 != low52:
            pos52 = round((close - low52) / (high52 - low52) * 100, 1)
        out_watch.append({
            "code": code, "name": w.get("name") or rt.get("name") or code,
            "group": w.get("group") or "其他",
            "close": _round(close, 2),
            "chg": _round(rt.get("change_pct"), 2),
            "high252": _round(high52, 3),
            "low252": _round(low52, 3),
            "pos_in_52w": pos52,
            "atr": _round(t_now.get("atr"), 3),
            "ma20": _round(t_now.get("ma20"), 3),
            "ma60": _round(t_now.get("ma60"), 3),
            "volume_ratio": _round(t_now.get("volume_ratio"), 2),
            "scores": {k: _round(v, 1) for k, v in sc.items()},
            "score": _round(sc.get("trend"), 1),
            "lscore": _round(sc.get("longterm"), 1),
            "dscore": _round(sc.get("divergence"), 1),
            "fscore": _round(sc.get("fundamental"), 1),
            "status": st, "advice": adv,
            "signals": [d.get("detail", "") for d in (all_dims.get("trend") or {}).values() if d.get("detail")],
            "dims": {tk: {k: {"score": _round(v.get("score"), 1), "detail": v.get("detail", "")}
                          for k, v in dv.items()} for tk, dv in all_dims.items()},
            "error": rt.get("error"),
        })

    # ---------- 历史快照 ----------
    as_of = as_of_est
    snap = {
        "date": as_of,
        "scores": {x["code"]: x["score"] for x in out_holdings},
        "timing": timing.get("total"),
        "total_mv": _round(total_mv, 0),
        "total_pnl_pct": _round(((total_mv / total_cost - 1) * 100) if total_cost else None, 2),
    }
    prev_snap = None
    snapshots = [s for s in history.get("snapshots", []) if s.get("date") != as_of]
    if snapshots:
        prev_snap = snapshots[-1]
    snapshots.append(snap)
    snapshots.sort(key=lambda s: s.get("date", ""))

    if backfill and len([s for s in snapshots if s["scores"]]) < 5:
        log(f"[回算] 历史评分（最近 {settings.get('history', {}).get('backfill_days', 60)} 个交易日）…")
        bf = backfill_history(holdings, klines, bench_bars, cfg,
                              int(settings.get("history", {}).get("backfill_days", 60)),
                              fund_map, fx_map,
                              index_codes=idx_codes, pool=holdings + watchlist)
        have = {s["date"] for s in snapshots}
        for row in bf:
            if row["date"] not in have and row["scores"]:
                snapshots.append(row)
        snapshots.sort(key=lambda s: s.get("date", ""))

    max_snaps = int(settings.get("history", {}).get("max_snapshots", 500))
    snapshots = snapshots[-max_snaps:]

    # 评分变动
    for x in out_holdings:
        prev = (prev_snap or {}).get("scores", {}).get(x["code"])
        x["score_prev"] = _round(prev, 1) if isinstance(prev, (int, float)) else None
        if isinstance(prev, (int, float)) and x["score"] is not None:
            x["score_change"] = _round(x["score"] - prev, 1)
        else:
            x["score_change"] = None
        tmpl_dims = cfg["templates"].get(x["template"], {}).get("dimensions", [])
        prev_dims = ((prev_snap or {}).get("dims") or {}).get(x["code"], {}).get(x["template"])
        x["change_reason"] = SC.score_change_reason(prev_dims, x["dims"].get(x["template"], {}),
                                                    tmpl_dims, x["score_change"])
    # 保存维度明细用于下次对比
    snap["dims"] = {x["code"]: x["dims"] for x in out_holdings}

    # spark（每只持仓最近 30 个快照的评分序列）
    hist_dates = [s["date"] for s in snapshots]
    for x in out_holdings:
        series = [s["scores"].get(x["code"]) for s in snapshots if s.get("scores")]
        x["spark"] = series[-30:]
    timing_series = [s.get("timing") for s in snapshots]

    # ---------- 组合汇总 ----------
    total_pnl_amt = total_mv - total_cost if total_mv or total_cost else None
    day_pnl_total = sum(x["day_pnl"] or 0 for x in out_holdings)
    by_weight = sorted([x for x in out_holdings if x["weight"]], key=lambda x: -x["weight"])
    top1 = by_weight[0]["weight"] if by_weight else None
    top3 = sum(x["weight"] for x in by_weight[:3]) if by_weight else None
    deep = [{"name": x["name"], "pnl": x["pnl"]} for x in out_holdings
            if x["pnl"] is not None and x["pnl"] <= cfg.get("thresholds", {}).get("deep_loss_pct", -20)]
    hi = [x["name"] for x in out_holdings
          if x["score"] is not None and x["score"] >= cfg.get("thresholds", {}).get("high_score", 70)]
    lo = [x["name"] for x in out_holdings
          if x["score"] is not None and x["score"] < cfg.get("thresholds", {}).get("low_score", 40)]

    portfolio = {
        "total_mv": _round(total_mv, 0),
        "total_cost": _round(total_cost, 0),
        "total_pnl_amt": _round(total_pnl_amt, 0),
        "total_pnl_pct": _round(((total_mv / total_cost - 1) * 100) if total_cost else None, 2),
        "day_pnl": _round(day_pnl_total, 0),
        "realized_pnl": _round(realized, 0),
        "position_count": len(out_holdings),
        "top1_weight": _round(top1, 1),
        "top3_weight": _round(top3, 1),
        "deep_loss": deep,
        "high_score_names": hi,
        "low_score_names": lo,
        "currency": "CNY",
    }

    # ---------- 摘要 ----------
    def _money(v):
        if v is None:
            return "—"
        a = abs(v)
        if a >= 10000:
            return f"{v/10000:.1f}万"
        return f"{v:,.0f}"

    parts = [f"大盘{timing.get('position','—')}"]
    if portfolio["position_count"]:
        parts.append(f"持仓{portfolio['position_count']}只")
        if hi:
            parts.append(f"高分(≥{cfg.get('thresholds',{}).get('high_score',70)}){len(hi)}只{'、'.join(hi)}")
        if lo:
            parts.append(f"低分(<{cfg.get('thresholds',{}).get('low_score',40)}){len(lo)}只{'、'.join(lo)}")
    parts.append(timing.get("note", ""))
    if total_mv:
        txt = f"总市值约{_money(total_mv)}"
        if by_weight:
            txt += f"，第一大持仓{by_weight[0]['name']}占{by_weight[0]['weight']:.0f}%"
            if top3:
                txt += f"、前三占{top3:.0f}%"
        txt += "，持仓分散度" + ("尚可" if (top1 or 0) < 35 else "偏低")
        parts.append(txt)
    if deep:
        parts.append("深套(≤" + str(cfg.get('thresholds', {}).get('deep_loss_pct', -20)) + "%)：" +
                     "、".join(f"{d['name']}{d['pnl']:.0f}%" for d in deep))
    summary = "｜".join(p for p in parts if p) + "。"

    templates_out = []
    for key, tmpl in cfg.get("templates", {}).items():
        templates_out.append({
            "key": key, "name": tmpl.get("name", key), "scene": tmpl.get("scene", ""),
            "rules": " + ".join(
                f"{d['name']}{d.get('max', '')}" for d in tmpl.get("dimensions", [])) or "不做评分，只做触发器",
            "sell_rules": tmpl.get("sell_rules", ""),
            "buy_desc": "；".join(f"{d['name']}({d.get('desc','')})" for d in tmpl.get("dimensions", [])),
            "names": [x["name"] for x in out_holdings if x["template"] == key],
        })

    score_rules = [
        {"name": "基本面/估值评分",
         "basis": f"基准 {cfg['templates']['fundamental'].get('base', 50)} 分 · 最新已披露财报 + 腾讯PE/PB · ETF不适用",
         "rules": "；".join(f"{d['name']}: {d.get('desc','')}" for d in cfg["templates"]["fundamental"]["dimensions"])},
        {"name": "止盈止损建议",
         "basis": "去成本锚定 · ATR(14) 波动率自适应 · 多支撑取严",
         "rules": "止损 = max(收盘 − K×ATR, MA20, MA60, 近段前低+缓冲)；"
                  "止盈 1 = 近段高 − 0.5×ATR(半仓触发)，止盈 2 = 近段高 + 1.5×ATR(突破后清剩余)；"
                  "背离模板改用 2:1 盈亏比目标；长线改以 52 周高为主目标；"
                  f"硬止损(¥{hard_stop_cny:,.0f}单笔浮亏)仍保留为风险兜底"},
        {"name": "出场纪律评分",
         "basis": "满分依模板而异（trend 15 / longterm 12 / divergence 15）",
         "rules": "空间分(止损 ≥2×ATR 满分) + 止损合规 + 止盈可达 + 时间止损"},
    ]

    dashboard = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M") + " (UTC+8)",
        "as_of": as_of,
        "summary": summary,
        "portfolio": portfolio,
        "timing": timing,
        "indexes": indexes,
        "holdings": out_holdings,
        "watchlist": out_watch,
        "trades": trade_rows,
        "templates": templates_out,
        "score_rules": score_rules,
        "score_config": cfg,
        "settings": {"fx": fx_map, "hard_stop_cny": hard_stop_cny,
                     "high_score": cfg.get("thresholds", {}).get("high_score", 70),
                     "low_score": cfg.get("thresholds", {}).get("low_score", 40)},
        "history": {
            "dates": hist_dates,
            "timing": timing_series,
            "series": {x["code"]: [s["scores"].get(x["code"]) for s in snapshots]
                       for x in out_holdings},
            "names": {x["code"]: x["name"] for x in out_holdings},
        },
        "errors": errors,
    }

    # 保护：行情大面积取不到时不要覆盖已有数据（定时任务尤其重要，否则线上会被刷成空）
    missing = [x["name"] for x in out_holdings if x["close"] is None]
    if out_holdings and len(missing) > len(out_holdings) * 0.5 and not force:
        raise RuntimeError(
            f"超过半数标的未取到价格（{len(missing)}/{len(out_holdings)}：{'、'.join(missing[:6])}），"
            f"已保留上次数据不覆盖。请检查网络/行情接口，或加 --force 强制写入。"
        )

    _write(os.path.join(DATA_DIR, "dashboard.json"), dashboard)
    _write(os.path.join(DATA_DIR, "history.json"), {"snapshots": snapshots})
    log(f"[完成] as_of={as_of}  持仓={len(out_holdings)}  自选={len(out_watch)}  快照={len(snapshots)}")
    if errors:
        log("[警告] " + "；".join(errors[:5]))
    return dashboard


if __name__ == "__main__":
    build_dashboard()

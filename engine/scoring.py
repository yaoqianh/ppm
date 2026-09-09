# -*- coding: utf-8 -*-
"""
评分引擎（配置驱动）
---------------------
所有维度与权重来自 config/score_config.json，前端页面使用同一套公式与同一份配置，
因此「在页面上调权重」与「跑 run_update.py 重算」结果一致。

核心公式
    composite = 100 * Σ(score_i / max_i * weight_i) / Σ(weight_i)
    仅统计 enabled=True 且 有数据(available=True) 的维度。
    默认 weight_i = max_i，此时 composite 等于原始总分。

    基本面模板为 base_plus_adjust 模式：
    raw = base + Σ(adj_i * weight_i / default_weight_i)，再裁剪到 0~100
"""

from typing import Dict, List, Optional, Sequence, Any

from . import indicators as ind


Number = Optional[float]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _safe(v, default=None):
    return default if v is None else v


# ---------------------------------------------------------------- 通用工具

def price_percentile(bars: Sequence[dict], window: int = 252) -> Optional[float]:
    """当前收盘价在最近 window 日收盘序列中的分位（0~1，越低越便宜）"""
    if not bars:
        return None
    seg = bars[-window:] if len(bars) > window else bars
    closes = sorted(b["close"] for b in seg)
    cur = bars[-1]["close"]
    if not closes:
        return None
    below = sum(1 for c in closes if c < cur)
    return below / len(closes)


def _norm(value, lo, hi):
    """把 value 从 [lo, hi] 线性映射到 [0, 1]，反向映射用 lo>hi"""
    if value is None:
        return None
    if lo == hi:
        return 0.0
    return _clamp((value - lo) / (hi - lo), 0.0, 1.0)


# ---------------------------------------------------------------- 子维度计算

def trend_dimensions(t: dict, ctx: dict) -> Dict[str, dict]:
    """趋势跟踪五维：结构25 / 动量30 / 量能15 / 滤网15 / 位置15"""
    dims: Dict[str, dict] = {}

    # 结构 25 = 均线多头15 + MA20上行10
    s = 0.0
    parts = []
    if t.get("bull_align"):
        s += 15
        parts.append("均线多头排列 15/15")
    elif t.get("ma5") and t.get("ma20") and t["ma5"] > t["ma20"]:
        s += 8
        parts.append("均线部分多头 8/15")
    else:
        parts.append("均线空头排列 0/15")
    if t.get("ma20_up"):
        s += 10
        parts.append("MA20 上行 10/10")
    else:
        parts.append("MA20 走平/下行 0/10")
    dims["structure"] = {"score": round(s, 1), "detail": "；".join(parts)}

    # 动量 30 = 突破20日新高15 + RS强于沪深300 15
    m = 0.0
    mp = []
    h20, close = t.get("high20"), t.get("close")
    if h20 and close:
        gap = close / h20 - 1
        if gap >= -0.005:
            m += 15
            mp.append(f"突破20日高点{h20:g} 15/15")
        elif gap >= -0.03:
            m += 8
            mp.append(f"逼近20日新高({h20:g}, 差距<3%) 8/15")
        else:
            mp.append(f"未突破20日高点{h20:g} 0/15")
    else:
        mp.append("20日高点数据不足 0/15")

    rs = ctx.get("rs_vs_benchmark")
    if rs is None:
        mp.append("相对强度数据不足 0/15")
    else:
        if rs >= 5:
            v = 15
        elif rs >= 0:
            v = 10
        elif rs >= -5:
            v = 5
        elif rs >= -10:
            v = 2
        else:
            v = 0
        m += v
        rel = "强于" if rs >= 0 else "弱于"
        mp.append(f"RS{rel}{ctx.get('benchmark_name','沪深300')} {rs:+.1f}pct {v}/15")
    dims["momentum"] = {"score": round(m, 1), "detail": "；".join(mp)}

    # 量能 15
    vr = t.get("volume_ratio")
    if vr is None:
        vv, vd = 0.0, "量能数据不足 0/15"
    elif vr >= 1.5:
        vv, vd = 15, f"突破放量(量比{vr:.1f}) 15/15"
    elif vr >= 1.2:
        vv, vd = 8, f"放量但未突破(量比{vr:.1f}) 8/15"
    else:
        vv, vd = round(_clamp(vr * 5, 0, 8), 1), f"量能不足(量比{vr:.1f}) 0/15"
    dims["volume"] = {"score": vv, "detail": vd}

    # 滤网 15 = 大盘滤网8 + 长周期位置7
    f = 0.0
    fp = []
    if ctx.get("benchmark_bull"):
        f += 8
        fp.append("大盘滤网通过 8/8")
    else:
        fp.append(f"{ctx.get('benchmark_name','沪深300')}空头(大盘滤网不通过) 0/8")
    ma120 = t.get("ma120")
    if ma120 and close:
        if close > ma120:
            f += 7
            fp.append("股价处于长周期多头区 7/7")
        else:
            fp.append("股价处于长周期空头区 0/7")
    else:
        fp.append("半年线数据不足 0/7")
    dims["filter"] = {"score": round(f, 1), "detail": "；".join(fp)}

    # 位置 15 = 距52周高点
    h252 = t.get("high252") or ctx.get("high52")
    if h252 and close:
        d = close / h252 - 1
        v = round(15 * _clamp((d + 0.35) / 0.20, 0, 1), 1)
        dims["position"] = {"score": v, "detail": f"距52周高点{h252:g} {d*100:+.0f}%"}
    else:
        dims["position"] = {"score": 0.0, "detail": "52周高点数据不足 0/15"}

    return dims


def longterm_dimensions(t: dict, ctx: dict) -> Dict[str, dict]:
    """长线监控四维：估值分位35 / 位置安全垫20 / 周线结构20 / 基本面质量25"""
    dims: Dict[str, dict] = {}
    bars = ctx.get("bars") or []
    close = t.get("close")

    # 估值分位 35
    pct = price_percentile(bars, 252)
    if pct is None:
        dims["valuation"] = {"score": None, "detail": "估值分位数据不足"}
    else:
        buckets = [(0.10, 35), (0.20, 30), (0.30, 26), (0.40, 21),
                   (0.50, 16), (0.60, 12), (0.70, 8), (0.80, 5), (0.90, 2), (1.01, 0)]
        v = 0
        for edge, val in buckets:
            if pct < edge:
                v = val
                break
        src = "价格分位" if ctx.get("is_etf") or not ctx.get("pe") else "PE/价格分位"
        dims["valuation"] = {"score": float(v),
                             "detail": f"{src}{pct*100:.0f}%(窗口252日) → {v}分"}

    # 位置安全垫 20
    low252 = t.get("low252") or ctx.get("low52")
    if low252 and close:
        d = close / low252 - 1
        v = round(20 * _clamp(1 - (d - 0.10) / 0.30, 0, 1), 1)
        dims["cushion"] = {"score": v, "detail": f"距52周低点{low252:g} {d*100:+.1f}% → {v}分"}
    else:
        dims["cushion"] = {"score": None, "detail": "52周低点数据不足"}

    # 周线结构 20 = MACD多头8 + 年线上8 + 年线上行4
    w = 0.0
    wp = []
    if t.get("w_macd_bull"):
        w += 8
        wp.append("周线MACD多头 +8")
    ma250 = t.get("ma250")
    if ma250 and close:
        if close > ma250:
            w += 8
            wp.append("位于年线上方 +8")
        else:
            wp.append("⚠卖出纪律: 跌破年线MA250且3日未收回")
    else:
        wp.append("年线数据不足")
    if t.get("ma250_up"):
        w += 4
        wp.append("年线上行 +4")
    dims["weekly"] = {"score": round(w, 1), "detail": "；".join(wp)}

    # 基本面质量 25
    q = ctx.get("quality_norm")
    if q is None:
        dims["quality"] = {"score": None, "detail": "基本面质量数据不足"}
    else:
        v = round(25 * (q ** 1.3), 1)
        dims["quality"] = {"score": v, "detail": f"基本面质量 → {v}分"}

    return dims


def divergence_dimensions(t: dict, ctx: dict) -> Dict[str, dict]:
    """背离反转：Elder 五维，各 0/1/2 分 × 10"""
    bars = ctx.get("bars") or []
    dims: Dict[str, dict] = {}

    w_now = ctx.get("w_impulse") or "blue"
    w_prev = ctx.get("w_impulse_prev")
    d_now = ctx.get("d_impulse") or "blue"
    d_prev = ctx.get("d_impulse_prev")
    CN = {"green": "绿", "red": "红", "blue": "蓝"}

    dims["w_impulse"] = {
        "score": float(ind.impulse_score(w_now, w_prev) * 10),
        "detail": f"周线强力系统 {CN.get(w_now, w_now)} → {ind.impulse_score(w_now, w_prev)*10}分"}
    dims["d_impulse"] = {
        "score": float(ind.impulse_score(d_now, d_prev) * 10),
        "detail": f"日线强力系统 {CN.get(d_now, d_now)} → {ind.impulse_score(d_now, d_prev)*10}分"}

    # 价格 vs 价值（EMA13~26 区间）
    e13, e26, close = t.get("ema13"), t.get("ema26"), t.get("close")
    if e13 and e26 and close:
        lo, hi = min(e13, e26), max(e13, e26)
        if close < lo:
            v, txt = 20, "价格低于EMA13~26区间 20分"
        elif close <= hi:
            v, txt = 10, "价格处于EMA13~26区间内 10分"
        else:
            v, txt = 0, "价格高于EMA13~26区间 0分"
    else:
        v, txt = 0, "EMA 数据不足"
    dims["price_value"] = {"score": float(v), "detail": txt}

    # 假突破（将要发生2 / 已发生1 / 无0）
    h20 = t.get("high20")
    if h20 and close:
        gap = close / h20 - 1
        if gap >= -0.03:
            fb, txt = 2, "逼近前高, 假突破将要发生 20分"
        elif gap >= -0.10 and ctx.get("touched_high_recently"):
            fb, txt = 1, "已发生冲高回落(假突破) 10分"
        else:
            fb, txt = 0, "无假突破结构 0分"
    else:
        fb, txt = 0, "数据不足"
    dims["false_break"] = {"score": float(fb * 10), "detail": txt}

    # 背离完备性：日线/周线 MACD 柱底背离各 1 分
    dv = 0
    dp = []
    if ctx.get("daily_bullish_divergence"):
        dv += 1
        dp.append("日线MACD柱底背离 +1")
    if ctx.get("weekly_bullish_divergence"):
        dv += 1
        dp.append("周线MACD柱底背离 +1")
    dims["divergence_ok"] = {"score": float(dv * 10),
                             "detail": "；".join(dp) if dp else "无有效背离结构 0分"}

    return dims


def fundamental_dimensions(ctx: dict) -> Dict[str, dict]:
    """基本面/估值：基准 50 分 + 六项调整"""
    f = ctx.get("fundamentals") or {}
    dims: Dict[str, dict] = {}

    def adj(key, value, rules):
        """rules: [(阈值, 分值)] 从高分到低分"""
        if value is None:
            return None, "无数据"
        for test, val, label in rules:
            if test(value):
                return float(val), label
        return 0.0, "未命中档位"

    roe, roe_l = adj("roe", f.get("roe"), [
        (lambda v: v >= 15, 10, "ROE≥15% +10"),
        (lambda v: v >= 8, 5, "ROE≥8% +5"),
        (lambda v: v < 0, -15, "ROE亏损 −15"),
        (lambda v: True, 0, "ROE 0~8% +0"),
    ])
    dims["roe"] = {"score": roe, "detail": f"ROE年化{_fmt_pct(f.get('roe'))} → {roe_l}"}

    nm, nm_l = adj("net_margin", f.get("net_margin"), [
        (lambda v: v >= 20, 5, "净利率≥20% +5"),
        (lambda v: v < 0, -10, "净利率为负 −10"),
        (lambda v: True, 0, "净利率 0~20% +0"),
    ])
    dims["net_margin"] = {"score": nm, "detail": f"净利率{_fmt_pct(f.get('net_margin'))} → {nm_l}"}

    rev, rev_l = adj("rev_yoy", f.get("rev_yoy"), [
        (lambda v: v >= 20, 10, "营收YoY≥20% +10"),
        (lambda v: v >= 0, 5, "营收YoY≥0% +5"),
        (lambda v: v <= -10, -10, "营收YoY≤−10% −10"),
        (lambda v: True, -5, "营收YoY为负 −5"),
    ])
    dims["rev_yoy"] = {"score": rev, "detail": f"营收YoY{_fmt_pct(f.get('rev_yoy'))} → {rev_l}"}

    py, py_l = adj("profit_yoy", f.get("profit_yoy"), [
        (lambda v: v >= 30, 10, "归母YoY≥30% +10"),
        (lambda v: v >= 0, 5, "归母YoY≥0% +5"),
        (lambda v: v <= -30, -15, "归母YoY≤−30% −15"),
        (lambda v: True, -5, "归母YoY为负 −5"),
    ])
    dims["profit_yoy"] = {"score": py, "detail": f"归母YoY{_fmt_pct(f.get('profit_yoy'))} → {py_l}"}

    pe = f.get("pe")
    if pe is None or pe <= 0:
        pe_v, pe_l = (-10.0, "PE亏损/无数据 −10") if pe is not None and pe <= 0 else (None, "无PE")
    else:
        pe_v, pe_l = adj("pe", pe, [
            (lambda v: v <= 15, 10, "PE≤15 +10"),
            (lambda v: v <= 30, 5, "PE 15–30 +5"),
            (lambda v: v > 80, -5, "PE>80 −5"),
            (lambda v: True, 0, "PE 30–80 +0"),
        ])
    dims["pe"] = {"score": pe_v, "detail": f"PE(TTM){_fmt_num(pe)} → {pe_l}"}

    pb = f.get("pb")
    pb_v, pb_l = adj("pb", pb, [
        (lambda v: v < 1.5, 5, "PB<1.5 +5"),
        (lambda v: v > 8, -5, "PB>8 −5"),
        (lambda v: True, 0, "PB 1.5–8 +0"),
    ])
    dims["pb"] = {"score": pb_v, "detail": f"PB{_fmt_num(pb)} → {pb_l}"}

    return dims


def _fmt_pct(v):
    return "—" if v is None else f"{v:+.1f}%"


def _fmt_num(v):
    return "—" if v is None else f"{v:g}"


# ---------------------------------------------------------------- 综合评分

def composite(dims: Dict[str, dict], tmpl_cfg: dict) -> Optional[float]:
    """按配置权重把子维度合成为 0~100 分"""
    mode = tmpl_cfg.get("mode", "weighted_sum")
    cfg_dims = {d["key"]: d for d in tmpl_cfg.get("dimensions", [])}

    if mode == "base_plus_adjust":
        base = tmpl_cfg.get("base", 50)
        total = float(base)
        for key, cfg in cfg_dims.items():
            if not cfg.get("enabled", True):
                continue
            got = dims.get(key) or {}
            if got.get("score") is None:
                continue
            w = cfg.get("weight", cfg.get("max", 1))
            dw = cfg.get("max", 1) or 1
            total += got["score"] * (w / dw) if dw else got["score"]
        return round(_clamp(total, 0, 100), 1)

    num = den = 0.0
    for key, cfg in cfg_dims.items():
        if not cfg.get("enabled", True):
            continue
        got = dims.get(key) or {}
        sc = got.get("score")
        if sc is None:
            continue
        mx = cfg.get("max") or 1
        w = cfg.get("weight", mx)
        num += (sc / mx) * w
        den += w
    if den == 0:
        return None
    return round(_clamp(100 * num / den, 0, 100), 1)


def status_of(score: Optional[float], tmpl_cfg: dict):
    """按配置档位给出状态与操作参考"""
    if score is None:
        return "无评分", "该模板无评分维度或未取到数据"
    for tier in tmpl_cfg.get("tiers", []):
        if score >= tier.get("min", 0):
            return tier.get("status", ""), tier.get("advice", "")
    return "", ""


def score_change_reason(prev_dims: Optional[dict], cur_dims: Dict[str, dict],
                        cfg_dims: List[dict], delta: Optional[float]) -> str:
    """生成评分变动说明：列出变化最大的若干子维度"""
    if delta is None:
        return "首次记录，暂无对比基准" if not prev_dims else "较上一交易日无变化"
    if not prev_dims:
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        return f"{arrow}{abs(delta):.1f} 分（历史快照未留存维度明细，仅记录总分）"
    if abs(delta) < 0.05:
        return "较上一交易日评分基本持平"
    changes = []
    for cfg in cfg_dims:
        key = cfg["key"]
        p = (prev_dims.get(key) or {}).get("score")
        c = (cur_dims.get(key) or {}).get("score")
        if p is None or c is None:
            continue
        d = c - p
        if abs(d) < 0.05:
            continue
        changes.append((abs(d), f"{cfg.get('name', key)} {p:g}→{c:g}({d:+.1f})"))
    changes.sort(reverse=True, key=lambda x: x[0])
    if not changes:
        return f"较上一交易日 {delta:+.1f} 分（权重口径变化）"
    arrow = "↑" if delta > 0 else "↓"
    return f"{arrow}{abs(delta):.1f} 分：" + "；".join(t for _, t in changes[:3])


# ---------------------------------------------------------------- 大盘择时

def market_timing(index_stats: List[dict], pool_stats: List[dict],
                  bench_vol: Optional[float], bench_amount_ratio: Optional[float],
                  cfg: dict) -> dict:
    """计算大盘五维择时评分"""
    mt = cfg["market_timing"]
    dims_cfg = {d["key"]: d for d in mt["dimensions"]}
    raw: Dict[str, dict] = {}

    n = max(len(index_stats), 1)
    bull = sum(1 for x in index_stats if x.get("state") == "多头")
    bear = sum(1 for x in index_stats if x.get("state") == "空头")
    mid = n - bull - bear
    raw["trend"] = {"score": float(bull * 8 + mid * 4),
                    "detail": f"多头{bull} · 震荡{mid} · 空头{bear}(共{n}指数)"}

    r = bench_amount_ratio
    if r is None:
        vr, vd = 0.0, "量能数据不足"
    elif r >= 1.5:
        vr, vd = 20, f"近5日量能/20日均量 = {r:.2f}(显著放量)"
    elif r >= 1.2:
        vr, vd = 16, f"近5日量能/20日均量 = {r:.2f}(温和放量)"
    elif r >= 1.0:
        vr, vd = 12, f"近5日量能/20日均量 = {r:.2f}(持平)"
    elif r >= 0.8:
        vr, vd = 10, f"近5日量能/20日均量 = {r:.2f}(略缩)"
    else:
        vr, vd = 4, f"近5日量能/20日均量 = {r:.2f}(明显缩量)"
    raw["volume"] = {"score": vr, "detail": vd}

    if pool_stats:
        above = sum(1 for x in pool_stats if x.get("above_ma20"))
        p = above / len(pool_stats)
        raw["breadth"] = {"score": round(20 * p, 1),
                          "detail": f"持仓+自选 {above}/{len(pool_stats)} 只站上MA20({p*100:.0f}%)"}
    else:
        raw["breadth"] = {"score": None, "detail": "池内标的数据不足"}

    v = bench_vol
    if v is None:
        raw["vol"] = {"score": None, "detail": "波动率数据不足"}
    else:
        lvl = "偏低" if v < 12 else ("正常" if v < 22 else "偏高")
        raw["vol"] = {"score": round(_clamp(10 * (28 - v) / 18, 0, 10), 1),
                      "detail": f"沪深300 20日年化波动率 {v:.1f}%({lvl})"}

    if pool_stats:
        nh = sum(1 for x in pool_stats if x.get("near_high20"))
        p = nh / len(pool_stats)
        raw["newhigh"] = {"score": round(10 * p, 1),
                          "detail": f"池内 {nh}/{len(pool_stats)} 只处20日新高附近({p*100:.0f}%)"}
    else:
        raw["newhigh"] = {"score": None, "detail": "池内标的数据不足"}

    dims_out = []
    for d in mt["dimensions"]:
        got = raw.get(d["key"]) or {}
        dims_out.append({
            "key": d["key"], "name": d["name"], "max": d.get("max"),
            "score": got.get("score"), "detail": got.get("detail", ""),
        })

    total = composite({k: v for k, v in raw.items()}, mt)
    pos, note = "", ""
    for tier in mt.get("position_tiers", []):
        if (total or 0) >= tier.get("min", 0):
            pos, note = tier.get("position", ""), tier.get("note", "")
            break

    return {"total": total, "dims": dims_out, "position": pos, "note": note,
            "raw": raw}


def index_state(t: dict) -> str:
    """多头 / 震荡 / 空头"""
    close, ma20 = t.get("close"), t.get("ma20")
    if close is None or ma20 is None:
        return "震荡"
    if close > ma20 and t.get("ma20_up"):
        return "多头"
    if close < ma20 and not t.get("ma20_up"):
        return "空头"
    return "震荡"

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
        # ETF 无财报属正常缺失，文案要和数据缺失区分开
        detail = "ETF 不适用" if ctx.get("is_etf") else "基本面质量数据不足"
        dims["quality"] = {"score": None, "detail": detail}
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
    is_etf = bool(ctx.get("is_etf"))
    dims: Dict[str, dict] = {}

    def adj(key, value, rules, na_label=None):
        """rules: [(阈值, 分值)] 从高分到低分

        na_label: 无数据时的说明文案。ETF 没有财报，属正常缺失，
        文案要区分开，避免和"数据没抓到"混为一谈。
        """
        if value is None:
            return None, (na_label or ("ETF 不适用" if is_etf else "无数据"))
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


# ---------------------------------------------------------------- 止盈止损 / 出场纪律
# 设计原则（来自 wb-finance-skill/stop-discipline.md）：
#   1. **去成本锚定**：成本价不是市场关心的变量。止损位不应该基于「浮亏多少」
#      反推，而是基于当前价格离关键支撑的偏离。
#   2. **波动率自适应**：ATR(14) 反映个股的真实波动，比固定百分比更适合不同波动
#      个性（高波动用更大缓冲，低波动用更紧）。
#   3. **多支撑取严**：候选支撑位（ATR 反推价、MA20、MA60、近段前低）取其中
#      **最高**的那条——保证任何一条支撑被破都出局。
#   4. **止盈分批**：第一目标(60日高/52周高 − buffer)触发减半，剩余部分用吊灯
#      或目标 2 跟踪。
#   5. **时间止损**：横盘超过模板阈值 → 降仓或退出（释放资金成本）。

from typing import Optional, Sequence  # noqa: E402


def _safe_atr(closes: Sequence[float], highs: Sequence[float], lows: Sequence[float], n: int = 14) -> Optional[float]:
    """手动 ATR（若 indicators 模块已经算好就直接传）"""
    if not closes or len(closes) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if len(trs) < n:
        return None
    a = sum(trs[-n:]) / n
    return a if a > 0 else None


def compute_stop_take(t: dict, ctx: dict, tmpl_cfg: dict,
                      avg_cost: Optional[float] = None) -> dict:
    """
    根据模板的 stop_take_config 计算止损 / 止盈价位。

    返回：{
        stop_price: 跌破即出局（取所有候选支撑位中"最高"那条），
        partial_sell_at: 第一止盈位（半仓触发），
        take_profit_2: 第二止盈位（清剩余仓位），
        stop_distance_pct: (close - stop) / close × 100, 留有空间越大该值越大,
        stop_distance_atr: (close - stop) / ATR(14), 0~3，越小越接近触发,
        tp1_distance_atr: (partial_sell_at - close) / ATR(14),
        stop_method: 实际生效的支撑位名(显示用),
        tp1_method, tp2_method,
    }
    """
    cfg = (tmpl_cfg or {}).get("stop_take_config", {}) or {}
    close = t.get("close")
    atr = t.get("atr")
    ma20, ma60, ma250 = t.get("ma20"), t.get("ma60"), t.get("ma250")
    high20, low20 = t.get("high20"), t.get("low20")
    high60 = t.get("high60")
    k = float(cfg.get("atr_multiplier") or 1.5)

    out: dict = {
        "stop_price": None, "partial_sell_at": None, "take_profit_2": None,
        "stop_distance_pct": None, "stop_distance_atr": None, "tp1_distance_atr": None,
        "tp2_distance_atr": None,
        "stop_method": None, "tp1_method": None, "tp2_method": None,
        "atr": atr, "atr_multiplier": k,
    }
    if close is None or atr is None or atr <= 0:
        return out

    # ---------------- 止损 ----------------
    # 多支撑取严（max）：任何一条被破都出局
    cands = []
    atr_stop = close - k * atr
    cands.append((atr_stop, f"收盘 − {k:g}×ATR({atr:.2f})"))
    if ma20:
        cands.append((ma20, "MA20"))
    if ma60:
        cands.append((ma60, "MA60"))
    if low20:
        # 给前低加点 buffer，避免插针
        buffer = max(atr * 0.3, close * 0.005)
        cands.append((low20 + buffer, f"近20日前低 + {buffer:.2f}"))
    # 取最高（即最严 = 最接近当前价）的支撑
    valid = [(p, name) for p, name in cands if p and p > 0]
    if valid:
        out["stop_price"], out["stop_method"] = max(valid, key=lambda x: x[0])
        if out["stop_price"] > close:
            out["stop_price"] = close * 0.97  # 上限封顶，最多比 close 低 3%
        out["stop_distance_atr"] = (close - out["stop_price"]) / atr
        out["stop_distance_pct"] = (close - out["stop_price"]) / close * 100

    # ---------------- 止盈 ----------------
    # tp1：60日高 − 0.5×ATR，留点回撤空间
    if high60:
        tp1 = high60 - 0.5 * atr
        out["partial_sell_at"] = tp1
        out["tp1_method"] = f"60日高({high60:.2f}) − 0.5×ATR"
        out["tp1_distance_atr"] = (tp1 - close) / atr
    elif high20:
        tp1 = high20 - 0.5 * atr
        out["partial_sell_at"] = tp1
        out["tp1_method"] = f"20日高({high20:.2f}) − 0.5×ATR"
        out["tp1_distance_atr"] = (tp1 - close) / atr

    # tp2：60日高 + 1.5×ATR，强势突破后才到
    if high60:
        tp2 = high60 + 1.5 * atr
    elif high20:
        tp2 = high20 + 1.5 * atr
    else:
        tp2 = None
    if tp2:
        out["take_profit_2"] = tp2
        out["tp2_method"] = f"近段高 + 1.5×ATR"
        out["tp2_distance_atr"] = (tp2 - close) / atr

    # ---------------- 模板特异性覆盖 ----------------
    # longterm 用 52 周高（来自接口 high52 / 自算 high252）作为主目标
    h52 = t.get("high252") or ctx.get("high52")
    if h52 and tmpl_cfg.get("name", "").startswith("长线"):
        out["partial_sell_at"] = h52 - 2 * atr
        out["tp1_method"] = f"52周高({h52:.2f}) − 2×ATR"
        out["take_profit_2"] = h52
        out["tp2_method"] = f"52周高({h52:.2f})"
        if out["partial_sell_at"]:
            out["tp1_distance_atr"] = (out["partial_sell_at"] - close) / atr
        if out["take_profit_2"]:
            out["tp2_distance_atr"] = (out["take_profit_2"] - close) / atr

    # divergence：用 2:1 盈亏比替代（按入场价；这里用入场价近似为 cost）
    if tmpl_cfg.get("name", "").startswith("背离") and avg_cost:
        rr_target = avg_cost + 2 * (avg_cost - (out["stop_price"] or avg_cost * 0.95))
        out["partial_sell_at"] = rr_target
        out["tp1_method"] = f"成本 + 2×ATR(2:1 盈亏比)"
        out["tp1_distance_atr"] = (rr_target - close) / atr
        out["take_profit_2"] = avg_cost + 3.5 * (avg_cost - (out["stop_price"] or avg_cost * 0.95))
        out["tp2_method"] = f"成本 + 3.5×ATR"

    return out


def discipline_dim(stop_take: dict, t: dict, ctx: dict, tmpl_cfg: dict,
                   holding_days: Optional[int] = None) -> dict:
    """
    出场纪律维度评分（满分依模板而异，由 tmpl_cfg.dimensions[discipline].max 决定）。

    拆分（每项满分加总后用 min(total, cfg_max) 收口）：
      - 空间分：止损位离现价 ≥ 2×ATR 满分；< 0.5×ATR 0 分
      - 止损合规分：当前价未跌破止损；当日跌破 0 分
      - 止盈可达分：到达 TP1 距离 ≤ 1×ATR 接近目标，5 分；超过 5×ATR 0 分
      - 时间止损分：横盘未超阈值

    返回：{"score": 0~cfg_max, "detail": "<人类可读明细>"}
    """
    cfg = (tmpl_cfg or {}).get("stop_take_config", {}) or {}
    close = t.get("close")
    atr = stop_take.get("atr") or t.get("atr")
    # 从 cfg 中读取真正模板允许的 discipline 维度 max（趋势 15 / 长线 12 / 背离 15）
    cfg_max = 15
    for d in (tmpl_cfg or {}).get("dimensions", []):
        if d.get("key") == "discipline":
            cfg_max = float(d.get("max") or 15)
            break

    detail_parts = []
    s = 0.0

    if close is None or atr is None or atr <= 0:
        return {"score": None, "detail": "止损/止盈建议计算依赖数据不足"}

    # ---- 1) 止损空间分（按距离分级）----
    sd_atr = stop_take.get("stop_distance_atr")
    if sd_atr is None:
        space_v, space_d = 0.0, "止损位未知"
    elif sd_atr >= 2.0:
        space_v, space_d = 5.0, f"止损安全(距 {sd_atr:.1f}×ATR)"
    elif sd_atr >= 1.0:
        space_v, space_d = 3.5, f"止损缓冲中等(距 {sd_atr:.1f}×ATR)"
    elif sd_atr >= 0.5:
        space_v, space_d = 1.5, f"止损贴近(距 {sd_atr:.1f}×ATR)"
    else:
        space_v, space_d = 0.0, f"⚠ 已触及或跌破止损(距 {sd_atr:.1f}×ATR)"
    s += space_v
    detail_parts.append(space_d)

    # ---- 2) 止损合规 ----
    sp = stop_take.get("stop_price")
    if sp and close <= sp * 1.005:
        comp_v, comp_d = 0.0, "已破止损位(收盘 ≤ 止损线)"
    elif sp and close <= sp * 1.02:
        comp_v, comp_d = 1.0, "距止损 ≤2%(危险区)"
    elif sp and ctx.get("chg", 0) and ctx["chg"] < -3 and close < sp * 1.05:
        comp_v, comp_d = 2.0, f"当日跌{ctx['chg']:.1f}%接近止损"
    else:
        comp_v, comp_d = 3.0, "未破止损"
    s += comp_v
    detail_parts.append(comp_d)

    # ---- 3) 止盈可达 ----
    tp1_atr = stop_take.get("tp1_distance_atr")
    if tp1_atr is None:
        tp_v, tp_d = 0.0, "止盈位未知"
    elif tp1_atr <= 0.3:
        tp_v, tp_d = 5.0, f"已触及 TP1(距{tp1_atr:.1f}×ATR)"
    elif tp1_atr <= 1.0:
        tp_v, tp_d = 4.0, f"接近 TP1(距{tp1_atr:.1f}×ATR)"
    elif tp1_atr <= 3.0:
        tp_v, tp_d = 2.0, f"可触 TP1(距{tp1_atr:.1f}×ATR)"
    elif tp1_atr <= 5.0:
        tp_v, tp_d = 1.0, f"TP1 较远(距{tp1_atr:.1f}×ATR)"
    else:
        tp_v, tp_d = 0.0, f"TP1 过远(距{tp1_atr:.1f}×ATR)"
    s += tp_v
    detail_parts.append(tp_d)

    # ---- 4) 时间止损 ----
    time_days = cfg.get("time_stop_days") or 60
    if holding_days is None:
        time_v, time_d = 0.0, "建仓日未知(无法做时间止损评分)"
    else:
        ratio = holding_days / time_days
        if ratio < 0.5:
            time_v, time_d = 2.0, f"持仓{holding_days}日 < 阈值{time_days}日的一半"
        elif ratio < 1.0:
            time_v, time_d = 1.0, f"持仓{holding_days}日 / {time_days}"
        else:
            time_v, time_d = 0.0, f"⚠ 持仓{holding_days}日 已超阈值{time_days}日，时间止损触发"
    s += time_v
    detail_parts.append(time_d)

    return {"score": round(_clamp(s, 0, cfg_max), 1),
            "detail": "｜".join(detail_parts)}


def state_label(stop_take: dict) -> dict:
    """给前端卡片用的快捷状态标签"""
    sd_atr = stop_take.get("stop_distance_atr")
    tp1_atr = stop_take.get("tp1_distance_atr")

    if sd_atr is not None and sd_atr <= 0.2:
        return {"level": "danger", "text": "已触发止损"}
    if tp1_atr is not None and 0 < tp1_atr <= 0.3:
        return {"level": "warm", "text": "已触止盈位"}
    if sd_atr is not None and sd_atr <= 0.5:
        return {"level": "warn", "text": "止损敏感区"}
    if sd_atr is not None and sd_atr < 1.0:
        return {"level": "watch", "text": "止损缓冲偏紧"}
    return {"level": "ok", "text": "建议有效"}


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

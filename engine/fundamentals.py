"""基本面财报数据抓取。

数据源：东方财富 datacenter（业绩报表 RPT_LICO_FN_CPD）
    WEIGHTAVG_ROE        加权平均净资产收益率（**单期**，需年化）
    YSTZ                 营业收入同比增长率 (%)
    SJLTZ                净利润同比增长率 (%)
    TOTAL_OPERATE_INCOME 营业总收入
    PARENT_NETPROFIT     归属母公司净利润

覆盖范围：仅 A 股。港股与 ETF 该接口不返回数据，仍需手工维护
    data/fundamentals.json（ETF 本就无财报，属正常缺失）。

仅用标准库 urllib，无需 pip install。
"""

import json
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT = "RPT_LICO_FN_CPD"
COLS = "SECURITY_CODE,SECURITY_NAME_ABBR,REPORTDATE,WEIGHTAVG_ROE,YSTZ,SJLTZ,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://data.eastmoney.com/"}

TIMEOUT = 25
BATCH = 30          # 单批代码数，避免 URL 过长


def _num(v):
    try:
        if v is None or v == "" or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _plain_code(code: str) -> Optional[str]:
    """'600036.SH' -> '600036'；非 A 股返回 None"""
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", code or "", re.I)
    return m.group(1) if m else None


def _annualize_roe(roe: Optional[float], report_date: str) -> Optional[float]:
    """把单期 ROE 换算为年化口径。

    一季报 ×4 / 中报 ×2 / 三季报 ×4÷3 / 年报 ×1。
    手工维护的 fundamentals.json 存的是年化口径，两边对齐后才可比。
    """
    if roe is None:
        return None
    m = re.match(r"^\d{4}-(\d{2})-", report_date or "")
    if not m:
        return roe
    month = int(m.group(1))
    factor = {3: 4.0, 6: 2.0, 9: 4.0 / 3.0, 12: 1.0}.get(month)
    if not factor:
        return roe
    return round(roe * factor, 2)


def _fetch_batch(codes: List[str]) -> Dict[str, dict]:
    """批量查询一批 A 股代码，返回 {6位代码: 财报dict}（每只只保留最新一期）"""
    quoted = ",".join('"%s"' % c for c in codes)
    filt = "(SECURITY_CODE in (%s))" % quoted
    url = "%s?reportName=%s&columns=%s&filter=%s&pageSize=500&sortColumns=REPORTDATE&sortTypes=-1" % (
        API, REPORT, COLS, urllib.parse.quote(filt))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        payload = json.loads(r.read().decode("utf-8", "ignore"))

    rows = ((payload.get("result") or {}).get("data")) or []
    out: Dict[str, dict] = {}
    # 已按 REPORTDATE 倒序，setdefault 即"最新一期优先"
    for row in rows:
        code = row.get("SECURITY_CODE")
        if not code or code in out:
            continue
        rev = _num(row.get("TOTAL_OPERATE_INCOME"))
        profit = _num(row.get("PARENT_NETPROFIT"))
        net_margin = round(profit / rev * 100, 2) if (rev and profit and rev > 0) else None
        rd = str(row.get("REPORTDATE") or "")
        out[code] = {
            "roe": _annualize_roe(_num(row.get("WEIGHTAVG_ROE")), rd),
            "net_margin": net_margin,
            "rev_yoy": _num(row.get("YSTZ")),
            "profit_yoy": _num(row.get("SJLTZ")),
            "as_of": rd[:10] or None,
            "source": "eastmoney-auto",
        }
    return out


def fetch_a_share_fundamentals(codes: List[str], verbose: bool = False) -> Dict[str, dict]:
    """抓取 A 股财报。

    :param codes: 形如 ['600036.SH', '002714.SZ'] 的代码列表（非 A 股自动忽略）
    :return: {原始代码(带后缀): 财报dict}
    """
    plain = {}
    for c in codes:
        p = _plain_code(c)
        if p:
            plain.setdefault(p, c)

    if not plain:
        return {}

    result: Dict[str, dict] = {}
    keys = list(plain.keys())
    for i in range(0, len(keys), BATCH):
        chunk = keys[i:i + BATCH]
        try:
            got = _fetch_batch(chunk)
        except Exception as e:  # 网络/解析异常不阻断主流程
            if verbose:
                print(f"    [财报] 批次 {i // BATCH + 1} 失败：{type(e).__name__}: {e}")
            continue
        for p, data in got.items():
            result[plain[p]] = data

    if verbose and result:
        print(f"    [财报] 自动补 {len(result)}/{len(plain)} 只 A 股")
    return result


def merge_fundamentals(manual: Dict[str, dict], auto: Dict[str, dict]) -> Dict[str, dict]:
    """合并手工与自动数据。

    规则：手工值优先（用户核过的数据更可信），
    但手工为 None 的字段用自动值补上；整只缺失则直接采用自动值。
    """
    out = {k: dict(v) for k, v in manual.items()}
    for code, a in auto.items():
        cur = out.get(code)
        if not cur:
            out[code] = dict(a)
            continue
        merged = dict(cur)
        changed = False
        for f in ("roe", "net_margin", "rev_yoy", "profit_yoy"):
            if merged.get(f) is None and a.get(f) is not None:
                merged[f] = a[f]
                changed = True
        if changed:
            merged["as_of"] = a.get("as_of") or merged.get("as_of")
            merged["source"] = f"{merged.get('source', 'manual')}+auto"
            out[code] = merged
    return out

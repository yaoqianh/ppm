/* ==========================================================================
   持仓追踪 · 辅助决策看板 —— 前端渲染与交互
   - 读取 data/dashboard.json
   - 评分维度权重可在「评分配置」中实时调整并立即重算全站分数
   - 图表为自绘 SVG，无任何外部依赖
   ========================================================================== */

'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

let DATA = null;          // dashboard.json
let BASE_CFG = null;      // 项目配置（config/score_config.json 的副本）
let OVR = {};             // 本地覆盖（localStorage）
let EFF = null;           // 生效配置

const LS_KEY = 'pd_score_cfg_v1';
const TN_FULL = { trend: '趋势跟踪', longterm: '长线监控', divergence: '背离反转', exit: '退出通道', fundamental: '基本面' };

/* ------------------------------------------------------------- 工具 */

const isNum = v => typeof v === 'number' && isFinite(v);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmt = (v, d = 2) => (isNum(v) ? v.toFixed(d) : '—');
const sign = v => (isNum(v) && v > 0 ? '+' : '');
const chgCls = v => (!isNum(v) || v === 0 ? 'flat' : v > 0 ? 'up' : 'down');
const pctTxt = (v, d = 2) => (isNum(v) ? sign(v) + v.toFixed(d) + '%' : '—');
const scoreColor = s => (!isNum(s) ? 'var(--faint)' : s >= 70 ? 'var(--up)' : s >= 50 ? 'var(--amber)' : 'var(--down)');
const badgeCls = s => (!isNum(s) ? 'b-na' : s >= 70 ? 'b-strong' : s >= 35 ? 'b-mid' : 'b-weak');

function money(v, unit = true) {
  if (!isNum(v)) return '—';
  const a = Math.abs(v);
  if (a >= 10000) return (v / 10000).toFixed(1) + (unit ? '万' : '');
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}
const moneyFull = v => (isNum(v) ? (v < 0 ? '-¥' : '¥') + Math.abs(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 }) : '—');
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
/** 股票代码含点号（513180.SH），不能直接拼进 #id 选择器，需转义为合法 ID */
const boxId = code => 'dbox-' + String(code).replace(/[^A-Za-z0-9_-]/g, '_');

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(t._tm);
  t._tm = setTimeout(() => t.classList.remove('on'), 2200);
}

/* ------------------------------------------------------------- 配置生效 */

function loadOverride() {
  try { OVR = JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch (e) { OVR = {}; }
}

function applyOverride(cfg, ovr) {
  // 深拷贝后套用本地覆盖，避免污染原始配置
  const out = JSON.parse(JSON.stringify(cfg));
  const merge = (tpl, o) => {
    if (!o || !tpl.dimensions) return;
    for (const d of tpl.dimensions) {
      const od = o[d.key];
      if (!od) continue;
      if (typeof od.enabled === 'boolean') d.enabled = od.enabled;
      if (isNum(od.weight)) d.weight = od.weight;
    }
  };
  if (ovr.templates) {
    for (const k in ovr.templates) if (out.templates[k]) merge(out.templates[k], ovr.templates[k]);
  }
  if (ovr.market_timing) merge(out.market_timing, ovr.market_timing);
  return out;
}

function recompute() { EFF = applyOverride(BASE_CFG, OVR); }

/* ------------------------------------------------------------- 评分（与 Python 引擎同公式） */

function composite(dims, tpl) {
  if (!tpl || !tpl.dimensions || !tpl.dimensions.length) return null;
  const mode = tpl.mode || 'weighted_sum';

  if (mode === 'base_plus_adjust') {
    let total = tpl.base || 50;
    for (const d of tpl.dimensions) {
      if (d.enabled === false) continue;
      const got = dims[d.key];
      if (!got || !isNum(got.score)) continue;
      const dw = d.max || 1;
      total += got.score * ((isNum(d.weight) ? d.weight : dw) / dw);
    }
    return Math.round(clamp(total, 0, 100) * 10) / 10;
  }

  let num = 0, den = 0;
  for (const d of tpl.dimensions) {
    if (d.enabled === false) continue;
    const got = dims[d.key];
    if (!got || !isNum(got.score)) continue;
    const mx = d.max || 1;
    const w = isNum(d.weight) ? d.weight : mx;
    num += (got.score / mx) * w;
    den += w;
  }
  return den ? Math.round(clamp(100 * num / den, 0, 100) * 10) / 10 : null;
}

function tierOf(score, tpl) {
  if (!isNum(score)) return { status: '无评分', advice: '该模板无评分维度或未取到数据' };
  for (const t of (tpl.tiers || [])) if (score >= t.min) return t;
  return { status: '', advice: '' };
}

/** 按生效配置实时计算某持仓的综合评分与状态 */
function liveOf(h) {
  const tpl = EFF.templates[h.template] || {};
  const sc = composite(h.dims[h.template] || {}, tpl);
  const tier = tierOf(sc, tpl);
  return { score: sc, status: tier.status, advice: tier.advice };
}

function allScores(h) {
  const out = {};
  for (const k of ['trend', 'longterm', 'divergence', 'fundamental']) {
    out[k] = composite(h.dims[k] || {}, EFF.templates[k] || {});
  }
  return out;
}

/** 大盘择时总分（按生效权重重算） */
function liveTiming() {
  const dims = {};
  for (const d of DATA.timing.dims) dims[d.key] = { score: d.score };
  const total = composite(dims, EFF.market_timing);
  let pos = '', note = '';
  for (const t of EFF.market_timing.position_tiers) {
    if ((total || 0) >= t.min) { pos = t.position; note = t.note; break; }
  }
  return { total, pos, note };
}

/* ------------------------------------------------------------- 渲染 */

function renderSummary() {
  $('#time').textContent = '更新于 ' + DATA.updated_at + (DATA.as_of ? `　收盘日 ${DATA.as_of}` : '');
  $('#summary').textContent = '📋 ' + DATA.summary;
}

function renderStats() {
  const p = DATA.portfolio, hi = DATA.settings.high_score, lo = DATA.settings.low_score;
  const cards = [
    { k: '总市值', v: moneyFull(p.total_mv), s: `${p.position_count} 只持仓 · 折人民币`, c: '' },
    { k: '持仓成本', v: moneyFull(p.total_cost), s: '含汇率折算', c: '' },
    { k: '浮动盈亏', v: (p.total_pnl_amt < 0 ? '-¥' : '+¥') + Math.abs(p.total_pnl_amt || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 }),
      s: pctTxt(p.total_pnl_pct), c: chgCls(p.total_pnl_amt), accent: true },
    { k: '当日盈亏', v: (p.day_pnl < 0 ? '-¥' : '+¥') + Math.abs(p.day_pnl || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 }),
      s: '较上一交易日', c: chgCls(p.day_pnl) },
    { k: '已实现盈亏', v: (p.realized_pnl < 0 ? '-¥' : '+¥') + Math.abs(p.realized_pnl || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 }),
      s: '来自调仓记录', c: chgCls(p.realized_pnl) },
    { k: '持仓集中度', v: isNum(p.top1_weight) ? p.top1_weight.toFixed(1) + '%' : '—',
      s: `前三合计 ${isNum(p.top3_weight) ? p.top3_weight.toFixed(0) : '—'}%`, c: p.top1_weight > 30 ? 'up' : '' },
    { k: `高分持仓(≥${hi})`, v: `${p.high_score_names.length} 只`, s: p.high_score_names.join('、') || '暂无', c: 'up' },
    { k: `低分持仓(<${lo})`, v: `${p.low_score_names.length} 只`, s: p.low_score_names.join('、') || '暂无', c: 'down' },
  ];
  $('#stats').innerHTML = cards.map(c => `
    <div class="stat ${c.accent ? 'accent' : ''}">
      <div class="k">${esc(c.k)}</div>
      <div class="v ${c.c}">${esc(c.v)}</div>
      <div class="s">${esc(c.s)}</div>
    </div>`).join('');
  $('#ovSub').textContent = p.deep_loss.length
    ? `　深套提醒：${p.deep_loss.map(d => `${d.name} ${d.pnl}%`).join('、')}`
    : '';
}

function renderWeights() {
  const hs = [...DATA.holdings].filter(h => isNum(h.weight)).sort((a, b) => b.weight - a.weight);
  const mx = Math.max(...hs.map(h => h.weight), 1);
  $('#weights').innerHTML = hs.map(h => {
    const lv = liveOf(h);
    return `<div class="bar-row">
      <span class="lb">${esc(h.name)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(h.weight / mx * 100).toFixed(1)}%;background:${scoreColor(lv.score)}"></div></div>
      <span class="bar-v ${chgCls(h.pnl)}">${fmt(h.weight, 1)}%</span>
      <span class="bar-d">市值 ${money(h.mv)} · 盈亏 ${pctTxt(h.pnl, 1)} · 评分 ${isNum(lv.score) ? lv.score : '—'}</span>
    </div>`;
  }).join('');
}

function renderRegime() {
  const t = liveTiming();
  const dims = DATA.timing.dims;
  const cfgDims = EFF.market_timing.dimensions;
  const keep = new Set(cfgDims.filter(d => d.enabled !== false).map(d => d.key));

  $('#regime').innerHTML = `
    <div style="display:flex;align-items:center;gap:22px;flex-wrap:wrap;margin-bottom:14px">
      <div><span style="font-size:34px;font-weight:700;color:${scoreColor(t.total)}">${isNum(t.total) ? t.total : '—'}</span>
        <span style="color:var(--faint);font-size:13px"> / 100</span></div>
      <div><span class="badge ${badgeCls(t.total)}" style="font-size:14px">${esc(t.pos)}</span>
        <div style="font-size:12.5px;color:var(--dim);margin-top:5px">${esc(t.note)}</div></div>
    </div>
    ${dims.map(m => {
      const p = m.max ? (m.score || 0) / m.max * 100 : 0;
      const off = !keep.has(m.key);
      return `<div class="bar-row" style="${off ? 'opacity:.35' : ''}">
        <span class="lb">${esc(m.name)}</span>
        <div class="bar-track" style="max-width:340px"><div class="bar-fill" style="width:${p.toFixed(1)}%;background:${scoreColor(p)}"></div></div>
        <span class="bar-v" style="color:${scoreColor(p)}">${isNum(m.score) ? m.score : '—'}/${m.max}</span>
        <span class="bar-d">${esc(m.detail)}</span>
      </div>`;
    }).join('')}
    <div style="font-size:11.5px;color:var(--faint);margin-top:10px;border-top:1px dashed var(--line);padding-top:8px">
      五维框架: ${cfgDims.map(d => `${d.name}${d.max}${d.enabled === false ? '(已停用)' : ''}`).join(' + ')}
      （参考机构金工择时体系；广度与新高动能基于你的「持仓 + 自选」全池计算，比全市场指标更贴近你的实际敞口）
    </div>`;
}

function renderIndexes() {
  $('#indexes').innerHTML = DATA.indexes.map(i => `
    <div class="card"><div class="idx">
      <div><div class="nm">${esc(i.name)} <span class="role">${esc(i.role || '')}</span></div>
        <div class="px ${chgCls(i.chg)}">${fmt(i.close)}</div>
        <div class="chg20">近20日 ${pctTxt(i.chg20)}　<span class="${chgCls(i.chg)}">今日 ${pctTxt(i.chg)}</span></div></div>
      <span class="st st-${i.state}">${esc(i.state)}</span>
    </div></div>`).join('');
}

function renderPositions() {
  const cols = [
    ['rk', '#', false], ['name', '名称 / 代码', true], ['tpl', '策略模板', true],
    ['shares', '持仓数量', true], ['cost', '成本价', true], ['close', '现价', true],
    ['chg', '今日涨跌', true], ['mv', '市值(¥)', true], ['pnl', '浮动盈亏', true],
    ['weight', '权重', true], ['score', '状态评分', true], ['delta', '评分变动', true],
    ['stop', '建议止损', true], ['tp', '建议止盈', true],
    ['status', '状态', false],
  ];
  $('#posHead').innerHTML = cols.map(([k, t, s]) =>
    `<th class="${s ? 'sortable' : ''}" data-k="${k}">${t}</th>`).join('');

  const rows = orderHoldings();
  $('#posBody').innerHTML = rows.map((h, i) => {
    const lv = liveOf(h);
    const d = h.score_change;
    const arrow = !isNum(d) ? '' : (d > 0 ? '↑' : d < 0 ? '↓' : '→');
    return `<tr data-code="${esc(h.code)}" style="cursor:pointer">
      <td><span class="rk ${i < 3 ? 'rk1' : ''}">${i + 1}</span></td>
      <td><b>${esc(h.name)}</b> <code class="c">${esc(h.code)}</code></td>
      <td><span class="tag amber">${esc(TN_FULL[h.template] || h.template)}</span></td>
      <td class="num">${h.shares.toLocaleString('zh-CN')}</td>
      <td class="num">${fmt(h.cost, h.cost < 10 ? 3 : 2)} <span class="faint" style="font-size:10.5px">${esc(h.currency)}</span></td>
      <td class="num">${fmt(h.close, h.close < 10 ? 3 : 2)}</td>
      <td class="num ${chgCls(h.chg)}">${pctTxt(h.chg)}</td>
      <td class="num">${money(h.mv, false)}</td>
      <td class="num ${chgCls(h.pnl_amt)}" title="${moneyFull(h.pnl_amt)}">${moneyFull(h.pnl_amt)}<br><span style="font-size:11px">${pctTxt(h.pnl, 1)}</span></td>
      <td class="num">${fmt(h.weight, 1)}%</td>
      <td class="num" style="color:${scoreColor(lv.score)};font-weight:680;font-size:15px">${isNum(lv.score) ? lv.score : '—'}</td>
      <td class="num ${chgCls(d)}" title="${esc(h.change_reason)}">${isNum(d) ? arrow + ' ' + fmt(Math.abs(d), 1) : '—'}</td>
      <td class="num">${stopTP_cell(h)}</td>
      <td class="num">${takeProfit_cell(h)}</td>
      <td><span class="badge ${badgeCls(lv.score)}">${esc(lv.status)}</span></td>
    </tr>
    <tr class="detail" data-for="${esc(h.code)}" style="display:none"><td></td><td colspan="14">
      <div class="dimbox" id="${boxId(h.code)}"></div>
    </td></tr>`;
  }).join('');

  $$('#posHead th.sortable').forEach(th => th.onclick = () => sortTable('pos', th.dataset.k, th));
  $$('#posBody tr[data-code]').forEach(tr => tr.onclick = () => {
    const code = tr.dataset.code;
    const det = $(`#posBody tr.detail[data-for="${code}"]`);
    if (!det) return;
    const box = document.getElementById(boxId(code));
    if (det.style.display === 'none') box.innerHTML = dimDetail(code);
    det.style.display = det.style.display === 'none' ? '' : 'none';
  });
}

// ---- 止损/止盈 推荐价列 ----
function stopTP_cell(h) {
  if (!isNum(h.stop_price)) return '<span class="faint">—</span>';
  const sdAtr = h.stop_distance_atr;
  const sdPct = h.stop_distance_pct;
  const atrTxt = isNum(sdAtr) ? `距 ${sdAtr.toFixed(1)}×ATR` : '';
  const pctTxt2 = isNum(sdPct) ? `(-${sdPct.toFixed(1)}%)` : '';
  const breached = h.stop_breached;
  const ccy = esc(h.currency || '');
  const cls = breached ? 'color:var(--up);font-weight:680'
    : (isNum(sdAtr) && sdAtr < 0.5 ? 'color:#f0a020;font-weight:650'
    : (isNum(sdAtr) && sdAtr < 1.0 ? 'color:var(--warn,#d49a30)' : ''));
  return `<div title="${esc(h.stop_method || 'ATR 波动率自适应')}" style="${cls}">
    ${fmt(h.stop_price, h.stop_price < 10 ? 3 : 2)} <span class="faint" style="font-size:10.5px">${ccy}</span><br>
    <span style="font-size:10.5px">${breached ? '⚠ 已触发' : atrTxt}${pctTxt2 ? ' ' + pctTxt2 : ''}</span>
  </div>`;
}

function takeProfit_cell(h) {
  if (!isNum(h.take_profit_1)) return '<span class="faint">—</span>';
  const tp1 = h.take_profit_1;
  const tp2 = h.take_profit_2;
  const tp1Atr = h.tp1_distance_atr;
  const reached = h.tp1_reached;
  const cls = reached ? 'color:var(--down);font-weight:680'
    : (isNum(tp1Atr) && tp1Atr < 1.0 ? 'color:#5fc78a;font-weight:650' : '');
  const tp2line = isNum(tp2)
    ? `<br><span style="font-size:10.5px" class="faint">T2 ${fmt(tp2, tp2 < 10 ? 3 : 2)} · 距 ${h.tp2_distance_atr ? h.tp2_distance_atr.toFixed(1) + '×ATR' : '—'}</span>`
    : '';
  return `<div title="${esc(h.take_profit_1_method || '')}" style="${cls}">
    ${fmt(tp1, tp1 < 10 ? 3 : 2)} <span class="faint" style="font-size:10.5px">-1/3</span>${tp2line}
  </div>`;
}

function dimDetail(code) {
  const h = DATA.holdings.find(x => x.code === code);
  if (!h) return '';
  const tpl = EFF.templates[h.template] || {};
  const dims = h.dims[h.template] || {};
  const rows = (tpl.dimensions || []).map(d => {
    const g = dims[d.key] || {};
    const off = d.enabled === false;
    return `<div class="row" style="${off ? 'opacity:.4' : ''}">
      <span class="n">${esc(d.name)}</span>
      <span style="width:58px;color:${scoreColor(d.max ? (g.score || 0) / d.max * 100 : 0)};font-weight:650">
        ${isNum(g.score) ? g.score : '—'}<span class="faint">/${d.max ?? '—'}</span></span>
      <span class="faint" style="width:52px">权重 ${d.weight ?? d.max}${off ? ' · 停用' : ''}</span>
      <span class="d">${esc(g.detail || '')}</span></div>`;
  }).join('');
  const others = allScores(h);
  // 出场纪律详情（仅当有 stop_price 时显示）
  let stopBlock = '';
  if (isNum(h.stop_price) || isNum(h.take_profit_1)) {
    const seg = [];
    if (isNum(h.stop_price)) {
      const cls = h.stop_breached ? 'var(--up)'
        : (isNum(h.stop_distance_atr) && h.stop_distance_atr < 0.5 ? '#f0a020' : '');
      seg.push(`<span><b style="color:${cls}">止损 ${fmt(h.stop_price, h.stop_price<10?3:2)} ${esc(h.currency||'')}</b>
        <span class="faint" style="font-size:10.5px">(${esc(h.stop_method || 'ATR 多支撑')}${isNum(h.stop_distance_atr) ? ' · 距 '+h.stop_distance_atr.toFixed(1)+'×ATR' : ''})</span></span>`);
    }
    if (isNum(h.take_profit_1)) {
      const cls = h.tp1_reached ? 'var(--down)' : '';
      seg.push(`<span><b style="color:${cls}">止盈 T1 ${fmt(h.take_profit_1, h.take_profit_1<10?3:2)}</b>
        <span class="faint" style="font-size:10.5px">${esc(h.take_profit_1_method || '')}${isNum(h.tp1_distance_atr) ? ' · 距 '+h.tp1_distance_atr.toFixed(1)+'×ATR' : ''}${h.tp1_reached ? ' · ⚡已触及' : ''}</span></span>`);
    }
    if (isNum(h.take_profit_2)) {
      seg.push(`<span><b>止盈 T2 ${fmt(h.take_profit_2, h.take_profit_2<10?3:2)}</b>
        <span class="faint" style="font-size:10.5px">${esc(h.take_profit_2_method || '')}${isNum(h.tp2_distance_atr) ? ' · 距 '+h.tp2_distance_atr.toFixed(1)+'×ATR' : ''}</span></span>`);
    }
    if (h.exit_state && h.exit_state.text) {
      const colorByLevel = {ok:'var(--up)', warn:'#f0a020', watch:'#f0a020', danger:'var(--up)', warm:'var(--down)'}[h.exit_state.level] || 'var(--dim)';
      seg.push(`<span style="color:${colorByLevel}">状态 · ${esc(h.exit_state.text)}</span>`);
    }
    if (isNum(h.holding_days) && isNum(h.time_stop_days)) {
      seg.push(`<span class="faint" style="font-size:10.5px">持仓 ${h.holding_days} 日 / 时间止损 ${h.time_stop_days} 日</span>`);
    }
    stopBlock = `<div class="row" style="background:rgba(210,163,95,.07);border-radius:6px;padding:6px 10px;margin-top:6px">
      <span class="n" style="color:var(--amber)">止盈止损</span>
      <div style="display:flex;gap:18px;flex-wrap:wrap">${seg.join('')}</div>
    </div>`;
  }
  return `<div style="font-size:12px;color:var(--dim);margin-bottom:6px">
      <b style="color:var(--amber)">${esc(TN_FULL[h.template])}</b> 维度明细　
      评分变动说明：${esc(h.change_reason)}
      ${isNum(h.hard_stop) ? `　|　硬止损 ${fmt(h.hard_stop, 3)} ${esc(h.currency)}${h.hard_stop_hit ? ' <b style="color:var(--up)">已触及</b>' : ''}` : ''}
      ${isNum(h.chandelier) ? `　|　吊灯止损 ${fmt(h.chandelier, 3)}` : ''}
    </div>${rows}${stopBlock}
    <div style="font-size:11.5px;color:var(--faint);margin-top:8px;border-top:1px dashed var(--line);padding-top:6px">
      其他模板评分：趋势 ${fmt(others.trend, 0)} · 长线 ${fmt(others.longterm, 0)} · 背离 ${fmt(others.divergence, 0)} · 基本面 ${fmt(others.fundamental, 0)}
      ${isNum(h.rsi) ? `　|　RSI ${fmt(h.rsi, 1)}` : ''}${isNum(h.ma20) ? `　|　MA20 ${fmt(h.ma20, 3)}` : ''}
    </div>`;
}

function renderDiag() {
  const cols = ['#', '名称', '现价', '涨跌', '盈亏', '仓位', '趋势', '长线', '背离', '基本面', '状态', '关键信号'];
  $('#diagHead').innerHTML = cols.map(t => `<th>${t}</th>`).join('');

  const rows = [...DATA.holdings].sort((a, b) => (b.weight || -1) - (a.weight || -1));
  $('#diagBody').innerHTML = rows.map((h, i) => {
    const s = allScores(h);
    const lv = liveOf(h);
    const cell = (key, val, tp) => {
      if (!isNum(val)) return '<td class="flat">—</td>';
      const cur = h.template === tp;
      return `<td style="color:${cur ? 'var(--amber)' : scoreColor(val)};font-weight:${cur ? 700 : 650}${cur ? ';border-bottom:2px solid var(--amber)' : ''}"
        title="${esc(((h.dims[tp] || {}) && dimTips(h, tp)) || '')}">${val}${cur ? '◀' : ''}</td>`;
    };
    if (h.error) return `<tr><td><span class="rk">${i + 1}</span></td>
      <td><b>${esc(h.name)}</b> <code class="c">${esc(h.code)}</code></td>
      <td colspan="10"><span class="err">取数失败: ${esc(h.error)}</span></td></tr>`;

    return `<tr><td><span class="rk">${i + 1}</span></td>
      <td><b>${esc(h.name)}</b> <code class="c">${esc(h.code)}</code><br>
        <span style="font-size:11px;color:var(--amber)">${esc(TN_FULL[h.template])}</span></td>
      <td class="num">${fmt(h.close, h.close < 10 ? 3 : 2)} <span class="faint" style="font-size:10.5px">${esc(h.currency)}</span></td>
      <td class="num ${chgCls(h.chg)}">${pctTxt(h.chg)}</td>
      <td class="num ${chgCls(h.pnl)}" style="font-weight:650">${pctTxt(h.pnl, 1)}</td>
      <td class="num">${fmt(h.weight, 1)}%</td>
      ${cell('trend', s.trend, 'trend')}
      ${cell('longterm', s.longterm, 'longterm')}
      ${cell('divergence', s.divergence, 'divergence')}
      ${cell('fundamental', s.fundamental, 'fundamental')}
      <td><span class="badge ${badgeCls(lv.score)}">${esc(lv.status)}</span></td>
      <td style="color:var(--dim);font-size:12.5px;line-height:1.5;min-width:260px">${esc((h.signals || []).join('；') || '—')}</td></tr>
    <tr class="sub-row"><td colspan="12" style="padding-top:0;border-bottom:1px solid var(--line)">
      <div class="advice">${esc(lv.advice || h.advice)}</div></td></tr>`;
  }).join('');
}

function dimTips(h, tp) {
  const dims = h.dims[tp] || {};
  const tpl = (EFF.templates[tp] || {}).dimensions || [];
  return tpl.map(d => `${d.name} ${((dims[d.key] || {}).score ?? '—')}/${d.max}：${(dims[d.key] || {}).detail || ''}`).join('\n');
}

function renderTrades() {
  const cols = ['日期', '方向', '名称 / 代码', '数量', '成交价', '成交额(¥)', '已实现盈亏', '备注'];
  $('#tradeHead').innerHTML = cols.map(t => `<th>${t}</th>`).join('');
  const ts = DATA.trades || [];
  if (!ts.length) {
    $('#tradeBody').innerHTML = '<tr><td colspan="8"><div class="empty">暂无调仓记录，可在 data/trades.json 中维护</div></td></tr>';
    return;
  }
  $('#tradeBody').innerHTML = ts.map(t => {
    const buy = t.side === 'buy';
    return `<tr>
      <td style="white-space:nowrap">${esc(t.date)}</td>
      <td><span class="badge ${buy ? 'b-strong' : 'b-weak'}">${buy ? '买入' : '卖出'}</span></td>
      <td><b>${esc(t.name)}</b> <code class="c">${esc(t.code)}</code></td>
      <td class="num">${t.shares.toLocaleString('zh-CN')}</td>
      <td class="num">${fmt(t.price, t.price < 10 ? 3 : 2)} <span class="faint" style="font-size:10.5px">${esc(t.currency || '')}</span></td>
      <td class="num">${money(t.amount, false)}</td>
      <td class="num ${chgCls(t.realized)}">${isNum(t.realized) ? moneyFull(t.realized) : (t.type === 'baseline' ? '<span class="faint">基线</span>' : '—')}</td>
      <td class="faint" style="font-size:12px">${esc(t.note || '')}</td>
    </tr>`;
  }).join('');
}

function renderTemplates() {
  const box = $('#tplBox');
  box.innerHTML = (DATA.templates || []).map(t => {
    const cfgT = EFF.templates[t.key] || {};
    const dimTxt = (cfgT.dimensions || []).map(d =>
      `${d.name}${d.max ?? ''}${d.enabled === false ? '(停用)' : ''}<span class="faint">权重${d.weight ?? d.max}</span>`).join(' + ');
    return `<div style="margin-bottom:16px">
      <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
        <b style="color:var(--amber);font-size:14.5px">${esc(t.name)}</b>
        <span class="faint" style="font-size:12px">适用: ${esc(t.scene)}</span>
      </div>
      <div style="font-size:12.5px;color:var(--dim);margin:5px 0">买入: ${esc(dimTxt || '不做评分，只做触发器')}</div>
      <div style="font-size:12.5px;color:var(--dim);margin:3px 0">卖出: ${esc(t.sell_rules || '')}</div>
      <div style="font-size:12.5px">持仓: ${t.names.length
        ? t.names.map(n => `<span class="tag">${esc(n)}</span>`).join('')
        : '<span class="faint">暂无</span>'}</div>
    </div>`;
  }).join('')
    + `<div style="font-size:11.5px;color:var(--faint);border-top:1px dashed var(--line);padding-top:8px">
        模板分配与规则均可调整——直接告诉助手「把XX改到XX模板」或「把趋势模板的止损线改成X%」即可。</div>`
    + (DATA.score_rules || []).map(r => `
      <div style="border-top:1px dashed var(--line);padding-top:10px;margin-top:10px">
        <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
          <b style="color:var(--blue);font-size:14.5px">${esc(r.name)}</b>
          <span class="faint" style="font-size:12px">${esc(r.basis)}</span>
        </div>
        <div style="font-size:12.5px;color:var(--dim);margin-top:5px">${esc(r.rules)}</div>
      </div>`).join('');
}

function renderWatch() {
  const GROUPS = ['价值', '周期', 'AI产业链', '其他'];
  const byGroup = {};
  DATA.watchlist.forEach(w => (byGroup[w.group || '其他'] = byGroup[w.group || '其他'] || []).push(w));
  $('#watchSub').textContent = `　共 ${DATA.watchlist.length} 只`;

  const cols = ['#', '名称', '现价', '涨跌', '趋势', '长线', '背离', '基本面', '状态', '关键信号'];
  $('#watchHead').innerHTML = cols.map(t => `<th>${t}</th>`).join('');

  const cell = (v, tp, w) => {
    if (!isNum(v)) return '<td class="flat">—</td>';
    return `<td style="color:${scoreColor(v)};font-weight:650" title="${esc(dimTipsW(w, tp))}">${v}</td>`;
  };
  const scoreOf = (w, tp) => composite(w.dims[tp] || {}, EFF.templates[tp] || {});

  $('#watchBody').innerHTML = GROUPS.filter(g => byGroup[g]).map(g => {
    const rows = byGroup[g].slice().sort((a, b) => (scoreOf(b, 'trend') || -1) - (scoreOf(a, 'trend') || -1));
    return `<tr class="group-row"><td colspan="10" style="background:var(--card2);color:var(--amber);font-size:12.5px;font-weight:600;padding:7px 10px">▍${esc(g)}（${rows.length}只）</td></tr>`
      + rows.map((w, i) => {
        const st = tierOf(scoreOf(w, 'trend'), EFF.templates.trend || {});
        return `<tr><td><span class="rk ${i < 3 ? 'rk1' : ''}">${i + 1}</span></td>
          <td><b>${esc(w.name)}</b> <code class="c">${esc(w.code)}</code></td>
          <td class="num">${fmt(w.close)}</td>
          <td class="num ${chgCls(w.chg)}">${pctTxt(w.chg)}</td>
          ${cell(scoreOf(w, 'trend'), 'trend', w)}
          ${cell(scoreOf(w, 'longterm'), 'longterm', w)}
          ${cell(scoreOf(w, 'divergence'), 'divergence', w)}
          ${cell(scoreOf(w, 'fundamental'), 'fundamental', w)}
          <td><span class="badge ${badgeCls(scoreOf(w, 'trend'))}">${esc(st.status || '')}</span></td>
          <td style="color:var(--dim);font-size:12.5px;line-height:1.5;min-width:240px">${esc((w.signals || []).join('；') || '—')}</td></tr>`;
      }).join('');
  }).join('');
}

function dimTipsW(w, tp) {
  const dims = w.dims[tp] || {};
  const tpl = (EFF.templates[tp] || {}).dimensions || [];
  return tpl.map(d => `${d.name} ${((dims[d.key] || {}).score ?? '—')}/${d.max}：${(dims[d.key] || {}).detail || ''}`).join('\n');
}

/* ------------------------------------------------------------- 排序 */

const sortState = {};

/** 按当前排序状态返回持仓顺序（默认按权重降序） */
function orderHoldings() {
  const st = sortState.pos;
  if (!st) return [...DATA.holdings].sort((a, b) => (b.weight ?? -1) - (a.weight ?? -1));
  const val = {
    rk: (h, i) => i, name: h => h.name, tpl: h => h.template, shares: h => h.shares,
    cost: h => h.cost, close: h => h.close, chg: h => h.chg ?? -999, mv: h => h.mv ?? -999,
    pnl: h => h.pnl ?? -999, weight: h => h.weight ?? -999,
    score: h => liveOf(h).score ?? -999, delta: h => h.score_change ?? -999,
    stop: h => h.stop_distance_atr ?? -999,
    tp: h => h.tp1_distance_atr ?? -999,
    status: h => liveOf(h).status,
  }[st.key] || (h => h.weight ?? -999);
  return [...DATA.holdings].map((h, i) => [h, i]).sort((a, b) => {
    const x = val(a[0], a[1]), y = val(b[0], b[1]);
    if (typeof x === 'string' || typeof y === 'string') return String(x).localeCompare(String(y)) * st.dir;
    return ((x ?? -999) - (y ?? -999)) * st.dir;
  }).map(r => r[0]);
}

function sortTable(which, key, th) {
  const st = sortState[which] || {};
  const dir = st.key === key ? -st.dir : -1;
  sortState[which] = { key, dir };
  $$(`#${which}Head th`).forEach(x => x.classList.remove('sorted', 'asc'));
  th.classList.add('sorted');
  if (dir === 1) th.classList.add('asc');
  renderPositions();
}

/* ------------------------------------------------------------- 配置抽屉 */

function renderCfg() {
  const blocks = [];
  for (const key in EFF.templates) {
    const t = EFF.templates[key];
    if (!t.dimensions || !t.dimensions.length) continue;
    const mode = t.mode === 'base_plus_adjust'
      ? `基准 ${t.base} 分 + 六项调整（越正越好）`
      : `满分 ${t.dimensions.reduce((a, d) => a + (d.max || 0), 0)}`;
    blocks.push(`<div class="cfg-tmpl" data-tpl="${esc(key)}">
      <div class="t">${esc(t.name)} <span>${esc(mode)}</span></div>
      ${t.dimensions.map(d => {
        const w = isNum(d.weight) ? d.weight : (d.max || 0);
        const on = d.enabled !== false;
        return `<div class="cfg-dim ${on ? '' : 'off'}" data-key="${esc(d.key)}">
          <input type="checkbox" ${on ? 'checked' : ''} data-act="on"/>
          <div class="nm">${esc(d.name)}<small>${esc(d.desc || '')}</small></div>
          <input type="range" min="0" max="${Math.max(20, Math.ceil((d.max || 10) * 2))}" step="1" value="${w}" data-act="w"/>
          <div class="wv">${w}</div>
        </div>`;
      }).join('')}
    </div>`);
  }
  const mt = EFF.market_timing;
  blocks.push(`<div class="cfg-tmpl" data-tpl="__market__">
    <div class="t">${esc(mt.name)} <span>满分 100</span></div>
    ${mt.dimensions.map(d => {
      const w = isNum(d.weight) ? d.weight : (d.max || 0);
      const on = d.enabled !== false;
      return `<div class="cfg-dim ${on ? '' : 'off'}" data-key="${esc(d.key)}">
        <input type="checkbox" ${on ? 'checked' : ''} data-act="on"/>
        <div class="nm">${esc(d.name)}<small>${esc(d.desc || '')}</small></div>
        <input type="range" min="0" max="${Math.max(20, Math.ceil((d.max || 10) * 2))}" step="1" value="${w}" data-act="w"/>
        <div class="wv">${w}</div>
      </div>`;
    }).join('')}
  </div>`);

  blocks.push(`<div class="hint">
    综合分 = 100 × Σ(维度得分 / 维度满分 × 权重) ÷ Σ(权重)；停用的维度不进入分母。<br>
    改动<b>立即在本页生效</b>并保存到浏览器本地。若希望「跑 run_update.py」时也按新权重计算，
    请点「导出 JSON」并把内容覆盖到 <code class="c">config/score_config.json</code>。
  </div>`);

  $('#cfgBody').innerHTML = blocks.join('');

  $$('#cfgBody .cfg-dim').forEach(row => {
    const tplKey = row.parentElement.dataset.tpl;
    const dimKey = row.dataset.key;
    const cb = row.querySelector('[data-act=on]');
    const rg = row.querySelector('[data-act=w]');
    const wv = row.querySelector('.wv');
    cb.oninput = () => {
      setOv(tplKey, dimKey, { enabled: cb.checked });
      row.classList.toggle('off', !cb.checked);
      afterCfgChange();
    };
    rg.oninput = () => {
      wv.textContent = rg.value;
      setOv(tplKey, dimKey, { weight: Number(rg.value) });
      afterCfgChange();
    };
  });
}

function setOv(tplKey, dimKey, patch) {
  if (tplKey === '__market__') {
    OVR.market_timing = OVR.market_timing || {};
    OVR.market_timing[dimKey] = Object.assign({}, OVR.market_timing[dimKey], patch);
  } else {
    OVR.templates = OVR.templates || {};
    OVR.templates[tplKey] = OVR.templates[tplKey] || {};
    OVR.templates[tplKey][dimKey] = Object.assign({}, OVR.templates[tplKey][dimKey], patch);
  }
  localStorage.setItem(LS_KEY, JSON.stringify(OVR));
}

function afterCfgChange() {
  recompute();
  renderAll();
}

function openCfg() {
  renderCfg();
  $('#drawer').classList.add('on');
  $('#mask').classList.add('on');
}
function closeCfg() {
  $('#drawer').classList.remove('on');
  $('#mask').classList.remove('on');
}

/* ------------------------------------------------------------- 主流程 */

/**
 * 把表头列名写进每个 td 的 data-l / data-k，
 * 窄屏时 CSS 用 ::before 显示列名，让宽表格变成卡片，手机无需左右横滑。
 */
function labelTables() {
  ['posTable', 'diagTable', 'tradeTable', 'watchTable'].forEach(id => {
    const t = document.getElementById(id);
    if (!t) return;
    const ths = Array.from(t.querySelectorAll('thead th'));
    Array.from(t.querySelectorAll('tbody tr')).forEach(tr => {
      if (tr.classList.contains('sub-row') || tr.classList.contains('detail')) return;
      Array.from(tr.children).forEach((td, i) => {
        const th = ths[i];
        if (!th) return;
        if (th.textContent.trim()) td.setAttribute('data-l', th.textContent.trim());
        if (th.dataset.k) td.setAttribute('data-k', th.dataset.k);
      });
    });
  });
}

function renderAll() {
  renderSummary();
  renderStats();
  renderWeights();
  renderRegime();
  renderIndexes();
  renderPositions();
  renderDiag();
  renderTrades();
  renderTemplates();
  renderWatch();
  labelTables();
}

function boot(data) {
  DATA = data;
  BASE_CFG = data.score_config;
  loadOverride();
  recompute();
  renderAll();

  $('#btnCfg').onclick = openCfg;
  $('#btnClose').onclick = closeCfg;
  $('#mask').onclick = closeCfg;
  $('#btnReload').onclick = () => location.reload();

  // 高级数据：收益统计 + 调仓记录 + 策略模板（默认折叠，仅手动开启）
  const ADV_KEY = 'pd_show_advanced';
  if (localStorage.getItem(ADV_KEY) === '1') document.body.classList.add('show-advanced');
  const btnAdv = $('#btnAdvanced');
  if (btnAdv) btnAdv.onclick = () => {
    document.body.classList.toggle('show-advanced');
    const on = document.body.classList.contains('show-advanced');
    localStorage.setItem(ADV_KEY, on ? '1' : '0');
    toast(on ? '已展开：收益统计 / 调仓记录 / 策略模板' : '已收起');
  };

  $('#btnReset').onclick = () => {
    OVR = {};
    localStorage.removeItem(LS_KEY);
    recompute();
    renderCfg();
    renderAll();
    toast('已恢复为项目默认权重');
  };

  $('#btnExport').onclick = () => {
    const out = JSON.stringify(EFF, null, 2);
    const blob = new Blob([out], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'score_config.json';
    a.click();
    URL.revokeObjectURL(a.href);
    toast('已导出，覆盖 config/score_config.json 即可永久生效');
  };

  // 导航高亮（老环境 / jsdom 里没有 IntersectionObserver，降级为不滚动高亮即可）
  const secs = $$('section[id]');
  const links = $$('#tabs a');
  if (typeof IntersectionObserver === 'function') {
    const io = new IntersectionObserver(es => {
      es.forEach(e => {
        if (!e.isIntersecting) return;
        links.forEach(l => l.classList.toggle('on', l.getAttribute('href') === '#' + e.target.id));
      });
    }, { rootMargin: '-70px 0px -70% 0px' });
    secs.forEach(s => io.observe(s));
  }
}

function start() {
  fetch('data/dashboard.json?_=' + Date.now())
    .then(r => {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(boot)
    .catch(e => {
      $('#summary').innerHTML =
        `<b style="color:var(--up)">数据加载失败：${esc(e.message)}</b><br>
         请通过 HTTP 打开本页（例如在项目目录执行 <code class="c">python run_update.py --serve</code>，
         然后访问 http://localhost:8000）。直接双击 index.html 会因浏览器的本地文件安全策略而无法读取数据。`;
      $('#time').textContent = '未加载';
    });
}

start();

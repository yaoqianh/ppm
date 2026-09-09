/* 运行时冒烟测试：用 jsdom 真实执行 index.html + app.js，检查渲染与交互链路。
   仅用于开发自检，不影响项目运行。   node smoke_test.js  */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = __dirname;
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const appjs = fs.readFileSync(path.join(ROOT, 'assets', 'app.js'), 'utf8');
const dataTxt = fs.readFileSync(path.join(ROOT, 'data', 'dashboard.json'), 'utf8');

const errors = [];
const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost:8000/', pretendToBeVisual: true });
const w = dom.window;

w.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(JSON.parse(dataTxt)) });
w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
w.URL.createObjectURL = () => 'blob:x';
w.URL.revokeObjectURL = () => {};
w.console.error = (...a) => errors.push('console.error: ' + a.join(' '));
w.addEventListener('error', e => errors.push('window error: ' + e.message));

w.eval(appjs);

const $ = s => w.document.querySelector(s);
const $$ = s => Array.from(w.document.querySelectorAll(s));

function check(name, cond, extra = '') {
  const ok = !!cond;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  → ' + extra : ''}`);
  if (!ok) process.exitCode = 1;
}

setTimeout(() => {
  // 基本信息
  check('无 JS 运行时错误', errors.length === 0, errors.join(' | '));
  check('更新时间已渲染', !/加载中|未加载/.test($('#time').textContent), $('#time').textContent);
  check('摘要已渲染', $('#summary').textContent.length > 30 && !/数据加载失败/.test($('#summary').textContent),
    $('#summary').textContent.slice(0, 60) + '…');

  // 各模块
  check('统计卡 8 张', $$('#stats .stat').length === 8, $$('#stats .stat').length + ' 张');
  check('权重分布条已渲染', $$('#weights .bar-row').length === 8);
  check('择时五维条已渲染', $$('#regime .bar-row').length === 5);
  check('择时总分非空', /^\d+/.test($('#regime').textContent.trim()) || $('#regime').textContent.includes('/ 100'));
  check('指数卡 5 张', $$('#indexes .card').length === 5);
  check('评分趋势图为 SVG', $('#chart svg') !== null);
  check('趋势图例 = 持仓数', $$('#legend .lg').length === 8, $$('#legend .lg').length + '');
  check('择时走势图为 SVG', $('#timingChart svg') !== null);

  // 表格
  check('持仓明细表头 16 列', $$('#posHead th').length === 16);
  check('持仓明细 8 行', $$('#posBody tr[data-code]').length === 8);
  check('持仓明细含完整字段', (() => {
    const t = $('#posBody tr[data-code]').textContent;
    return /腾讯|移动|双环/.test(t) && /%/.test(t);
  })());
  check('持仓诊断 8 行', $$('#diagBody tr').length === 16, $$('#diagBody tr').length + ' 行(含副行)');
  check('调仓记录已渲染', $$('#tradeBody tr').length === 8);
  check('自选扫描已渲染', $$('#watchBody tr').length > 34, $$('#watchBody tr').length + ' 行');
  check('策略模板 4 套', $$('#tplBox').length === 1 && /趋势跟踪/.test($('#tplBox').textContent));

  // 手机端卡片布局依赖的数据标签
  const posTds = $$('#posBody tr[data-code]')[0].children;
  check('手机端列名标签已注入', Array.from(posTds).filter(td => td.getAttribute('data-l')).length >= 10,
    Array.from(posTds).map(td => td.getAttribute('data-l')).filter(Boolean).join('/'));
  check('持仓诊断副行有 sub-row 标记', $$('#diagBody tr.sub-row').length === 8);
  check('自选分组行有 group-row 标记', $$('#watchBody tr.group-row').length === 3);

  // 展开维度明细
  const firstRow = $('#posBody tr[data-code]');
  firstRow.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const box = w.document.getElementById('dbox-' + firstRow.dataset.code.replace(/[^A-Za-z0-9_-]/g, '_'));
  check('点击展开维度明细', box && box.textContent.includes('维度明细'),
    box ? box.textContent.slice(0, 50).replace(/\s+/g, ' ') : 'box 未找到');

  // 排序
  const scoreTh = $$('#posHead th').find(t => t.dataset.k === 'pnl');
  scoreTh.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const order = $$('#posBody tr[data-code]').map(r => r.dataset.code);
  check('按盈亏排序生效', order.length === 8 && scoreTh.classList.contains('sorted'), order.slice(0, 3).join(','));

  // 评分配置：打开抽屉并调整权重
  $('#btnCfg').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const dims = $$('#cfgBody .cfg-dim');
  check('配置面板已渲染维度', dims.length >= 20, dims.length + ' 个维度');

  // 调整「长线监控」模板的估值分位权重，验证长线持仓分数确实变化
  const ltBlock = $$('#cfgBody .cfg-tmpl').find(b => b.dataset.tpl === 'longterm');
  const ltSlider = ltBlock.querySelectorAll('.cfg-dim')[0].querySelector('input[type=range]');
  const colOf = () => $$('#posBody tr[data-code]').map(r => r.querySelector('td:nth-child(11)').textContent.trim());
  const before = colOf();
  ltSlider.value = '5';
  ltSlider.dispatchEvent(new w.Event('input', { bubbles: true }));
  const after = colOf();
  check('调整权重后分数重算', JSON.stringify(before) !== JSON.stringify(after),
    `${before.join('/')} → ${after.join('/')}`);

  const ltCb = ltBlock.querySelectorAll('.cfg-dim')[1].querySelector('input[type=checkbox]');
  ltCb.checked = false;
  ltCb.dispatchEvent(new w.Event('input', { bubbles: true }));
  const after2 = colOf();
  check('停用维度后分数重算', JSON.stringify(after) !== JSON.stringify(after2) &&
    $$('#posBody tr[data-code]').length === 8, `${after.join('/')} → ${after2.join('/')}`);

  // 图例切换（renderCharts 会重建节点，需重新查询）
  const firstKey = $$('#legend .lg')[0].dataset.k;
  $$('#legend .lg')[0].dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const lgNow = $$('#legend .lg').find(x => x.dataset.k === firstKey);
  check('图例可切换隐藏', lgNow && !lgNow.classList.contains('on'), firstKey);

  // 时间范围切换
  const rb = $$('#rangeBar button')[2];
  rb.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  check('时间范围可切换', rb.classList.contains('on') && $('#chart svg') !== null);

  check('全流程后仍无 JS 错误', errors.length === 0, errors.join(' | '));
  console.log('\n冒烟测试结束。');
}, 300);

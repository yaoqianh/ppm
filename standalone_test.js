const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const file = process.argv[2] || path.join(__dirname, '..', '持仓看板-离线版.html');
const html = fs.readFileSync(file, 'utf8');
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: 'https://example.invalid/' });
const w = dom.window;

const errs = [];
w.addEventListener('error', e => errs.push(String(e.error || e.message)));

setTimeout(() => {
  const q = s => w.document.querySelector(s);
  const qa = s => Array.from(w.document.querySelectorAll(s));
  const out = [];
  out.push(['JS错误', errs.length ? errs.join(' | ') : '无']);
  out.push(['更新时间', (q('#time') || {}).textContent]);
  out.push(['摘要', ((q('#summary') || {}).textContent || '').slice(0, 60)]);
  out.push(['持仓行数', qa('#posBody tr[data-code]').length]);
  out.push(['统计卡', qa('#stats .stat').length]);
  out.push(['自选行数', qa('#watchBody tr').length]);
  out.push(['数据加载失败提示?', /数据加载失败/.test((q('#summary') || {}).textContent || '') ? '是(异常)' : '否']);
  out.forEach(([k, v]) => console.log(k + ': ' + v));
  const bad = errs.length || qa('#posBody tr[data-code]').length === 0;
  console.log(bad ? 'RESULT: FAIL' : 'RESULT: PASS');
  process.exit(0);
}, 800);

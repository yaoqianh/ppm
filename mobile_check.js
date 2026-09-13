/* 移动端排版检查：用真实 Chromium 在手机视口下渲染，验证
   「表格仍是表格 + 可横向滑动 + 表头吸顶 + 名称列冻结」，并输出截图。

   用法：
     set NODE_PATH=<托管 node_modules>
     node mobile_check.js [目标HTML路径] [截图输出目录]
   默认目标为 ../持仓看板-离线版.html（单文件离线版，自带数据）
*/
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = __dirname;
const target = process.argv[2] || path.join(ROOT, '..', '持仓看板-离线版.html');
const outDir = process.argv[3] || path.join(ROOT, '..');
// 支持直接传线上 URL（验证已发布的版本），否则按本地文件处理
const isUrl = /^https?:/i.test(target);
const url = isUrl ? target : 'file:///' + target.replace(/\\/g, '/');
const PREFIX = isUrl ? '线上-' : '';

const results = [];
const check = (name, cond, extra = '') => {
  results.push([!!cond, name, extra]);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? '  → ' + extra : ''}`);
};

(async () => {
  if (!isUrl && !fs.existsSync(target)) {
    console.error('找不到目标文件：' + target + '\n先执行 python make_standalone.py');
    process.exit(1);
  }
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },   // iPhone 14 逻辑分辨率
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForSelector('#posBody tr[data-code]', { timeout: 15000 });
  await page.waitForSelector('#posTable thead th', { timeout: 15000 });
  await page.waitForTimeout(300);   // 等渲染稳定（线上取数据有延迟）

  check('无页面错误', errs.length === 0, errs.join(' | '));

  // 1) 表格结构没有被拆成卡片
  const layout = await page.evaluate(() => {
    const tbl = document.querySelector('#posTable');
    const row = document.querySelector('#posBody tr[data-code]');
    const tw = tbl.closest('.tw');
    const tds = Array.from(row.children);
    const nameCell = tds[1];
    return {
      tableDisplay: getComputedStyle(tbl).display,
      rowDisplay: getComputedStyle(row).display,
      tdDisplay: getComputedStyle(tds[2]).display,
      twScroll: tw.scrollWidth,
      twClient: tw.clientWidth,
      nameSticky: getComputedStyle(nameCell).position,
      theadSticky: getComputedStyle(document.querySelector('#posTable thead th')).position,
      pageScrollW: document.documentElement.scrollWidth,
      pageClientW: document.documentElement.clientWidth,
      hintVisible: getComputedStyle(document.querySelector('.scroll-hint')).display,
    };
  });
  check('表格 display = table', layout.tableDisplay === 'table', layout.tableDisplay);
  check('数据行 display = table-row', layout.rowDisplay === 'table-row', layout.rowDisplay);
  check('单元格未被改成 flex/block', layout.tdDisplay === 'table-cell', layout.tdDisplay);
  check('容器可横向滚动', layout.twScroll > layout.twClient + 50,
    `scrollWidth ${layout.twScroll} > clientWidth ${layout.twClient}`);
  check('页面本身不横向溢出', layout.pageScrollW <= layout.pageClientW + 1,
    `page ${layout.pageScrollW} vs ${layout.pageClientW}`);
  check('名称列 position = sticky', layout.nameSticky === 'sticky', layout.nameSticky);
  check('表头 position = sticky', layout.theadSticky === 'sticky', layout.theadSticky);
  check('滑动提示在手机端可见', layout.hintVisible === 'block', layout.hintVisible);

  // 2) 冻结列真的生效：横向滚动后名称列被钉在容器左边缘，且仍完整可见
  const stickyOk = await page.evaluate(() => {
    const tw = document.querySelector('#posTable').closest('.tw');
    const cell = document.querySelector('#posBody tr[data-code] > td:nth-child(2)');
    const natural = cell.getBoundingClientRect().left;
    tw.scrollLeft = 400;                       // 向右滚 400px
    const pinned = cell.getBoundingClientRect().left;
    return {
      natural, pinned, scrolled: tw.scrollLeft,
      twLeft: tw.getBoundingClientRect().left,
      cellRight: cell.getBoundingClientRect().right,
      twRight: tw.getBoundingClientRect().right,
    };
  });
  // 未滚动时名称列在 # 列右侧（自然位置），滚动后被钉在容器左边缘
  check('名称列被钉在容器左边缘',
    Math.abs(stickyOk.pinned - stickyOk.twLeft) <= 2,
    `容器左 ${stickyOk.twLeft.toFixed(0)}，名称列左 ${stickyOk.natural.toFixed(0)} → ${stickyOk.pinned.toFixed(0)}`);
  check('冻结的名称列完整可见', stickyOk.cellRight <= stickyOk.twRight + 1,
    `名称列右 ${stickyOk.cellRight.toFixed(0)} ≤ 容器右 ${stickyOk.twRight.toFixed(0)}`);

  // 3) 出图：直接对元素截图，避免整页滚动位置干扰
  const shot = async (sel, name) => {
    const el = page.locator(sel).first();
    await el.scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    await el.screenshot({ path: path.join(outDir, name) });
  };

  await page.evaluate(() => { document.querySelector('#posTable').closest('.tw').scrollLeft = 0; });
  await shot('#holdings .card', PREFIX + '手机端-持仓明细-左端.png');
  await page.evaluate(() => { document.querySelector('#posTable').closest('.tw').scrollLeft = 999; });
  await page.waitForTimeout(150);
  await page.locator('#holdings .card').first().screenshot({ path: path.join(outDir, PREFIX + '手机端-持仓明细-滑到右侧.png') });

  await page.evaluate(() => { document.querySelector('#watchTable').closest('.tw').scrollLeft = 0; });
  await shot('#watch .card', PREFIX + '手机端-自选扫描.png');

  // 4) 桌面端回归：改动只应影响窄屏
  const desk = await ctx.newPage();
  await desk.setViewportSize({ width: 1440, height: 900 });
  await desk.goto(url, { waitUntil: 'load' });
  await desk.waitForSelector('#posBody tr[data-code]', { timeout: 15000 });
  await desk.waitForTimeout(300);
  const d = await desk.evaluate(() => ({
    hint: getComputedStyle(document.querySelector('.scroll-hint')).display,
    tableDisplay: getComputedStyle(document.querySelector('#posTable')).display,
    namePosition: getComputedStyle(document.querySelector('#posBody tr[data-code] > td:nth-child(2)')).position,
    twOverflow: getComputedStyle(document.querySelector('#posTable').closest('.tw')).overflowX,
  }));
  check('桌面端：滑动提示隐藏', d.hint === 'none', d.hint);
  check('桌面端：表格仍是 table', d.tableDisplay === 'table', d.tableDisplay);
  check('桌面端：名称列未被冻结', d.namePosition === 'static', d.namePosition);
  check('桌面端：容器 overflow-x = auto', d.twOverflow === 'auto', d.twOverflow);
  await desk.locator('#holdings .card').first().screenshot({ path: path.join(outDir, PREFIX + '电脑端-持仓明细-对照.png') });

  await browser.close();
  const failed = results.filter(r => !r[0]).length;
  console.log('\n共 %d 项，失败 %d 项；截图已输出到 %s', results.length, failed, outDir);
  process.exit(failed ? 1 : 0);
})();

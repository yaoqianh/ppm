# 部署到 GitHub Pages（每交易日收盘后自动更新）

> 结论先行：**数据接口不需要改代码**。仓库里已经有完整的自动更新流水线
> （`.github/workflows/update.yml`），你只要把这个仓库推到 GitHub、打开 Pages，
> 之后每个交易日收盘后会自动抓取行情 → 重算评分 → 重新部署。

---

## 零、手机随时看：WorkBuddy「发布为应用」（当前采用）

不想折腾 GitHub，只想电脑关机也能在手机上打开 → 用发布能力把`.publish` 目录推到云端链接：

1. 双击 `update.bat` 更新数据（或发截图给 AI 让它更新）
2. 双击 `publish.bat` —— 生成 `.publish/`（只含 `index.html` + `assets/` + `data/dashboard.json`，
   **不会上传** `positions.json` / `trades.json` / Python 引擎）
3. 告诉 AI「发布/更新线上版本」，链接不变、内容被覆盖

优点：5 分钟上线，不依赖本机；缺点：**内容是发布那一刻的快照，不会自己日更**，
需要每天更新时由 AI 重新 publish 一次（或改走下面的 GitHub 方案）。

> 隐私提醒：分享链接拿到的人能看到 `dashboard.json` 里的持仓明细、成本与盈亏，
> 不要转发到公开群；下线方式同目录执行 unpublish。
>
> ⚠️ 该托管的 CDN 会长期缓存 `/` 与 `/assets/*`，所以**每次发布后入口链接要带新的版本参数**
> （如 `?v=20260913`）才能让浏览器拿到新版；`stamp_assets.py` 负责给 css/js 打内容指纹。

---

## 零之二、常见问题：GitHub Pages 是不是每次更新都要重新配置？

**不是。Pages 是「一次性配置」，之后永不再配。** 分工如下：

| 场景 | 你要做什么 | 需要重新配置吗 |
|---|---|---|
| 每交易日收盘后刷新行情/评分 | 什么都不用做（Actions 定时跑） | 否 |
| 你调仓了 | 发截图给 AI，AI 改 `positions.json` 并 push | 否 |
| 想改评分权重 / 汇率 | 让 AI 改 `config/*.json` 并 push | 否 |
| 改了仓库名 / 换自定义域名 | 重新设一次 Pages | 是（仅此一种） |

而且**网址永远不变**（`https://<用户名>.github.io/<仓库名>/`），不像发布链接那样每次要换 `?v=` 参数。
推送到仓库后约 1~2 分钟线上生效。

一次性配置需要两样东西：
1. 一个 GitHub 仓库（建议**公开**，免费额度才能用 Pages）
2. 一个有权限推送的令牌（`repo` 权限；如果用 classic token，**还要勾上 `workflow`**，
   否则推不动 `.github/workflows/update.yml`）

> 隐私：Pages 站点是公开的，任何人拿到网址都能看到持仓明细、成本与盈亏。

---

## 一、它现在是怎么工作的

```
定时任务(每交易日 15:30 / 17:30 北京时间)
   └─ GitHub Actions 云端跑 python run_update.py --no-backfill
        ├─ 腾讯财经 qt.gtimg.cn        实时行情
        ├─ 腾讯财经 web.ifzq.gtimg.cn  前复权日线（技术指标）
        ├─ 东方财富 datacenter         A 股财报（ROE / 营收YoY / 归母YoY / 净利率）
        └─ 输出 data/dashboard.json + data/history.json → 上传 → 部署到 Pages
```

- **不需要改任何接口代码**：`run_update.py` 每次运行都是拉最新数据。
- **只有三种情况需要你动手改文件**：
  1. 买卖了股票 → 改 `data/positions.json`（持仓/成本/数量）
  2. 想记历史买卖 → 改 `data/trades.json`
  3. 想调评分权重 / 汇率 / 风控额度 → 改 `config/score_config.json`、`config/settings.json`
  改完 `git push`，Actions 会立刻重跑并重新部署。
- **汇率是手工常量**：`config/settings.json` 里 `"HKD": 0.86`。港币汇率漂移较大时手动改一下，
  港股市值、权重都按它折算。

---

## 二、三步上线（约 10 分钟）

### 1) 在 GitHub 上建空仓库

打开 <https://github.com/new>，仓库名例如 `portfolio-dashboard`，
**不要**勾选 “Add a README file”（本地已经有仓库了，避免冲突）。

### 2) 本地推送（Git Bash 里执行）

```bash
cd "C:/Users/Baidu BV/WorkBuddy/2026-09-09-14-48-59/portfolio-dashboard"

# 换成你的地址（SSH 或 HTTPS 二选一）
git remote add origin git@github.com:<你的用户名>/portfolio-dashboard.git
# 或者：git remote add origin https://github.com/<你的用户名>/portfolio-dashboard.git

git push -u origin main
```

> 若用 HTTPS，GitHub 现在要求用 **Personal Access Token** 当密码
> （Settings → Developer settings → Personal access tokens → Tokens(classic)，勾选 `repo`）。
> 用 SSH 则先在 GitHub 添加本机公钥。

### 3) 打开 Pages

仓库 → **Settings → Pages** → *Build and deployment* → **Source** 选 **GitHub Actions**。

然后去 **Actions** 页 manually 触发一次：右上角 *Run workflow → Run workflow*，
约 1~2 分钟后绿色对勾，页面地址形如：

```
https://<你的用户名>.github.io/portfolio-dashboard/
```

---

## 三、隐私务必先想清楚 ⚠️

> **GitHub Pages 站点默认是完全公开的**，任何拿到链接的人都能看到
> `data/dashboard.json` 里的持仓代码、数量、成本价、盈亏。

| 方案 | 源码私密 | 站点私密 | 费用 |
|---|---|---|---|
| 公开仓库 + Pages | ✗ | ✗ | 免费 |
| 私有仓库 + Pages | ✓ | ✗ **站点仍然公开** | GitHub Pro（约 $4/月） |
| Cloudflare Pages + Zero Trust Access | ✓ | ✓（登录才能看） | 免费额度够用 |
| 自己的云服务器 / 内网 | ✓ | ✓ | 服务器费用 |

如果你不希望持仓被搜索引擎或陌生人看到，建议：
1. **最省事**：用 Cloudflare Pages（连同一个私有仓库，Actions 里改成部署到 CF），再开 Zero Trust Access 做邮箱验证码登录；
2. 或者干脆 **不要把真实成本写进公开库** —— 保留一份私有的 `positions.json`，推送时只发布脱敏版本。

（若坚持用 GitHub Pages，至少在 README / 页面底部提醒自己：链接不要随便转发。）

---

## 四、定时任务的几个坑（已尽量规避）

- **cron 用 UTC**：现在是 `30 7,9 * * 1-5`，即北京时间 **15:30 与 17:30**。
  两次是为了防止 GitHub 排队延迟错过收盘；同一天重复运行不会产生重复快照。
- **港股 16:00 收盘**：17:30 那次会覆盖到港股收盘价，不用额外加班次。
- **GitHub 可能延迟 15~30 分钟**，极端情况更久，这是平台限制，靠两班次兜底。
- **仓库 60 天无任何活动会停用定时任务**：我们每天都在提交数据，所以不会被停用。
- **节假日**：周一到周五照跑，但数据没变化 → 自动跳过提交，不会污染历史。

---

## 五、手动更新 / 本地依旧可用

- 云端随时补跑：GitHub → Actions → *每日更新持仓看板* → **Run workflow**。
- 本地照旧双击 `update.bat`（或 `python run_update.py`）、`preview.bat` 预览。

---

## 六、本次为适配云端做的健壮性改动

1. **财报缓存**：自动抓取的 A 股财报会写回 `data/fundamentals.json`
   （`config/settings.json → fundamentals.persist`）。
   云端 runner 万一访问不到东方财富，也能沿用上一轮的财报，不会出现大面积「数据不足」。
2. **代码/前端变更也会触发重新部署**：`.github/workflows/update.yml` 的 push 触发路径
   增加了 `index.html`、`assets/**`、`engine/**`、`run_update.py`。
3. 修复了 `engine/build.py` 中 `errors` 变量在财报抓取前未定义的问题（异常时会 NameError）。

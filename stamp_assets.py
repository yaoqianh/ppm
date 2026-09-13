# -*- coding: utf-8 -*-
"""给 index.html 里的 CSS/JS 引用打上内容版本号。

为什么需要：
    云端静态托管（WorkBuddy 发布 / 各类 CDN）会按 **完整 URL** 缓存文件。
    `assets/style.css` 这种不带参数的地址很可能长期命中旧缓存，
    导致「index.html 与 dashboard.json 都更新了，样式/脚本还是老版本」。
    给引用加上 `?v=<内容指纹>` 后，内容一变 URL 就变，缓存自然失效。

用法：
    python stamp_assets.py     # 幂等，可重复执行
"""

import hashlib
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def fingerprint(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


def main():
    idx_path = os.path.join(HERE, "index.html")
    html = io.open(idx_path, encoding="utf-8").read()

    targets = {
        "assets/style.css": fingerprint(os.path.join(HERE, "assets", "style.css")),
        "assets/app.js": fingerprint(os.path.join(HERE, "assets", "app.js")),
    }

    changed = []
    for rel, ver in targets.items():
        pat = re.compile(r'(href|src)="' + re.escape(rel) + r'(\?v=[0-9a-f]+)?"')

        def repl(m, ver=ver):
            return '%s="%s?v=%s"' % (m.group(1), rel, ver)

        new_html, n = pat.subn(repl, html)
        if new_html != html:
            changed.append("%s -> v=%s" % (rel, ver))
        html = new_html

    io.open(idx_path, "w", encoding="utf-8").write(html)
    if changed:
        print("[OK] 已更新版本号：" + "，".join(changed))
    else:
        print("[OK] 版本号已是最新，无需改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())

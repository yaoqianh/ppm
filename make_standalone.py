# -*- coding: utf-8 -*-
"""生成「单文件离线看板」。

把 index.html + assets/style.css + assets/app.js + data/dashboard.json
打包成一个自包含的 HTML：数据直接内联，不依赖 fetch、不需要起服务、
双击就能看，也能直接发到手机上用浏览器打开。

用法：
    python make_standalone.py                      # 输出到 workspace 根目录
    python make_standalone.py -o D:/看板.html      # 指定输出路径
"""

import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def build(out_path):
    html = read(os.path.join(HERE, "index.html"))
    css = read(os.path.join(HERE, "assets", "style.css"))
    js = read(os.path.join(HERE, "assets", "app.js"))
    data = read(os.path.join(HERE, "data", "dashboard.json"))
    # 校验数据是合法 JSON，避免把坏数据打进产物
    meta = json.loads(data)

    # 内联 CSS（保留原有的 properties）；引用可能带 ?v= 版本号，一并吃掉
    html = re.sub(r'<link[^>]*href="assets/style\.css[^"]*"[^>]*/?>',
                  lambda m: "<style>\n" + css + "\n</style>", html, count=1)

    # 内联数据 + 拦截 fetch，让 app.js 无需改动即可读到内联数据
    shim = (
        "<script>\n"
        "window.__PD_DATA__ = " + data + ";\n"
        "// 离线版：所有对 dashboard.json 的请求直接返回内联数据\n"
        "window.fetch = function (url) {\n"
        "  if (String(url).indexOf('dashboard.json') >= 0) {\n"
        "    return Promise.resolve({ ok: true, status: 200,\n"
        "      json: function () { return Promise.resolve(window.__PD_DATA__); } });\n"
        "  }\n"
        "  return Promise.reject(new Error('offline'));\n"
        "};\n"
        "</script>\n"
    )
    html = re.sub(r'<script src="assets/app\.js[^"]*"></script>',
                  lambda m: shim + "<script>\n" + js + "\n</script>", html, count=1)

    if re.search(r'assets/(app\.js|style\.css)', html):
        raise SystemExit("[FAIL] 内联后仍残留外部资源引用，样式/脚本会丢失")

    with io.open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(out_path)
    print("[OK] 已生成 " + out_path)
    print("     更新于 " + str(meta.get("updated_at")) +
          "　收盘日 " + str(meta.get("as_of")) +
          "　持仓 " + str(meta.get("portfolio", {}).get("position_count")) + " 只")
    print("     体积 %.0f KB，单文件自包含，可直接发到手机打开" % (size / 1024))


def main():
    ap = argparse.ArgumentParser(description="打包单文件离线看板")
    default_out = os.path.join(os.path.dirname(HERE), "持仓看板-离线版.html")
    ap.add_argument("-o", "--out", default=default_out, help="输出 HTML 路径")
    args = ap.parse_args()
    build(args.out)


if __name__ == "__main__":
    sys.exit(main())

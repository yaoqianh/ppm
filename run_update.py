# -*- coding: utf-8 -*-
"""
一键更新看板
-------------
用法：
    python run_update.py                # 抓取最新行情 → 重算评分 → 刷新页面
    python run_update.py --no-backfill  # 跳过历史回算（更快）
    python run_update.py --serve        # 更新完顺便启动本地预览服务

每天收盘后（15:30 之后）跑一次即可。也可以把新的持仓信息写入 data/positions.json 后再跑。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.build import build_dashboard  # noqa: E402


def lan_ip() -> str:
    """获取本机在局域网中的 IP，供手机访问"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    ap = argparse.ArgumentParser(description="刷新持仓看板数据")
    ap.add_argument("--no-backfill", action="store_true", help="跳过历史评分回算")
    ap.add_argument("--serve", action="store_true", help="更新后启动本地预览服务")
    ap.add_argument("--port", type=int, default=8000, help="本地服务端口，默认 8000")
    ap.add_argument("--lan", action="store_true", help="允许局域网设备（手机/平板）访问本地预览")
    ap.add_argument("--force", action="store_true", help="即使多数标的取数失败也强制写入")
    args = ap.parse_args()

    print("=" * 60)
    print("持仓看板 · 数据更新")
    print("=" * 60)
    try:
        d = build_dashboard(verbose=True, backfill=not args.no_backfill, force=args.force)
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}")
        print("\n数据未改动，上次生成的看板仍然可用。")
        sys.exit(1)

    p = d["portfolio"]
    print("-" * 60)
    print(f"总市值      ¥{p['total_mv']:,.0f}")
    print(f"总成本      ¥{p['total_cost']:,.0f}")
    print(f"浮动盈亏    ¥{p['total_pnl_amt']:,.0f}  ({p['total_pnl_pct']:+.2f}%)")
    print(f"当日盈亏    ¥{p['day_pnl']:,.0f}")
    print(f"已实现盈亏  ¥{p['realized_pnl']:,.0f}")
    print(f"大盘择时    {d['timing']['total']} / 100  {d['timing']['position']}")
    print("-" * 60)
    for h in sorted(d["holdings"], key=lambda x: -(x["weight"] or 0)):
        chg = h.get("score_change")
        arrow = "" if chg is None else ("↑" if chg > 0 else ("↓" if chg < 0 else "→"))
        print(f"  {h['name']:<8} {h['score'] or 0:>5.0f} 分 {arrow:<2}"
              f" 权重{h['weight'] or 0:>5.1f}%  盈亏{h['pnl'] or 0:>7.1f}%  {h['status']}")

    if args.serve:
        import http.server
        import socketserver
        root = os.path.dirname(os.path.abspath(__file__))
        os.chdir(root)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def end_headers(self):
                self.send_header("Cache-Control", "no-store, max-age=0")
                super().end_headers()

        bind = "0.0.0.0" if args.lan else "127.0.0.1"
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer((bind, args.port), Handler) as httpd:
            print(f"\n预览地址： http://localhost:{args.port}/  （Ctrl+C 停止）")
            if args.lan:
                print(f"手机同一 WiFi 下访问： http://{lan_ip()}:{args.port}/")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n已停止。")


if __name__ == "__main__":
    main()

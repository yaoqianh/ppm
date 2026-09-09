# -*- coding: utf-8 -*-
"""
本地预览服务

    python serve.py              # 只有本机能访问
    python serve.py --lan        # 同一 WiFi 下的手机/平板也能访问（会打印局域网地址）
    python serve.py 8080 --lan   # 指定端口

注意：这只是临时预览。要让手机随时随地方便查看，请用 README 里的 GitHub Pages 方案。
"""

import os
import socket
import sys
import http.server
import socketserver

args = [a for a in sys.argv[1:]]
LAN = "--lan" in args
PORT = 8000
for a in args:
    if a.isdigit():
        PORT = int(a)

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # 禁用缓存，避免刷新后看到旧快照
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


socketserver.TCPServer.allow_reuse_address = True
bind = "0.0.0.0" if LAN else "127.0.0.1"
with socketserver.TCPServer((bind, PORT), Handler) as httpd:
    print(f"持仓看板已启动： http://localhost:{PORT}/   （Ctrl+C 停止）")
    if LAN:
        print(f"手机同一 WiFi 下访问： http://{lan_ip()}:{PORT}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")

# -*- coding: utf-8 -*-
"""通过 GitHub Git Data API 一次性提交多个文件（绕过本机 git 的 TLS 问题）。

用法：
    python gh_commit.py <token> "<提交信息>" <文件1> [<文件2> ...]
文件路径为相对仓库根目录的路径。
"""
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request

OWNER_REPO = "yaoqianh/ppm"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)))
BRANCH = "main"


def api(method, path, token, body=None):
    url = "https://api.github.com" + path
    headers = {
        "User-Agent": "workbuddy",
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        raw = resp.read().decode("utf-8", "ignore")
        return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode("utf-8", "ignore")[:300]}


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    token, message = sys.argv[1], sys.argv[2]
    files = sys.argv[3:]

    st, ref = api("GET", "/repos/%s/git/ref/heads/%s" % (OWNER_REPO, BRANCH), token)
    if st != 200:
        print("取 ref 失败:", st, ref)
        return 1
    parent = ref["object"]["sha"]

    st, commit = api("GET", "/repos/%s/git/commits/%s" % (OWNER_REPO, parent), token)
    base_tree = commit["tree"]["sha"]
    print("父提交 =", parent[:8])

    tree_items = []
    for rel in files:
        local = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(local):
            print("跳过（本地不存在）:", rel)
            continue
        content = io.open(local, "rb").read()
        st, blob = api("POST", "/repos/%s/git/blobs" % OWNER_REPO, token, {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        if st != 201:
            print("建 blob 失败:", rel, st, blob)
            return 1
        tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print("  blob", rel, blob["sha"][:8], "(%d bytes)" % len(content))

    if not tree_items:
        print("没有可提交的文件")
        return 1

    st, tree = api("POST", "/repos/%s/git/trees" % OWNER_REPO, token,
                   {"base_tree": base_tree, "tree": tree_items})
    if st != 201:
        print("建 tree 失败:", st, tree)
        return 1

    st, newc = api("POST", "/repos/%s/git/commits" % OWNER_REPO, token,
                   {"message": message, "tree": tree["sha"], "parents": [parent]})
    if st != 201:
        print("建 commit 失败:", st, newc)
        return 1
    print("新提交 =", newc["sha"][:8])

    st, upd = api("PATCH", "/repos/%s/git/refs/heads/%s" % (OWNER_REPO, BRANCH), token,
                  {"sha": newc["sha"], "force": False})
    if st != 200:
        print("更新 ref 失败:", st, upd)
        return 1
    print("✅ 已推送到 %s，并会触发 Actions" % BRANCH)
    return 0


if __name__ == "__main__":
    sys.exit(main())

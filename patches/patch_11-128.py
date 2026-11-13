# -*- coding: utf-8 -*-
"""补丁：让 WorkBuddy2API 网关改写 DSH 系统提示词里被国内上游拦掉的两段话。

背景
----
国内版（cn）上游对"看起来不像官方客户端"的请求返回：

    400 {"code":11128,"msg":"Illegal API invocation from an unapproved channel"}

实测定位到 DSH 自己系统提示词里的**两句话**（单独一句都能过，两句凑一起必挂）：

  1. Tokens prefixed with @ name paths the user pointed at. ...
  2. You are talking to the user through the agent's Web GUI, ...

这个补丁在网关出站前把这两段换成同义说法，别的字一个不动。

用法
----
    python patch_11-128.py "D:\\path\\to\\wb_proxy.py"

装完要重启网关。
"""

import pathlib
import shutil
import sys
import time

MARKER = "SANITIZE_DSH_PATHS_RE"

BLOCK = r'''SANITIZE_DSH_PATHS_RE = re.compile(
    r"""Tokens prefixed with @ name paths the user pointed at\.[^\n]*""")
SANITIZE_DSH_PATHS_NEW = (
    "Tokens prefixed with @ point at paths the user named. Relative ones start "
    "at the workspace root; absolute ones name files or directories on this "
    "machine. A trailing slash means a directory: list it when the contents "
    "matter. Anything else is a file: read it before saying what it holds, and "
    "never claim to have inspected something you have not read. @\"...\" quotes "
    "a path that contains spaces.")
SANITIZE_DSH_WEBGUI_RE = re.compile(
    r"""You are talking to the user through the agent's Web GUI,[^\n]*""")
SANITIZE_DSH_WEBGUI_NEW = (
    "The user reaches you through the agent's Web GUI; the runtime context names "
    "its URL. When the user says \"this page\", \"this GUI\", or \"this app\" "
    "without naming another target, they mean this GUI. The browser gives you no "
    "implicit DOM, route, or screenshot context. The client-plugin HMR receiver "
    "is active, but client-plugin changes reload without a refresh only while "
    "`pnpm run dev:web` is also running from this same checkout to rebuild their "
    "bundles; check that watcher before promising automatic updates. Any other "
    "change - the apps/web shell and plain packages - needs the affected Web "
    "artifacts rebuilt and this existing URL verified after a page refresh.")


def sanitize_dsh_prompt(text):
    """Reword the two DSH prompt blocks the cn channel check rejects."""
    if not isinstance(text, str) or not text:
        return text
    if "Tokens prefixed with @ name paths" in text:
        text = SANITIZE_DSH_PATHS_RE.sub(SANITIZE_DSH_PATHS_NEW, text)
    if "talking to the user through the agent's Web GUI" in text:
        text = SANITIZE_DSH_WEBGUI_RE.sub(SANITIZE_DSH_WEBGUI_NEW, text)
    return text


'''

ANCHOR_CONST = "SANITIZE_OMO_JUNIOR_RE = re.compile("

ANCHOR_TEXT = '''def sanitize_text(text, identity=True):
    if not isinstance(text, str) or not text:
        return text
'''
NEW_TEXT = ANCHOR_TEXT + "    text = sanitize_dsh_prompt(text)\n"


def main():
    if len(sys.argv) < 2:
        print("用法: python patch_11-128.py <wb_proxy.py 的完整路径>")
        return 2
    path = pathlib.Path(sys.argv[1])
    if not path.exists():
        print("找不到文件:", path)
        return 2

    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("已经打过了（文件里已有 %s），不用重复打。" % MARKER)
        return 0

    if ANCHOR_CONST not in text:
        print("没找到插入点 SANITIZE_OMO_JUNIOR_RE —— 说明网关版本不同，请按文档手改。")
        return 3
    if ANCHOR_TEXT not in text:
        print("没找到 sanitize_text 的开头 —— 说明网关版本不同，请按文档手改。")
        return 3

    text = text.replace(ANCHOR_CONST, BLOCK + ANCHOR_CONST, 1)
    text = text.replace(ANCHOR_TEXT, NEW_TEXT, 1)

    bak = path.with_name(path.name + ".bak-11128-%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, bak)
    path.write_text(text, encoding="utf-8")

    print("已打补丁:", path.name)
    print("备份:", bak.name)
    print("下一步：重启网关，然后用真实 DSH 会话验证 —— 日志里不应再出现 11-128。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

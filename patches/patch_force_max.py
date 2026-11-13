# -*- coding: utf-8 -*-
"""补丁：让网关把每一次 DeepSeek 请求的思考档位强制抬到 max。

为什么需要
----------
DSH 每回合都会显式传 `reasoning_effort`（默认 high），客户端的显式值会盖过
网关的默认档位。所以只在模型目录里改 defaultEffort 是不生效的——必须在上游
请求体里覆盖。

插在 `if not opted_out ... != "none":` 这一行之后，所以：
  - 关掉思考（thinking disabled / effort=none）的请求照旧不动
  - 其他请求一律被抬到指定档位

优先级：WB_FORCE_REASONING_EFFORT 环境变量 > 配置文件 > 客户端传的值

用法
----
    python patch_force_max.py "D:\\path\\to\\wb_proxy.py"

装完重启网关。
"""

import pathlib
import re
import shutil
import sys
import time

MARKER = "def forced_reasoning_effort("

FUNC = '''def forced_reasoning_effort():
    """Return the thinking level every request must use, or "" to respect the client.

    Read from, in order: env WB_FORCE_REASONING_EFFORT, then a one-line file
    force_reasoning_effort.txt in the gateway dir (or in accounts/). Empty or
    "off"/"client" means "do not force".
    """
    import os
    v = (os.environ.get("WB_FORCE_REASONING_EFFORT") or "").strip().lower()
    if v:
        return "" if v in ("0", "off", "none", "false", "client") else v
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in (os.path.join(here, "force_reasoning_effort.txt"),
                     os.path.join(here, "accounts", "force_reasoning_effort.txt")):
            if os.path.exists(cand):
                with open(cand, "r", encoding="utf-8") as fh:
                    t = fh.read().strip().lower()
                return "" if t in ("", "0", "off", "none", "false", "client") else t
    except Exception:
        pass
    return "max"


'''

ANCHOR_FUNC = "def default_reasoning_effort_mode():"

# 上游这一段在各版本里都存在，只是缩进深度不同；用它当锚点最稳。
ANCHOR_RE = re.compile(
    r'(?m)^(?P<indent>[ \t]*)if not opted_out and str\(effort or ""\)\.strip\(\)\.lower\(\) != "none":[ \t]*\r?\n'
)
INJECT = '''{indent}    # 强制思考档位（patch_force_max）：客户端每回合都显式传 reasoning_effort，
{indent}    # 显式值会盖过目录默认，所以必须在这里覆盖。关掉思考的请求上面已经跳过。
{indent}    forced = forced_reasoning_effort()
{indent}    if forced:
{indent}        body["reasoning_effort"] = forced
'''


def main():
    if len(sys.argv) < 2:
        print("用法: python patch_force_max.py <wb_proxy.py 的完整路径>")
        return 2
    path = pathlib.Path(sys.argv[1])
    if not path.exists():
        print("找不到文件:", path)
        return 2

    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("已经打过了（文件里已有 forced_reasoning_effort），不用重复打。")
        return 0

    if ANCHOR_FUNC not in text:
        print("没找到 default_reasoning_effort_mode() —— 网关版本不同，请按 02 文档手改。")
        return 3

    m = ANCHOR_RE.search(text)
    if not m:
        print("没找到思考档位那段代码 —— 网关版本不同，请按 02 文档手改。")
        return 3

    indent = m.group("indent")
    text = text[:m.end()] + INJECT.format(indent=indent) + text[m.end():]
    text = text.replace(ANCHOR_FUNC, FUNC + ANCHOR_FUNC, 1)

    bak = path.with_name(path.name + ".bak-forcemax-%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, bak)
    path.write_text(text, encoding="utf-8")

    print("已打补丁:", path.name)
    print("备份:", bak.name)
    print("默认档位 = max；想改：在网关目录建 force_reasoning_effort.txt，内容写 high / medium / off。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

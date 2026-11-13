# -*- coding: utf-8 -*-
"""验证补丁真的生效了：两段提示词会被改写、强制档位读得到。

用法：
    python check_sanitize.py "D:\\path\\to\\WorkBuddy2API"
"""

import sys
import pathlib

LINE5 = ("Tokens prefixed with @ name paths the user pointed at. A relative one starts "
         "at the workspace root.")

LINE41 = ("You are talking to the user through the agent's Web GUI, whose URL the "
          "runtime context names.")


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    sys.path.insert(0, str(root))
    try:
        import wb_proxy
    except Exception as exc:
        print("导入 wb_proxy 失败:", exc)
        return 2

    ok = True

    fn = getattr(wb_proxy, "sanitize_dsh_prompt", None)
    if fn is None:
        print("[X] 没有 sanitize_dsh_prompt —— patch_11-128 没打上")
        ok = False
    else:
        a = fn(LINE5)
        b = fn(LINE41)
        print("[%s] 第 1 段（@ 路径）改写" % ("OK" if a != LINE5 else "X"))
        print("[%s] 第 2 段（Web GUI）改写" % ("OK" if b != LINE41 else "X"))
        if a == LINE5 or b == LINE41:
            ok = False
            print("    匹配条件是空的，补丁等于没装。检查 patch_11-128.py 的版本。")

    forced = getattr(wb_proxy, "forced_reasoning_effort", None)
    if forced is None:
        print("[-] 没有 forced_reasoning_effort —— patch_force_max 没打（可选）")
    else:
        print("[OK] 强制思考档位 =", repr(forced()))

    print()
    print("结论:", "全部正常" if ok else "有问题，按上面提示处理")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
一键跑完整条链路：engine -> content_bank -> build_site。

用法（在本目录打开终端）：
    python 代码1.py              全流程
    python 代码1.py --skip-web   只重算模型和内容，网页先不动
    python 代码1.py --only-web   数据没变、只想重出网页时最快

想跳过某一步，就在 STEPS 里把那一行注释掉；顺序就是列表顺序。
任何一步挂了立刻停，不拿半截数据继续往下算。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# (脚本名, 屏幕上打印的说明)
STEPS = [
    ("engine.py", "1/3 分析引擎：清洗作答数据，生成 model/model.json 与各分析 JSON"),
    ("content_bank.py", "2/3 内容库：组装 9 个知识点的例题/推导/习题 -> model/knowledge_content.json"),
    ("build_site.py", "3/3 生成网页：把模型注入模板 -> web/index.html"),
]


def run(script, label):
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        print(f"[跳过] 找不到 {path}")
        return False
    print("\n" + "=" * 70)
    print(label)
    print("=" * 70)
    t0 = time.time()
    # 用当前解释器，别指望 PATH 里的 python 是这个版本
    r = subprocess.run([sys.executable, path], cwd=HERE)
    cost = time.time() - t0
    if r.returncode != 0:
        print(f"[失败] {script} 退出码 {r.returncode}，耗时 {cost:.2f}s")
        return False
    print(f"[完成] {script} 耗时 {cost:.2f}s")
    return True


def main():
    args = set(sys.argv[1:])
    if "--only-web" in args:
        steps = [STEPS[2]]
    elif "--skip-web" in args:
        steps = STEPS[:2]
    else:
        steps = STEPS

    print(f"工作目录：{HERE}")
    ok = True
    for script, label in steps:
        if not run(script, label):
            ok = False
            break

    print("\n" + "-" * 70)
    if ok:
        print("全部完成。")
        print(f"模型：{os.path.join(HERE, 'model', 'model.json')}")
        print(f"网页：{os.path.join(HERE, 'web', 'index.html')}")
        print("双击 web/index.html 即可离线打开。")
    else:
        print("流程中断，请根据上面的报错修复后重跑。")
        sys.exit(1)


if __name__ == "__main__":
    main()

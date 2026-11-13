#!/usr/bin/env python3
"""
repatch.py — 给官方 FongMi/TV 编译好的 APK 注入自动更新能力（smali 动态发现版）。

背景：官方 release APK 经过 R8 混淆，Updater/Github 等类名每次构建都可能变化，
因此本脚本不写死任何混淆名，全部在运行时按特征发现：

  1) 更新源：官方把 "FongMi/Release" 内联进了完整 URL 常量
     "https://api.github.com/repos/FongMi/Release/releases?per_page=100"
     → 按 URL 段替换为目标仓库，App 之后的检查/下载全部指向本仓库。
  2) 检查任务：含上述 URL 常量的 synthetic Runnable 类（反编译前是 Github 异步任务），
     其 run() 里 packed-switch 按 byte 选择分支；含 URL 的分支即"联网检查+弹窗"。
     解析 packed-switch 数据段算出该分支的 byte 值。
  3) Updater 单例：从任务类的字段类型 X 出发，找静态字段 "...:LX;" 的 sget-object
     调用点（App.onActivityResumed 里就有：Updater.get()）。
  4) 注入点：App.onActivityResumed 末尾（每个 Activity 回前台都会经过，App 类名
     在 AndroidManifest 引用下不会被混淆，四个 flavor 均保留原名）。
     注入 12 小时节流的检查任务触发，形成"启动即静默检查更新"。

用法：python3 repatch.py <apktool_反编译目录> [owner/repo]
"""
import os
import re
import sys

REPO_FROM = "FongMi/Release"
URL_MARK = "api.github.com/repos/"
TASK_URL_MARK = "releases?per_page=100"
INTERVAL_MS = 12 * 3600 * 1000  # 12h 节流
MARKER_FIELD = ".field public static awu:J"  # 注入幂等标记
SKIP_LABEL = ":awu_skip"

FAILURES = []


def die(msg):
    FAILURES.append(msg)
    print(f"[FATAL] {msg}")


def walk_smali(root):
    for base, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".smali"):
                yield os.path.join(base, fn)


def read_lines(p):
    with open(p, encoding="utf-8", errors="ignore") as f:
        return f.readlines()


def write_lines(p, lines):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)


# ---------------------------------------------------------------- 1) 更新源
def patch_repo(root, repo_to):
    n = 0
    for p in walk_smali(root):
        lines = read_lines(p)
        changed = False
        for i, ln in enumerate(lines):
            if URL_MARK in ln and REPO_FROM in ln:
                lines[i] = ln.replace(REPO_FROM, repo_to)
                changed = True
        if changed:
            write_lines(p, lines)
            n += 1
    if n == 0:
        # 可能已替换过（幂等重跑），检查目标仓库是否已在 URL 里
        already = any(
            URL_MARK in ln and repo_to in ln
            for p in walk_smali(root)
            for ln in read_lines(p)
        )
        if already:
            print(f'[repo] 更新源已指向 "{repo_to}"（幂等跳过）')
        else:
            die("未找到任何 api.github.com/repos/FongMi/Release 的 const-string")
        return n
    print(f'[repo] 已把 {n} 个 smali 文件的更新源 "{REPO_FROM}" 指向 "{repo_to}"')
    return n


# ------------------------------------------------------- 2) 检查任务类发现
def find_task_class(root):
    """返回 (task_path, task_desc, updater_desc, check_byte)"""
    hits = []
    for p in walk_smali(root):
        text = "".join(read_lines(p))
        if TASK_URL_MARK in text:
            hits.append(p)
    if not hits:
        die(f'未找到含 "{TASK_URL_MARK}" 的检查任务类')
        return None
    if len(hits) > 1:
        print(f"[warn] 检查任务类出现 {len(hits)} 处，取第一个: {hits}")

    p = hits[0]
    desc = smali_class_desc(p)
    lines = read_lines(p)

    # 任务类持有的"外部类"字段类型 = Updater 类（La54;）
    ups = set()
    for ln in lines:
        t = re.search(r":(L[\w/$]+;)", ln)
        if not ln.startswith(".field") or not t:
            continue
        td = t.group(1)
        if td == desc or td.startswith(("Ljava", "Landroid", "Lorg", "Lcom/github")):
            continue
        ups.add(td)
    if len(ups) != 1:
        die(f"{desc} 中未能唯一定位 Updater 类型字段，候选: {ups}")
        return None
    upd = ups.pop()
    print(f"[task] 检查任务类 = {desc}，Updater 类 = {upd}")

    # run() 中 URL 所在分支的 pswitch 标签 → 换算 byte
    body = run_body(p)
    if body is None:
        die(f"{desc} 里没有 run() 方法")
        return None
    url_idx = next(i for i, ln in enumerate(body) if TASK_URL_MARK in ln)
    # run() 内（数据段之外）的 pswitch 块标签
    data_start = data_end = None
    for i, ln in enumerate(body):
        if ":pswitch_data_" in ln:
            data_start = i
        if data_start is not None and ".end packed-switch" in ln:
            data_end = i
            break
    labels = [
        i
        for i, ln in enumerate(body)
        if re.match(r"^\s{1,6}:(pswitch_\w+)\s*$", ln)
        and (data_start is None or i < data_start)
    ]
    before = [i for i in labels if i < url_idx]
    if not before:
        die("URL 分支前找不到 pswitch 标签")
        return None
    block_label = re.match(r"^\s*:(pswitch_\w+)", body[before[-1]]).group(1)

    # 数据段：.packed-switch 0xN → 标签列表，byte = N + index
    base, order = None, []
    if data_start is None:
        die("run() 里没有 packed-switch")
        return None
    for i in range(data_start, data_end + 1):
        m = re.search(r"\.packed-switch (0x[0-9a-fA-F-]+)", body[i])
        if m:
            base = int(m.group(1), 16)
        m = re.match(r"^\s*:(pswitch_\w+)\s*$", body[i])
        if m and i > data_start:
            order.append(m.group(1))
    if base is None or block_label not in order:
        die(f"packed-switch 解析失败 base={base} order={order} block={block_label}")
        return None
    byte = base + order.index(block_label)
    print(f"[task] 检查分支 {block_label} → byte={byte}")
    return p, desc, upd, byte


def run_body(path):
    lines = read_lines(path)
    s = e = None
    for i, ln in enumerate(lines):
        if re.match(r"^\.method.*\brun\(\)V", ln):
            s = i
        elif s is not None and ".end method" in ln:
            e = i
            break
    return lines[s : e + 1] if s is not None else None


def smali_class_desc(path):
    norm = path.replace("\\", "/")
    tail = norm.split("/smali")[-1].lstrip("/")
    return "L" + tail[: -len(".smali")] + ";"


# ------------------------------------------------------ 3) Updater 单例发现
def find_singleton(root, upd):
    pat = re.compile(r"sget-object\s+\S+,\s*(L[\w/$]+;)->([\w$]+):" + re.escape(upd))
    cands = []
    for p in walk_smali(root):
        for ln in read_lines(p):
            m = pat.search(ln)
            if m:
                cands.append((p, m.group(1), m.group(2)))
    if not cands:
        die(f"全包未找到静态字段类型为 {upd} 的单例读取")
        return None
    app_hits = [c for c in cands if c[0].replace("\\", "/").endswith("com/fongmi/android/tv/App.smali")]
    pick = app_hits[0] if app_hits else cands[0]
    print(f"[single] Updater 单例 = {pick[1]}->{pick[2]}:{upd}")
    return pick[1], pick[2]


def find_executor(root):
    """优先 Task.execute（后台线程），退回 App.a（主线程）。"""
    want = "Lcom/github/catvod/utils/Task;->execute(Ljava/lang/Runnable;)V"
    for p in walk_smali(root):
        if any(want in ln for ln in read_lines(p)):
            return want
    alt = "Lcom/fongmi/android/tv/App;->a(Ljava/lang/Runnable;)V"
    for p in walk_smali(root):
        if any(alt in ln for ln in read_lines(p)):
            print("[exec] Task.execute 不存在，退回 App.a（主线程）")
            return alt
    die("既没有 Task.execute 也没有 App.a(Runnable)")
    return None


# ------------------------------------------------------------- 4) 注入
def inject(root, single_cls, single_fld, upd, task_desc, byte, executor):
    app = None
    for p in walk_smali(root):
        if p.replace("\\", "/").endswith("com/fongmi/android/tv/App.smali"):
            app = p
            break
    if app is None:
        die("未找到 com/fongmi/android/tv/App.smali")
        return
    lines = read_lines(app)

    if MARKER_FIELD in "".join(lines):
        print("[inject] 已注入过（发现标记字段），跳过")
        return

    # 找 onActivityResumed 方法块（修饰符可能是 public final 等）
    s = e = None
    for i, ln in enumerate(lines):
        if re.match(r"^\.method.*\bonActivityResumed\b", ln):
            s = i
        elif s is not None and ".end method" in ln:
            e = i
            break
    if s is None:
        die("App.smali 里没有 onActivityResumed")
        return

    # .locals 提升到 >=8（注入用到 v0..v7）；pN 是符号寄存器，提升安全
    for i in range(s, e):
        m = re.match(r"^(\s*)\.locals\s+(\d+)", lines[i])
        if m:
            if int(m.group(2)) < 8:
                lines[i] = f"{m.group(1)}.locals 8\n"
            break

    # 末尾 return-void 前插入
    rets = [i for i in range(s, e) if re.match(r"^\s*return-void\s*$", lines[i])]
    if not rets:
        die("onActivityResumed 里找不到 return-void")
        return
    ret = rets[-1]
    block = [
        "    invoke-static {}, Ljava/lang/System;->currentTimeMillis()J\n",
        "    move-result-wide v0\n",
        "    sget-wide v2, Lcom/fongmi/android/tv/App;->awu:J\n",
        "    sub-long/2addr v0, v2\n",
        f"    const-wide/32 v2, 0x{INTERVAL_MS:x}\n",
        "    cmp-long v4, v0, v2\n",
        f"    if-ltz v4, {SKIP_LABEL}\n",
        "    invoke-static {}, Ljava/lang/System;->currentTimeMillis()J\n",
        "    move-result-wide v0\n",
        "    sput-wide v0, Lcom/fongmi/android/tv/App;->awu:J\n",
        f"    sget-object v5, {single_cls}->{single_fld}:{upd}\n",
        f"    new-instance v6, {task_desc}\n",
        f"    const/4 v7, 0x{byte:x}\n",
        f"    invoke-direct {{v6, v5, v7}}, {task_desc}-><init>({upd}B)V\n",
        f"    invoke-static {{v6}}, {executor}\n",
        f"    {SKIP_LABEL}\n",
    ]
    lines[ret:ret] = block

    # 加幂等标记字段（同时兼作上次检查时间戳）
    first_method = next(i for i, ln in enumerate(lines) if ln.startswith(".method"))
    lines.insert(first_method, MARKER_FIELD + "\n")

    write_lines(app, lines)
    print(f"[inject] App.onActivityResumed 注入完成（12h 节流，检查 byte={byte}）")


# ---------------------------------------------------------------- main
def main():
    if len(sys.argv) < 2:
        print("usage: python3 repatch.py <apktool_out_dir> [owner/repo]")
        sys.exit(2)
    root = sys.argv[1]
    repo_to = sys.argv[2] if len(sys.argv) > 2 else "dimogu/nightly-sync"

    patch_repo(root, repo_to)
    t = find_task_class(root)
    if t is None:
        finish()
        return
    _p, task_desc, upd, byte = t
    s = find_singleton(root, upd)
    if s is None:
        finish()
        return
    ex = find_executor(root)
    if ex is None:
        finish()
        return
    inject(root, s[0], s[1], upd, task_desc, byte, ex)
    finish()


def finish():
    if FAILURES:
        print(f"repatch FAILED: {FAILURES}")
        sys.exit(1)
    print("repatch done")


if __name__ == "__main__":
    main()

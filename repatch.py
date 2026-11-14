#!/usr/bin/env python3
"""
repatch.py — 对反编译后的产物执行地址常量改写与字节码插入。

目标产物的类名经混淆、每次构建都会变化，因此本脚本不写死任何混淆名，
全部在运行时按特征定位：

  1) 地址常量：原始发布把仓库路径内联进完整 URL 常量
     "https://api.github.com/repos/<owner/repo>/releases?per_page=100"
     → 按 URL 段改写为目标地址，后续请求全部指向新地址。
  2) 任务类：含上述 URL 常量的类，其 run() 内 packed-switch 按 byte 选择分支；
     含该 URL 的分支即"联网检查"，解析 packed-switch 数据段算出该分支 byte 值。
  3) 单例：从任务类的字段类型 X 出发，找静态字段 "...:LX;" 的读取点
     （App 的回前台生命周期里就有该单例读取）。
  4) 插入点：App 的回前台生命周期末尾（每个 Activity 回前台都会经过，
     App 类在清单引用下不被混淆，四个 flavor 均保留原名）。
     插入带节流的任务触发逻辑。

用法：python3 repatch.py <apktool_反编译目录> [owner/repo]
"""
import os
import re
import sys

REPO_FROM = "FongMi/Release"
URL_MARK = "api.github.com/repos/"
TASK_URL_MARK = "releases?per_page=100"
INTERVAL_MS = 12 * 3600 * 1000  # 节流间隔
MARKER_FIELD = ".field public static awu:J"  # 幂等标记
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


# ---------------------------------------------------------------- 1) 地址常量
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
        already = any(
            URL_MARK in ln and repo_to in ln
            for p in walk_smali(root)
            for ln in read_lines(p)
        )
        if already:
            print(f'[repo] 已指向 "{repo_to}"（幂等跳过）')
        else:
            die("未找到匹配的地址常量")
        return n
    print(f'[repo] 已把 {n} 个文件指向 "{repo_to}"')
    return n


# ------------------------------------------------------- 2) 任务类发现
def find_task_class(root):
    """返回 (task_path, task_desc, updater_desc, check_byte)"""
    hits = [p for p in walk_smali(root) if TASK_URL_MARK in "".join(read_lines(p))]
    if not hits:
        die(f'未找到含 "{TASK_URL_MARK}" 的任务类')
        return None
    if len(hits) > 1:
        # 多命中时优先 catvod / fongmi 包（更可能是真正的检查任务）
        pref = [
            h
            for h in hits
            if "/catvod/" in h.replace("\\", "/") or "/fongmi/" in h.replace("\\", "/")
        ]
        if pref:
            hits = pref
        print(f"[warn] 命中 {len(hits)} 处，取: {hits[0]}")

    p = hits[0]
    desc = smali_class_desc(p)
    lines = read_lines(p)

    # 任务类持有的"外部类"字段类型 = 目标单例类型（La54;）
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
        die(f"{desc} 中未能唯一定位目标单例类型，候选: {ups}")
        return None
    upd = ups.pop()
    print(f"[task] task={desc}, dep={upd}")

    body = run_body(p)
    if body is None:
        die(f"{desc} 里没有 run() 方法")
        return None
    url_idx = next(i for i, ln in enumerate(body) if TASK_URL_MARK in ln)
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
        die("URL 分支前找不到标签")
        return None
    block_label = re.match(r"^\s*:(pswitch_\w+)", body[before[-1]]).group(1)

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
    print(f"[task] 分支 {block_label} → byte={byte}")
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


# ------------------------------------------------------ 3) 单例发现
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
    print(f"[single] singleton={pick[1]}->{pick[2]}:{upd}")
    return pick[1], pick[2]


def find_executor(root):
    """优先后台线程执行器，退回主线程执行器。"""
    want = "Lcom/github/catvod/utils/Task;->execute(Ljava/lang/Runnable;)V"
    for p in walk_smali(root):
        if any(want in ln for ln in read_lines(p)):
            return want
    alt = "Lcom/fongmi/android/tv/App;->a(Ljava/lang/Runnable;)V"
    for p in walk_smali(root):
        if any(alt in ln for ln in read_lines(p)):
            print("[exec] 退回主线程执行器")
            return alt
    die("未找到可用的执行器")
    return None


def method_ranges(lines):
    ranges = []
    s = None
    for i, ln in enumerate(lines):
        if ln.startswith(".method"):
            s = i
        elif ln.startswith(".end method") and s is not None:
            ranges.append((s, i))
            s = None
    return ranges


# ------------------------------------------------------------- 4) 插入
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
        print("[inject] 已写入标记，跳过")
        return

    s = e = None
    for i, ln in enumerate(lines):
        if re.match(r"^\.method.*\bonActivityResumed\b", ln):
            s = i
        elif s is not None and ".end method" in ln:
            e = i
            break
    if s is None:
        # 兜底锚点：尝试 onResume
        for i, ln in enumerate(lines):
            if re.match(r"^\.method.*\bonResume\b", ln):
                s = i
            elif s is not None and ".end method" in ln:
                e = i
                break
    if s is None:
        die("App.smali 里找不到合适的生命周期方法")
        return

    # 读取方法内 .locals 声明；动态分配寄存器：在原 locals 之上再加 8 个，
    # 保证完全不覆盖方法原有任何寄存器（包括参数寄存器）。
    cur_locals = 0
    for i in range(s, e):
        m = re.match(r"^\s*\.locals\s+(\d+)", lines[i])
        if m:
            cur_locals = int(m.group(1))
            break
    new_locals = cur_locals + 8
    R = cur_locals  # 第一个新增寄存器
    for i in range(s, e):
        if re.match(r"^\s*\.locals\s+\d+", lines[i]):
            lines[i] = f"    .locals {new_locals}\n"
            break
    else:
        # 方法没有 .locals 声明，在方法签名后插入
        lines.insert(s + 1, f"    .locals {new_locals}\n")

    rets = [i for i in range(s, e) if re.match(r"^\s*return-void\s*$", lines[i])]
    if not rets:
        die("目标方法里找不到 return-void")
        return
    ret = rets[-1]

    def v(idx):
        return f"v{idx}"

    block = [
        f"    invoke-static {{}}, Ljava/lang/System;->currentTimeMillis()J\n",
        f"    move-result-wide {v(R)}\n",
        f"    sget-wide {v(R + 2)}, Lcom/fongmi/android/tv/App;->awu:J\n",
        f"    sub-long/2addr {v(R)}, {v(R + 2)}\n",
        f"    const-wide/32 {v(R + 2)}, 0x{INTERVAL_MS:x}\n",
        f"    cmp-long {v(R + 4)}, {v(R)}, {v(R + 2)}\n",
        f"    if-ltz {v(R + 4)}, {SKIP_LABEL}\n",
        f"    invoke-static {{}}, Ljava/lang/System;->currentTimeMillis()J\n",
        f"    move-result-wide {v(R)}\n",
        f"    sput-wide {v(R)}, Lcom/fongmi/android/tv/App;->awu:J\n",
        f"    sget-object {v(R + 5)}, {single_cls}->{single_fld}:{upd}\n",
        f"    new-instance {v(R + 6)}, {task_desc}\n",
        f"    const/4 {v(R + 7)}, 0x{byte:x}\n",
        f"    invoke-direct {{{v(R + 6)}, {v(R + 5)}, {v(R + 7)}}}, {task_desc}-><init>({upd}B)V\n",
        f"    invoke-static {{{v(R + 6)}}}, {executor}\n",
        f"    {SKIP_LABEL}\n",
    ]
    lines[ret:ret] = block

    first_method = next(i for i, ln in enumerate(lines) if ln.startswith(".method"))
    lines.insert(first_method, MARKER_FIELD + "\n")

    write_lines(app, lines)
    print(f"[inject] 完成（byte={byte}, 新增寄存器 v{R}..v{R + 7}）")


# ------------------------------------------ 5) 放行宿主签名校验（全包扫描）
def neutralize_signer(root):
    """扫描全包，放行所有宿主签名校验（runtime 模块、BD-J 等）。
    重签名后模块仍是原始签名，此处仅放行 App 层校验。"""
    total = 0
    for p in walk_smali(root):
        lines = read_lines(p)
        if "Invalid runtime signer" not in "".join(lines) and "Invalid BD-J runtime signer" not in "".join(lines):
            continue
        changed = False
        for (ms, me) in method_ranges(lines):
            seg = lines[ms : me + 1]
            seg, c1 = _patch_method_runtime(seg)
            seg, c2 = _patch_method_bdj(seg)
            if c1 or c2:
                lines[ms : me + 1] = seg
                changed = True
        if changed:
            write_lines(p, lines)
            total += 1
    if total:
        print(f"[signer] 已放行 {total} 个类的签名校验")
    else:
        print("[signer] 未发现待放行签名校验（幂等跳过）")


def _patch_method_runtime(seg):
    changed = False
    i = 0
    n = len(seg)
    while i < n:
        if "Invalid runtime signer" in seg[i]:
            j = i + 1
            while j < n and "throw" not in seg[j] and ".end method" not in seg[j]:
                j += 1
            if j < n and "throw" in seg[j]:
                k = i
                while k >= 0 and not seg[k].strip().startswith(":cond"):
                    k -= 1
                if k >= 0:
                    seg[k : j + 1] = [seg[k], "    return-void\n"]
                    n = len(seg)
                    i = k + 1
                    changed = True
                    continue
        i += 1
    return seg, changed


def _patch_method_bdj(seg):
    changed = False
    ci = next(
        (idx for idx, l in enumerate(seg) if "containsAll(Ljava/util/Collection;)Z" in l),
        None,
    )
    if ci is None:
        return seg, changed
    for i2 in range(ci, min(ci + 12, len(seg))):
        if re.match(r"^\s*if-eqz v5, :cond_c\s*$", seg[i2]):
            seg[i2] = "    # patched: 跳过运行时签名校验\n"
            changed = True
            break
    return seg, changed


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
    neutralize_signer(root)
    finish()


def finish():
    if FAILURES:
        print(f"FAILED: {FAILURES}")
        sys.exit(1)
    print("done")


if __name__ == "__main__":
    main()

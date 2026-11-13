#!/usr/bin/env python3
"""
repatch.py — 给官方 FongMi/TV 编译好的 APK 注入自动更新能力。

背景：FongMi/TV 编译需要两个私有 aar（lib-mpvplayer / lib-decoder-ffmpeg），
官方从不发布，无法直接源码编译。但官方每次都会发布编译好的 APK
（github.com/FongMi/Release）。本脚本直接修改官方已编译 APK，绕开私有依赖：

  1) 把 smali 中的更新源常量 "FongMi/Release" 改为目标仓库（如 dimogu/nightly-sync）
     —— 官方 App 的 Github.REPO 常量，App 据此去 GitHub Releases 拉更新。
  2) 在 App.onActivityResumed 注入：进入前台即静默检查更新
     —— 官方原本只调 notifyModules，从不主动检查，故「感觉没有自动更新」。

用法：
  python3 repatch.py <apktool_反编译目录> [owner/repo]
"""
import os
import re
import sys

REPO_FROM = "FongMi/Release"
REPO_TO_DEFAULT = "dimogu/nightly-sync"


def patch_repo_strings(root: str, repo_to: str) -> int:
    """把 smali 里孤立的 "FongMi/Release" 字符串改为目标仓库。

    只改值恰好等于该串的 const-string，不动
    'https://.../FongMi/Release/...' 这类完整 URL，避免误伤 runtime module 等引用。
    """
    count = 0
    for base, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".smali"):
                continue
            p = os.path.join(base, fn)
            with open(p, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            if f'"{REPO_FROM}"' in text:
                text = text.replace(f'"{REPO_FROM}"', f'"{repo_to}"')
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text)
                count += 1
    print(f'[repo] 已将 {count} 个 smali 文件中的 "{REPO_FROM}" 改为 "{repo_to}"')
    return count


def find_app_smali(root: str):
    hits = []
    for base, _dirs, files in os.walk(root):
        for fn in files:
            if fn != "App.smali":
                continue
            p = os.path.join(base, fn)
            with open(p, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            if "onActivityResumed" in text and "Updater" in text:
                hits.append(p)
    return hits


def inject_auto_check(app_smali: str) -> bool:
    with open(app_smali, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    # 定位 onActivityResumed 方法，并读取 .registers 数量
    start = None
    regs = 16
    for i, ln in enumerate(lines):
        if re.search(r"\.method.*\bonActivityResumed\b", ln):
            start = i
            for j in range(i, min(i + 40, len(lines))):
                m = re.search(r"\.registers\s+(\d+)", lines[j])
                if m:
                    regs = int(m.group(1))
                    break
            break
    if start is None:
        print(f"[warn] 未在 {app_smali} 找到 onActivityResumed，跳过自动检查注入")
        return False

    # 在方法体内找 notifyModules 的 invoke 行，并扫描最大 v 编号
    target = None
    maxv = -1
    for i in range(start, len(lines)):
        ln = lines[i]
        if ln.strip() == ".end method":
            break
        for mv in re.findall(r"\bv(\d+)", ln):
            maxv = max(maxv, int(mv))
        if "Updater;->notifyModules" in ln and "invoke-virtual" in ln:
            target = i

    if target is None:
        print(f"[warn] 未找到 notifyModules 调用，跳过注入")
        return False

    # 临时寄存器：用方法内未出现的最大编号 +1/+2（确保 < regs）
    tmp1 = maxv + 1
    tmp2 = maxv + 2
    if tmp2 >= regs:
        tmp1, tmp2 = 0, 1  # 退化（onActivityResumed 体极短，通常安全）
    label = ":auto_update_skip"

    inject = [
        f"    instance-of v{tmp1}, p1, Landroidx/fragment/app/FragmentActivity;\n",
        f"    if-eqz v{tmp1}, {label}\n",
        f"    invoke-virtual {{p1}}, Lcom/fongmi/android/tv/Updater;->get()Lcom/fongmi/android/tv/Updater;\n",
        f"    move-result-object v{tmp1}\n",
        f"    const/4 v{tmp2}, 0x0\n",
        f"    check-cast p1, Landroidx/fragment/app/FragmentActivity;\n",
        f"    invoke-virtual {{v{tmp1}, p1, v{tmp2}}}, Lcom/fongmi/android/tv/Updater;->start(Landroidx/fragment/app/FragmentActivity;Z)V\n",
        f"{label}\n",
        "    nop\n",
    ]
    lines = lines[: target + 1] + inject + lines[target + 1 :]
    with open(app_smali, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"[inject] 已在 {app_smali} 注入自动检查 (tmp v{tmp1}/v{tmp2})")
    return True


def main():
    if len(sys.argv) < 2:
        print("usage: python3 repatch.py <apktool_out_dir> [owner/repo]")
        sys.exit(1)
    root = sys.argv[1]
    repo_to = sys.argv[2] if len(sys.argv) > 2 else REPO_TO_DEFAULT

    patch_repo_strings(root, repo_to)
    apps = find_app_smali(root)
    if not apps:
        print("[warn] 未找到 App.smali，自动检查注入跳过（仅改更新源）")
    else:
        for a in apps:
            inject_auto_check(a)
    print("repatch done")


if __name__ == "__main__":
    main()

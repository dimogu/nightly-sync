# fongmi 影视 · 自动更新版（repatch 重打包）

给开源影音 App **FongMi/TV（fongmi 影视）** 加上**自动更新**能力，且**以后自动更新升级上来的版本也自带自动更新**，形成闭环。

> 仓库名刻意低调（中性随机名），不出现在主页简介、不宣传。

## 为什么是「重打包」而不是「源码编译」

FongMi/TV 官方编译需要两个私有预编译库 `lib-mpvplayer-release.aar`、`lib-decoder-ffmpeg-release.aar`，
**官方从不发布这两个文件，也无任何公开下载源**（已穷尽核实：官方 git、官方 Release 各 tag、上游 mpv-android 全部 404）。
这是所有想编译 FongMi/TV 的人的通用门槛，与「自动更新」功能本身无关。

但官方**每次都会发布编译好的完整 APK**（`FongMi/Release`）。所以我们**直接拿官方已编译 APK 改**，
绕开私有依赖，得到「就是 fongmi 影视本身 + 自动更新补丁」。

## 闭环原理

```
官方 FongMi/Release 出新版本
      │  (GitHub Actions 每 6 小时检测)
      ▼
下载官方 4 个 APK
      │  apktool 反编译 → repatch.py 注入：
      │    ① 更新源 "FongMi/Release" → 改指本仓库 dimogu/nightly-sync
      │    ② App 启动/回前台 自动静默检查更新（注入 onActivityResumed）
      │  重打包 + 用本仓库 keystore 重签名
      ▼
发布到本仓库 Release（同名 APK：leanback-arm64_v8a.apk 等）
      │
      ▼
你手机上的 App 启动即静默检查 → 发现新版 → 弹窗询问下载安装
      │
      ▼
升级上来的版本依旧内置自动更新、更新源仍指向本仓库 ──► 永久闭环
```

## 你只需要做一次的事

1. 进本仓库 **Releases**，下载对应你设备的 APK（电视用 `leanback-*`，手机用 `mobile-*`；
   64 位设备选 `arm64_v8a`，老设备选 `armeabi_v7a`）。
2. **先卸载官方原版 fongmi 影视**（重要：我们的包是重签名的，与官方不同签名，不能直接覆盖安装）。
3. 安装我们发布的版本。之后一切全自动。

## 以后怎么用

- App 启动 / 回到前台时会**静默检查**更新，发现新版本才**弹窗**询问是否下载安装。
- 官方一出新版，本仓库 Actions 次日（或 6 小时内）自动重打包发布，App 会自动收到。
- 不需要你做任何事，也不会回退到「无自动更新」的官方版。

## 仓库结构

```
.github/workflows/auto-update.yml   重打包流水线（每6小时检查官方、下载、注入、签名、发布）
repatch.py                          smali 注入脚本（改更新源 + 注入自动检查）
app/src/main/.../Github.java        源码补丁参考（若未来拿到 mpv/ffmpeg aar 改用源码编译方案时可用）
app/src/main/.../Updater.java       源码补丁参考
app/src/main/.../App.java          源码补丁参考
docs/get-mpv-aar.md                 （备选方案）如何获取那两个私有 aar
README.md                          本文件
```

## 备选：若你拿到了那两个 aar

如果你通过 FongMi 社区等渠道拿到了 `lib-mpvplayer-release.aar` 和 `lib-decoder-ffmpeg-release.aar`，
可上传到本仓库 `mpv-libs` Release，并把流水线切回源码编译方案（见 `docs/get-mpv-aar.md` 与一个
源码编译版 workflow 变体）——那样就完全从源码出包，不需要重打包官方二进制。

## 安全与低调说明

- 本仓库**公开**（因为 App 用匿名请求 `api.github.com/repos/.../releases` 拉更新，私有仓库对匿名返回 404，
  会导致永远「检测不到更新」）。但仓库名中性、不关联主页、不宣传，做到低调。
- 发布的 APK = 官方二进制 + 自动更新补丁，用本仓库 keystore 签名。这是 FongMi/TV 开源项目的再分发，
  符合其开源许可；仅用于个人使用。
- **签名 keystore 已备份在你本地**（`signing-backup/keystore.jks`，口令见同目录 `README.txt`）。
  它是后续所有更新的签名密钥，**丢失将无法覆盖安装升级，请妥善保存**。

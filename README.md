# FongMi 影视 · 自带自动更新（闭环方案 · 补丁式 fork）

给开源项目 **FongMi/TV（fongmi 影视）** 加上**真正的自动更新**，并保证：
**通过自动更新升级上来的版本，依然自带自动更新功能。**

> 核心思路：fongmi 影视其实**已经内置了更新框架**（`Updater` + `UpdateService` + `Github` 检查 GitHub Release），
> 但它默认检查的是**官方 `FongMi/Release`**。官方版不带"自更新闭环"，所以"更新一次就又没了"。
> 本方案把更新源改成**你自己的仓库 Release**，并用 GitHub Actions 每天自动同步官方最新代码、打上自动更新补丁、重新出包。
> 这样每次升级来的版本，更新源仍指向你自己，闭环成立。

---

## 〇、本仓库是「补丁式」的（重点）

**本仓库不存放官方完整源码**，只放 3 个自动更新补丁 + 一条构建流水线：

```
fongmi-auto-update/
├── app/src/main/java/com/fongmi/android/tv/
│   ├── utils/Github.java      # ① 更新源：编译时被流水线自动锁定为本仓库
│   ├── Updater.java           # ② 自动检查节流 + 取消不再永久关闭
│   └── App.java               # ③ 进入前台时静默触发自动检查
└── .github/workflows/
    └── auto-update.yml        # ④ 每天拉官方源码 → 套补丁 → 编译 → 发到本仓库 Release
```

流水线每次运行才去 `git clone` 官方最新源码并套补丁，**官方源码不落库**。
这样做的好处：

- 仓库极小（几 KB），**非常低调**，不容易被一眼认出是 fongmi 的修改版；
- 永远跟随官方最新代码，不怕官方改结构；
- 你不需要维护一份庞大的 fork。

> 因为 App 要从 GitHub Releases **匿名**拉更新，而私有仓库的 Releases 对匿名请求返回 404，
> 所以**放 APK 的本仓库必须是「公开」的**。我们用**中性名字**（如 `nightly-sync`）且不写进主页简介、不宣传，做到低调。

---

## 一、闭环原理

```
┌─────────────────────────────────────────────────────────────────┐
│  官方 FongMi/TV (github.com/FongMi/TV)                            │
│   —— 流水线每次运行才 clone，官方持续维护，无需你动                │
└───────────────┬─────────────────────────────────────────────────┘
                │ 每天 cron 拉取最新代码 + 套补丁
                ▼
┌─────────────────────────────────────────────────────────────────┐
│  本仓库 (github.com/你的用户名/nightly-sync)  【公开·中性名】      │
│   —— 只放了 3 个补丁 + workflow；Github.REPO 被自动改为本仓库      │
└───────────────┬─────────────────────────────────────────────────┘
                │ Actions 签名编译 + 发布 APK
                ▼
┌─────────────────────────────────────────────────────────────────┐
│  本仓库 Release (.../nightly-sync/releases)                       │
│   tag: v5.8.8 / v5.8.9 / ...                                     │
│   assets: leanback-arm64_v8a.apk、mobile-armeabi_v7a.apk …        │
└───────────────┬─────────────────────────────────────────────────┘
                │ App 启动静默检查（12h 节流）→ 发现新版本弹窗
                ▼
        用户设备上的 App（带自动更新）
                │  下载并安装新 APK（依旧内置自动更新）
                └─────────────── 回到顶部，闭环 ✅
```

**关键点**：更新源永远是你自己的 Release，而你发布的每一个 APK 都来自"官方代码 + 自动更新补丁"，
所以无论更新多少次，自动更新功能都不会丢。

---

## 二、补丁都改了什么

- **Github.java**：原来硬编码 `REPO = "FongMi/Release"`，现改为占位并在流水线里自动替换成 `${{ github.repository }}`（即本仓库）。**你无需手动改。**
- **Updater.java**：新增 `startAuto()` 与 12 小时自动检查节流；`onCancel` 不再 `putUpdate(false)`，点"取消"只是本次跳过，不会把自动更新彻底关死。
- **App.java**：在 `onActivityResumed` 注入 `Updater.get().startAuto(activity)`，实现"启动/回到前台即静默检查"。
- 其余文件（`build.gradle`、`versionCode/versionName`）完全不动，版本号随官方自动递增，更新校验永远成立。

---

## 三、部署步骤（一次性）

> 下面假设你已经用脚本/机器人把本仓库内容推到了 `github.com/你的用户名/nightly-sync`（公开）。
> 若是你自己推，等同把所有文件 commit 上去即可。

### 1. 准备签名 Secrets
Release 包需要签名。准备一个 keystore（没有就本地生成一个）：

```bash
keytool -genkey -v -keystore keystore.jks -keyalg RSA -keysize 2048 -validity 10000 -alias mykey
base64 -w0 keystore.jks > keystore.txt
```

到仓库 **Settings → Secrets and variables → Actions → New repository secret**，添加 4 个：

| 名称 | 值 |
|------|----|
| `KEYSTORE_BASE64` | `keystore.txt` 的全部内容 |
| `KEY_ALIAS` | 你的 alias（如 `mykey`） |
| `KEY_PASSWORD` | key 密码 |
| `STORE_PASSWORD` | keystore 密码 |

### 2. 启用并触发 Actions
到仓库 **Actions** 页，启用 workflow；然后手动 **Run workflow** 跑一次（或等每天 UTC 16:00 自动跑）。
首次运行会发布当前官方版本（如 `v5.8.8`）到本仓库 Release。

### 3. 安装使用
从本仓库 Release 下载对应 APK 安装。之后：
- App 启动/回到前台会**静默检查**更新（12 小时内只查一次，不打扰）；
- 发现新版本**弹窗**询问是否下载安装；
- 以后官方 FongMi/TV 一出新版，Actions 次日自动同步并发布到本仓库 Release，App 自动收到更新。

---

## 四、APK 文件命名规则（重要）

App 的 `Updater` 按 `flavor-abi.apk` 匹配更新包，`Github.getAsset` 据此查找 Release 里的 asset。
流水线已自动重命名为以下 4 个名字，请勿改动：

| 文件名 | 含义 |
|--------|------|
| `leanback-arm64_v8a.apk` | 电视版 64 位 |
| `leanback-armeabi_v7a.apk` | 电视版 32 位 |
| `mobile-arm64_v8a.apk` | 手机版 64 位 |
| `mobile-armeabi_v7a.apk` | 手机版 32 位 |

> 注意 `arm64-v8a` 在文件名里写成 `arm64_v8a`（连字符变下划线），与 `Updater` 里的 `PackageAbi.get().replace('-','_')` 一致。

---

## 五、构建环境说明（首次可能要微调）

流水线构建环境取自官方 `gradle/libs.versions.toml`（2026-11 快照）：

- Gradle 9.7.1（由 wrapper 自动下载）
- AGP 9.5.1，Java 21，compileSdk/targetSdk 37，minSdk 24，buildTools 37.0.0
- Android SDK 通过官方 commandline-tools **手动安装**（未使用 `android-actions/setup-android`，因其在新版 cmdline-tools 上会因已废弃的 `tools` 包报错）
- **NDK 由 Gradle 按需自动下载**（官方未在 version catalog 固定 NDK 版本，交给 Gradle 拉取最稳妥，避免版本猜错）

**首次运行若构建失败**，多半是 SDK/NDK 版本或 Chaquopy 下载问题，看 Actions 日志：
- 报 SDK platform 缺失 → 调整 `env.ANDROID_COMPILE_SDK`；
- 报 NDK 相关 → 可在 workflow 的 `env` 增 `ANDROID_NDK_VERSION` 并用 `sdkmanager "ndk;<版本>"` 预装；
- 报 Chaquopy/Python 下载失败 → 通常是网络抖动，重跑一次即可。
这些都是 CI 环境的常规微调，与自动更新逻辑无关。

---

## 六、常见问题

**Q：为什么仓库必须是公开的？**
A：App 用匿名请求 `api.github.com/repos/.../releases` 拉更新；私有仓库对匿名返回 404，App 会永远"检测不到更新"。公开 + 中性名是"既可用又低调"的折中。若你坚持完全私有，需要把只读 Token 写进 App（有泄露风险）或改用自定义更新 JSON——按需再议。

**Q：官方更新后，我的自动更新补丁会丢吗？**
A：不会。本仓库的补丁文件是固定内容，流水线每次都用它们覆盖官方同名文件；官方对其它文件（含 `versionCode/versionName`）的改动在 clone 后的官方源码里自然生效。

**Q：会不会一天发好几次 Release？**
A：不会。流水线在发布前检查 tag 是否已存在；只有 `versionName` 变化（即官方出了新版本）才会发布，平时跳过。

**Q：digest / 校验和没填会不会有问题？**
A：无影响。`Updater` 的 `UpdatePackage` 在 GitHub asset 没有 `digest` 字段时跳过校验，只用 `size + versionCode` 校验，足够安全。

**Q：TV 上怎么安装 APK？**
A：App 已具备 `REQUEST_INSTALL_PACKAGES` 权限与 FileProvider，`UpdateService` 下载完成后会用系统安装器拉起安装。部分机型需在"设置→安全"允许"安装未知应用"。

---

## 七、手动触发一次（测试流程）

在仓库 Actions 页面点 **Run workflow**，勾选 `force` 可强制重新构建并发布当前版本（用于验证流水线）。

---

> 许可：FongMi/TV 本身为 GPL-3.0，本补丁同样以 GPL-3.0 提供，请遵守原项目许可。

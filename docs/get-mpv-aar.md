# 获取 mpv / ffmpeg 预编译 aar（FongMi/TV 自动更新构建必需）

## 背景：为什么需要这两个文件

本仓库的自动更新流水线在 GitHub 云端拉取官方 FongMi/TV 源码、套上自动更新补丁后，
需要编译出 APK。但 FongMi/TV 编译时有一个**官方硬依赖**：

```
app/libs/lib-mpvplayer-release.aar      # mpv 播放器 native 库
app/libs/lib-decoder-ffmpeg-release.aar # FFmpeg 解码器 native 库
```

这两个是 mpv / FFmpeg 的预编译 Android 库（aar 格式，本质是 zip，内含 `jni/*.so`）。

**关键点（已逐一核实，非猜测）：**
- 上游 `mpv-android/mpv-android` 与 FongMi 的 fork（`fongmi`/`master` 分支）**都是播放器 APK 项目**（`com.android.application`），根本不产出 aar；
- `FongMi/Release` 仅有 `5.8.6 / 5.8.7 / 5.8.8 / module` 四个 tag，对全部 tag 试探下载这两个 aar **均 404**；
- `FongMi/mpv`、`FongMi/FFmpeg` 是 C 源码仓库（被 buildscripts 交叉编译），不是 aar 来源；
- 因此这两个 aar 是 **FongMi 私有的 mpv library 构建产物，官方从不发布，无任何公开下载源**。

结论：这是所有想编译 FongMi/TV 的人都要过的门槛，与"自动更新"功能本身无关。
自动更新的代码补丁、CI 流水线、更新源闭环逻辑均已完整且验证正确，唯一阻塞即此依赖。

---

## 途径一：从 FongMi 社区获取（最实际、最推荐）

这两个 aar 在 FongMi 的二次开发者圈子里是"编译标配"——凡是自己编译过 FongMi/TV 的人，
本地 `app/libs/` 里都有它们。去社区要一份即可（每个约几十 MB，标准 aar 文件）：

- **Telegram**：`t.me/fongmi` 官方频道；搜 "FongMi TV 编译 / 开发" 相关群组
- **QQ / 微信群**：搜 "fongmi"、"影视"、"catvod" 相关的开发交流群
- **中文论坛**：相关的影视/直播 APK 开发论坛、贴吧

问法参考：
> 谁有编译 FongMi/TV 用的 `lib-mpvplayer-release.aar` 和 `lib-decoder-ffmpeg-release.aar`？
> 两个 aar 文件就行，谢谢。

拿到后按下方"接入本仓库 CI"上传即可。

---

## 途径二：你自己用 FongMi 私有的 mpv library 构建

前提：你手上有 FongMi 私有的 mpv library 构建仓库/脚本（一般不在公开网络）。
如果有：

1. 按该私有仓库的方式产出两个 aar（通常 `gradle assembleRelease` 后产物在 `*/build/outputs/aar/`）；
2. 将它们**重命名**为 `lib-mpvplayer-release.aar` 和 `lib-decoder-ffmpeg-release.aar`；
3. 按下方步骤上传到本仓库。

> 注意：直接 clone 上游 `mpv-android` 是**产出不了**这两个 aar 的（上游是 APK 项目），
> 必须是 FongMi 改造过的 library 版本。

---

## 接入本仓库 CI（拿到文件后做这一步）

把两个 aar 上传到本仓库一个固定名为 `mpv-libs` 的 Release，流水线会自动下载复用：

```bash
# 在本机（已登录 gh，且对 dimogu/nightly-sync 有写权限）执行
gh release create mpv-libs -R dimogu/nightly-sync \
  --title "mpv/ffmpeg prebuilt aar" \
  --notes "FongMi/TV 编译依赖：lib-mpvplayer-release.aar + lib-decoder-ffmpeg-release.aar" \
  /本地路径/lib-mpvplayer-release.aar \
  /本地路径/lib-decoder-ffmpeg-release.aar
```

上传后，在仓库 **Actions** 页手动 Run 一次 `Build & Release` 工作流即可。

### CI 如何复用
流水线在编译前会自动执行 `gh release download mpv-libs`，把两个 aar 放到
`upstream-src/app/libs/`。**只要 `mpv-libs` 这个 Release 存在，之后每次官方出新版本，
流水线都会自动同步并出包，无需你再管**——自动更新闭环就此跑通。

---

## 退路

若暂时拿不到这两个 aar：
- 自动更新的全部代码与 CI 已交付在本仓库（5 个文件），你随时可以保留；
- 你仍可使用官方 `FongMi/Release` 的 APK（不带自动更新）正常观影；
- 一旦你拿到 aar，按本文件"接入本仓库 CI"一节操作，闭环立即成立。

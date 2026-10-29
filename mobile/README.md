# 科研系统 Android 客户端

手机版是 `ResearchHub` 的安全入口，不在手机内复制桌面端 SQLite 数据库。

## 使用

1. 在电脑双击 `ResearchHub.exe`；
2. 记下窗口显示的 `LAN address for members`；
3. 手机与电脑连接同一私有网络；
4. 安装 APK，输入该地址并登录。

正式文件名为 `ResearchOS-Mobile-vX.Y.Z-Android.apk`。也可以在手机浏览器打开 Hub，然后点击页面底部“在 Android App 中打开当前中心”，自动带入地址。

公网中心必须使用 HTTPS。HTTP 只允许 loopback、`.local` 和 RFC1918 私有地址。客户端不启用 JavaScript 原生桥，不申请相机、麦克风或存储权限；文件选择通过 Android 系统选择器完成。

## 开发构建

在 `mobile/android` 目录使用 Gradle：

```bash
gradle :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

正式 GitHub 工作流会构建并校验可侧载 APK。由于手机端只是稳定的 ResearchHub 客户端，日常功能更新主要由 Hub 页面提供，不要求每次桌面升级都重装 APK。

当前公开 APK 使用 GitHub 构建环境提供的侧载签名，不通过 Google Play 分发。安装时只应信任本项目公开 Release；如果未来更换签名，Android 可能要求先卸载旧客户端，但 Hub 中的数据不会受影响。

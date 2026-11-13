# App 更新后闪退：Android 12 恢复说明

更新日期：2026-11-13。此处的 4.3.3 / 4.2.6 是步道乐跑 App 版本，v2.3.0 是 Windows 助手版本，两者不同。

## 再次升级复测（2026-11-13）

[应用宝版本页](https://sj.qq.com/appdetail/com.lptiyu.tanke) 当日列出的 Android 版本为 **4.3.3**（更新日期 2026-11-07）。在完成离线实例备份及文件校验后，再次使用此前从 App 更新中保留的原始安装包覆盖安装；Android 包管理器确认版本为 4.3.3 / versionCode 233。

安装返回 `Success`，但启动数秒后仍退出至模拟器桌面，日志再次出现 `SIGSEGV` 和 `libdexfile.so`。因此：**4.3.3 安装成功，启动验证失败，尚未完成兼容适配。** 不能将 Android 的 `am start` 返回 `Status: ok` 当作应用持续运行成功。可用旧版的恢复步骤仍见下文。

[脱敏复测结果](app-4.3.3-retest.json) 仅记录版本、安装包校验值和验证结论，不包含账号、设备标识、虚拟机备份或原始日志。Windows 助手仍为 v2.3.0，发行 ZIP 未改动；本仓库不分发步道乐跑 APK。

## 已验证的情况

在一台 MuMu Android 12 实例上，App 更新到 4.3.3 后启动即退出。ARM64 模式日志出现 `SIGSEGV`，调用栈包含 `libdexfile.so`；同一安装包切换为 ARM32 后仍崩溃。日志不能证明这是反作弊拦截，也不能推断其他设备必然存在同样故障。

使用原来已验证的、签名一致的 4.2.6 安装包覆盖回退后，原登录状态保留，已登录首页及助手自动联动恢复。**这属于回退恢复，不是修复了 4.3.3，也没有加入隐藏模拟器或代跑的防检测补丁。** 本次没有更改 Windows 助手代码，现有 v2.3.0 EXE 和下载包校验值不变。

## 回退前准备

1. 在 App 中处理尚未结束的记录，关闭助手。不要在运行中更新、回退或改变定位。
2. 确认有可恢复的 MuMu 实例备份，并备份助手的 `user-data`。仅备份该文件夹不包含 Android App 的登录和数据库。
3. 保留当前安装包；从可信来源取得自己原来使用的 4.2.6 安装包。项目不分发第三方 APK，也不提供修改签名的安装包。
4. 使用 MuMu 多开器确认目标 Android 12 实例编号。不要照抄他人的实例编号或 ADB 端口。

## 使用 ADB 保留数据覆盖回退

以下在 Windows PowerShell 执行。修改安装目录、实例编号和 APK 路径为你自己的值：

```powershell
$mumuRoot = 'C:\Program Files\Netease\MuMu'
$instanceIndex = 1  # 示例：替换为需要修复的实例编号
$manager = Join-Path $mumuRoot 'nx_main\MuMuManager.exe'
$adb = Join-Path $mumuRoot 'nx_main\adb.exe'
$apk = 'D:\Downloads\budao-lepao-4.2.6.apk'
$info = (& $manager info -v $instanceIndex | ConvertFrom-Json)
if (-not $info.is_android_started -or -not $info.adb_port) {
    throw '请先启动目标 Android 12 实例，等待启动完成后重试'
}
$device = '{0}:{1}' -f $info.adb_host_ip, $info.adb_port
& $adb connect $device
& $adb -s $device shell dumpsys package com.lptiyu.tanke |
    Select-String 'versionName=|primaryCpuAbi='
& $adb -s $device install -r -d --abi arm64-v8a $apk
```

必须看到 `Success` 才继续。`-r` 请求保留数据替换安装，`-d` 请求允许版本回退，但并非所有系统都允许；旧版也未必兼容新版已迁移的数据。若提示版本降级不允许、签名不一致或其他错误，停止操作，保留备份与报错；不要以卸载或清除数据来绕过错误。本次成功不能保证每台设备都保留登录。

安装成功后先单独打开 App，确认能进入首页且账号仍有效，再打开助手。检查助手连接的实例、GPS 和自动联动选项；App 能打开不等于新版本页面识别已经适配。

## 避免再次安装当前不兼容版本

本次回退后，App 再次发起了更新安装。取消该更新，并在 Android 设置中关闭“允许来自此来源的应用”。也可对同一目标实例执行：

```powershell
& $adb -s $device shell cmd appops get com.lptiyu.tanke REQUEST_INSTALL_PACKAGES
& $adb -s $device shell cmd appops set com.lptiyu.tanke REQUEST_INSTALL_PACKAGES deny
& $adb -s $device shell cmd appops get com.lptiyu.tanke REQUEST_INSTALL_PACKAGES
```

读回应为 `deny`。这只关闭 App 自己发起安装的权限，不会禁用应用商店更新，也不会消除 App 自己的更新提示。若重新打开时出现“安装未知应用”设置页，直接返回，不要开启开关；其他更新来源需分别检查。

将来确认新版本兼容后，可在系统设置恢复该权限；若原值为 `allow`，对应恢复命令为：

```powershell
& $adb -s $device shell cmd appops set com.lptiyu.tanke REQUEST_INSTALL_PACKAGES allow
```

不要使用未知来源的所谓反作弊补丁。启动兼容性、定位运行和服务端判定是不同问题，本项目不承诺模拟数据不会被识别。

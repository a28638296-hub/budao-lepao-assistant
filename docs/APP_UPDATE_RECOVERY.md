# App 更新后闪退：Android 12 保留数据恢复

更新日期：2026-11-17。**4.3.1 / 4.3.3 是步道乐跑 App 版本，v2.3.1 是 Windows 助手版本，两者不同。** 本文只介绍 Android 12 的恢复方法。

## 当前结果与适用范围

| App 版本 | 本次结果 |
|---|---|
| **4.3.1（231）** | MuMu Android 12：进入首页与登录页；本人微信授权后，桌面冷启动成功，已观察到计时、距离增长和打卡 |
| 4.3.3（233） | MuMu Android 12 / 15：安装成功，但启动阶段崩溃；仍未修复 |
| 4.3.2（232） | 已测安装包没有稳定进入可用页面，不作为恢复建议 |
| 4.2.6 | 早期能启动，但 2026-11-17 已提示“当前版本过低，请升级后使用~”，不再作为当前方案 |

本次是使用官方 4.3.1 恢复，不是修改或修复了 4.3.3。**4.3.1 在 Android 15 尚未测试**；完整记录结束、上传和成绩有效性未验证。其他电脑及后续服务版本政策仍需单独确认。

可查阅 [本次脱敏结果](app-4.3.1-recovery.json)、[4.3.3 历史复测](app-4.3.3-retest.json)。不包含账号、学校、设备标识、私人路径、原始日志、截图或备份。

## 1. 取得并核验真正的 4.3.1

下载 [腾讯官方 CDN 的 4.3.1 历史安装包](https://imtt2.dd.qq.com/sjy.10001/sjy.00004/16891/apk/9463D5F0222DC2D16C33B4C96BD03A51.apk)，另存为 `budao-lepao-4.3.1.apk`。应用商店首页可能提供更高版本，不等于此文件。

| 项目 | 核验值 |
|---|---|
| 包名 | `com.lptiyu.tanke` |
| 版本 / versionCode | `4.3.1` / `231` |
| 大小 | `183846804` 字节 |
| SHA256 | `48f0b9a90e3844db2fec8e11ec7d7ee836fa5338d12917e7c85023c259a68c5b` |
| MD5（辅助识别） | `9463d5f0222dc2d16c33b4c96bd03a51` |
| 签名证书 SHA256 | `a6af18d3f047f3a210e9762f41b6f923f445151625fb5533c41748c7b02a6a24` |

在下载目录执行 `Get-FileHash .\budao-lepao-4.3.1.apk -Algorithm SHA256`，输出应与表格相同。网页写着旧版本不能代替文件核验，本次遇到过标注旧版却返回 4.3.3 的下载页。链接失效、文件大小或 SHA256 不符时停止安装，不要换一个同名文件继续。

本仓库和 Windows ZIP 不附带第三方 APK，只提供原始下载链接和已验证文件标识。

## 2. 保留原账号：先备份完整实例

1. 在 App 中处理尚未结束的记录，关闭助手。不要在运行中更新、回退或改变定位。
2. 备份整个 MuMu 实例。使用多开器备份/导出时，确认操作成功、文件非零且能用于恢复。导出报错或 0 字节文件不能当作备份。
3. 若使用离线目录副本，完全关闭目标实例，完整复制与该实例对应的虚拟磁盘和配置，保留原 MuMu 引擎及路径信息；不要在运行中复制或只复制一个配置文件。本次是在原电脑建立完整副本，并逐文件核对 SHA256，大小约 7.9 GB；其他实例大小不同。
4. 使用 MuMu 多开器确认目标 Android 12 实例编号。不要照抄他人的实例编号或 ADB 端口。
5. 另行备份助手的 `user-data`。该文件夹只包含助手配置和路线历史，不包含 Android App 的账号及数据库。保留当前安装包、完整实例备份，并确认备份盘空间足够。

尚无可用备份时不要降级。全新且没有账号的实例可直接安装 4.3.1，再按 [安装教程](INSTALL.md#3-安装-app-并完成微信登录) 本人登录。

## 3. 使用 ADB 保留数据覆盖安装

以下在 Windows PowerShell 执行。修改安装目录、实例编号和 APK 路径为你自己的值：

```powershell
$mumuRoot = 'C:\Program Files\Netease\MuMu'
$manager = Join-Path $mumuRoot 'nx_main\MuMuManager.exe'
$adb = Join-Path $mumuRoot 'nx_main\adb.exe'
$apk = Join-Path $env:USERPROFILE 'Downloads\budao-lepao-4.3.1.apk'
if (-not (Test-Path -LiteralPath $manager) -or -not (Test-Path -LiteralPath $adb)) {
    throw 'MuMu 安装路径不正确，请修改 mumuRoot'
}
if (-not (Test-Path -LiteralPath $apk)) { throw '找不到 APK，请修改 apk 路径' }
$expectedHash = '48f0b9a90e3844db2fec8e11ec7d7ee836fa5338d12917e7c85023c259a68c5b'
if ((Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash -ne $expectedHash) {
    throw 'APK 校验值不匹配，停止安装'
}
& $manager info -v all
$instanceIndex = Read-Host '根据名称和 Android 版本，输入目标实例编号'
if ($instanceIndex -notmatch '^\d+$') { throw '实例编号必须是非负整数' }
$info = (& $manager info -v $instanceIndex | ConvertFrom-Json)
if ($info.android_version -notmatch '^12(\.|$)') {
    throw '此恢复步骤仅在 Android 12 验证，请重新核对实例'
}
if (-not $info.is_android_started -or -not $info.adb_port) {
    throw '请先启动目标 Android 12 实例，等待启动完成后重试'
}
$device = '{0}:{1}' -f $info.adb_host_ip, $info.adb_port
& $adb connect $device
if ($LASTEXITCODE -ne 0) { throw '连接失败，停止安装' }
& $adb -s $device shell dumpsys package com.lptiyu.tanke |
    Select-String 'versionName=|primaryCpuAbi='
# 确认已备份、没有正在记录的跑步。这里不卸载 App、不清除数据。
& $adb -s $device shell am force-stop com.lptiyu.tanke
& $adb -s $device install -r -d $apk
if ($LASTEXITCODE -ne 0) { throw '安装失败，请保留错误和备份，不要卸载或清除数据' }
& $adb -s $device shell dumpsys package com.lptiyu.tanke |
    Select-String 'versionName=|versionCode='
```

必须看到 `Success` 才继续。`-r` 请求保留数据替换安装，`-d` 请求允许版本回退，但并非所有系统都允许；旧版也未必兼容新版已迁移的数据。若提示版本降级不允许、签名不一致或其他错误，停止操作，保留备份与报错；不要以卸载或清除数据来绕过错误。本次成功不能保证每台设备都保留登录。

确认安装后显示 `versionName=4.3.1`、`versionCode=231`。本次先在无账号实例验证了 4.3.3 → 4.3.1 保留数据安装，再处理已备份的原实例；没有额外指定 ABI。

## 4. 本人重新授权，再验证重开

1. 单独打开 App，确认能持续停留在可操作页面。安装成功或 `am start` 返回 `Status: ok` 不算通过。
2. 若提示“登录信息失效”，确认微信在同一实例，用原来的微信账号重新授权。本次也需要这一步，不等于清除了数据。
3. 检查账号、学校和历史信息；异常时保留备份与报错，不要反复清除数据。
4. 没有进行中记录时，关闭 App 再打开，再关闭整个模拟器并通过助手桌面快捷方式重开。确认连接的仍是原 Android 12 实例，登录仍有效。
5. 等待定位与持续启动检查完成，GPSTest 显示期间不要手动切换页面，再按正常流程使用。

本次原实例重新登录及桌面冷启动通过，随后观察到计时页面距离从 0.06 km 增至 0.14 km、打卡成功。完整结束、上传和服务端成绩有效性未验证。

## 5. 避免再次安装已失败的 4.3.3

当前已测环境先保持 4.3.1。遇到 4.3.3 更新提示先取消；可在 Android 设置关闭乐跑的“允许来自此来源的应用”，或对上文已确认的同一个目标实例执行：

```powershell
& $adb -s $device shell cmd appops get com.lptiyu.tanke REQUEST_INSTALL_PACKAGES
# 记下原值，再按需关闭该 App 自行发起安装的权限。
& $adb -s $device shell cmd appops set com.lptiyu.tanke REQUEST_INSTALL_PACKAGES deny
& $adb -s $device shell cmd appops get com.lptiyu.tanke REQUEST_INSTALL_PACKAGES
```

读回应为 `deny`。这只关闭 App 自己发起安装的权限，不会禁用应用商店更新，也不会消除更新提示或解除服务端版本限制。安装权限页可以返回，其他更新来源需单独检查。

将来确认新版本兼容后，可在系统设置恢复该权限；若原值为 `allow`，对应恢复命令为：

```powershell
& $adb -s $device shell cmd appops set com.lptiyu.tanke REQUEST_INSTALL_PACKAGES allow
```

若 4.3.1 也提示版本过低，本方案在该环境即未恢复业务，保留版本及提示，等待新的已验证方案。启动兼容性与服务端判定是不同问题，本项目不承诺记录有效或具有防检测能力。

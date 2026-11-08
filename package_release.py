"""Package only public release files; never copy local user data."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = "2.3.0"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"LePaoRunner-v{VERSION}-windows-x64.zip"
    files = [ROOT / name for name in (
        "README.md", "CHANGELOG.md", "LICENSE", "NOTICE.md", "config.example.json")]
    files += sorted((ROOT / "docs").glob("*.md"))
    files += [ROOT / "docs/compatibility-results.json"]
    files += sorted((ROOT / "licenses").glob("*.txt"))
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / "dist/LePaoRunner.exe", "LePaoRunner.exe")
        for path in files:
            z.write(path, path.relative_to(ROOT).as_posix())
        z.writestr("portable-mode.txt", "Keep this file beside LePaoRunner.exe. Settings are stored in user-data.\n")
        z.writestr("开始使用.txt", (
            "步道乐跑 v2.3.0 — Windows 64 位版，无需安装 Python\n\n"
            "1. 完整解压 ZIP 到可写文件夹，保留 portable-mode.txt。\n"
            "2. 在 MuMu 创建/选择 Android 12 实例。安装并本人登录步道乐跑 4.2.6；微信登录需要同实例安装微信。\n"
            "3. 按 docs/INSTALL.md 安装指定 GPSTest 组件，打开位置开关并授予精确位置权限。\n"
            "4. 双击 LePaoRunner.exe，选择 MuMu 安装根目录，查询并选择 Android 12 实例编号。\n"
            "5. 核对六点坐标和坐标系，勾选启用自动联动，点击保存默认。示例坐标不能直接用于其他场地。\n"
            "6. 从助手打开 App，完成 App 的开始确认和倒计时，观察日志、距离与地图；仅首页点击不代表已经开始记录。\n"
            "7. 右键 LePaoRunner.exe 发送到桌面快捷方式；以后使用同一个入口。\n\n"
            "旧版升级：先阅读 docs/INSTALL.md 第 8 节，备份并迁移 user-data，更新桌面快捷方式。\n"
            "重新选择实例：在程序目录 PowerShell 执行 .\\LePaoRunner.exe --setup\n"
            "详细教程：https://github.com/a28638296-hub/budao-lepao-assistant/blob/main/docs/INSTALL.md\n"
            "下载包不含第三方 APK、账号或个人配置。使用前核对 SHA256SUMS.txt。\n"
        ))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (args.output / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"{archive}\nSHA256 {digest}")


if __name__ == "__main__":
    main()

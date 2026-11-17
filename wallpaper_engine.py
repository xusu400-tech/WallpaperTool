"""
壁纸核心引擎：获取分辨率 / 下载图片 / 设置壁纸
"""
import os
import io
import time
import hashlib
import ctypes
import ctypes.wintypes as wt
import requests
from pathlib import Path

# Windows API 常量
SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE   = 0x01
SPIF_SENDCHANGE      = 0x02

# 注册表位置
REG_DESKTOP = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Wallpapers"

CACHE_DIR = Path.home() / ".WallpaperTool" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ============ 分辨率检测 ============
def get_screen_resolution():
    """调用 GetSystemMetrics 获取主屏分辨率"""
    user32 = ctypes.windll.user32
    width  = user32.GetSystemMetrics(0)   # SM_CXSCREEN
    height = user32.GetSystemMetrics(1)   # SM_CYSCREEN
    return width, height


# ============ 图片下载 ============
_APP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 彼岸图网统一请求头：搜索页、列表页、原图、缩略图都复用这一套。
# 站点前置了百度 WAF，缺 UA/Referer 的裸请求会被判为异常流量（HTTP 405）
NETBIAN_HEADERS = {
    "User-Agent": _APP_UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.netbian.com/",
    "Connection": "keep-alive",
}

NETBIAN_BLOCKED_MSG = (
    "彼岸图网当前对本机网络触发了百度 WAF 人机验证（HTTP 405）。\n"
    "这属于站点侧的访问限制，补 UA/Referer 也无法绕过。\n\n"
    "可以尝试：\n"
    "1. 等待 1~5 分钟后重新搜索（WAF 拦截通常会自动解除）；\n"
    "2. 在浏览器打开 https://www.netbian.com 手动完成滑块验证；\n"
    "3. 临时切换到「Bing 每日」等其他图源。"
)


class NetbianBlockedError(RuntimeError):
    """彼岸图网触发 WAF 人机验证（HTTP 405）"""

    def __init__(self, msg=NETBIAN_BLOCKED_MSG):
        super().__init__(msg)


# img.netbian.com 有防盗链：不带站内 Cookie 时部分图片返回 405
_NETBIAN_SESSION = None


def get_netbian_session(refresh=False):
    """获取（或重建）彼岸图网会话；访问站内首页获取防盗链 Cookie"""
    global _NETBIAN_SESSION
    if refresh:
        _NETBIAN_SESSION = None
    if _NETBIAN_SESSION is None:
        s = requests.Session()
        s.headers.update(NETBIAN_HEADERS)
        try:
            s.get("https://www.netbian.com/", timeout=10,
                  proxies=_get_proxies_safe())
        except Exception:
            pass
        _NETBIAN_SESSION = s
    return _NETBIAN_SESSION


def netbian_get(url: str, timeout: int = 15, stream: bool = False):
    """彼岸图网所有请求的统一入口：统一请求头 + 405/WAF 识别

    405 时先重建会话重试一次（Cookie 过期场景）；仍 405 则抛出
    NetbianBlockedError，交给界面显示明确提示而不是原始 HTTP 报错。
    """
    resp = get_netbian_session().get(
        url, timeout=timeout, stream=stream, proxies=_get_proxies_safe())
    if resp.status_code == 405:
        time.sleep(1)
        resp = get_netbian_session(refresh=True).get(
            url, timeout=timeout, stream=stream, proxies=_get_proxies_safe())
        if resp.status_code == 405:
            raise NetbianBlockedError()
    resp.raise_for_status()
    return resp


# 极简壁纸（bz.zzzmh.cn / imageai.zzzmh.cn）统一请求头：
# 站点与图床都校验 Referer，裸请求会被拒绝
ZZZMH_HEADERS = {
    "User-Agent": _APP_UA,
    "Accept": "text/html,application/json;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://bz.zzzmh.cn/",
}


def zzzmh_get(url: str, timeout: int = 15, stream: bool = False):
    """极简壁纸所有请求的统一入口（列表、详情、原图、缩略图都走这里）"""
    resp = requests.get(url, headers=ZZZMH_HEADERS, timeout=timeout,
                        stream=stream, proxies=_get_proxies_safe())
    resp.raise_for_status()
    return resp


def fetch_image_bytes(url: str, timeout: int = 10) -> bytes:
    """下载图片原始字节（缩略图等场景），失败时抛出异常由调用方处理"""
    if "netbian.com" in url:
        return netbian_get(url, timeout=timeout).content
    if "zzzmh.cn" in url:
        return zzzmh_get(url, timeout=timeout).content
    # th.wallhaven.cc（图床）国内可直连，wallhaven.cc（API）被墙需代理：
    # 缩略图优先直连，失败再走代理，两种网络环境下都能加载
    if "th.wallhaven.cc" in url:
        for proxies in (None, _get_proxies_safe()):
            try:
                resp = requests.get(url, timeout=timeout, proxies=proxies,
                                    headers={"User-Agent": _APP_UA})
                resp.raise_for_status()
                return resp.content
            except requests.exceptions.RequestException:
                continue
        raise RuntimeError(f"缩略图下载失败: {url}")
    last_err = None
    for attempt in range(3):   # SSL 握手偶发被重置，加退避重试
        try:
            resp = requests.get(url, timeout=timeout, proxies=_get_proxies_safe(),
                                headers={"User-Agent": _APP_UA})
            resp.raise_for_status()
            return resp.content
        except requests.exceptions.RequestException as e:
            last_err = e
            time.sleep(0.8 * (attempt + 1))
    raise last_err


def _get_proxies_safe():
    try:
        from config import get_proxies   # 延迟导入，避免循环依赖
    except ImportError:                  # 缺少 config.py 时降级为直连
        return None
    return get_proxies()


def download_image(url: str, filename: str = None) -> Path:
    """
    下载图片到本地缓存目录，返回文件路径
    """
    if not filename:
        filename = url.split("/")[-1].split("?")[0]
        if not filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            filename += ".jpg"

    save_path = CACHE_DIR / filename
    if "netbian.com" in url:
        # 统一走会话请求头；WAF 拦截直接抛明确异常，不做无意义的 3 次重试
        resp = netbian_get(url, timeout=30, stream=True)
    elif "zzzmh.cn" in url:
        resp = zzzmh_get(url, timeout=30, stream=True)
    else:
        resp = None
        for attempt in range(3):
            try:
                resp = requests.get(
                    url, stream=True, timeout=30,
                    proxies=_get_proxies_safe(),
                    headers={"User-Agent": _APP_UA})
                resp.raise_for_status()
                break
            except requests.exceptions.SSLError:
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
            except requests.exceptions.RequestException:
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)

    with open(save_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return save_path


# ============ 设置壁纸 ============
def set_wallpaper(image_path: str, style: str = "fill"):
    """
    设置桌面壁纸
    :param image_path: 图片绝对路径
    :param style: "center" / "stretch" / "fit" / "fill" / "tile"
    """
    import winreg
    image_path = os.path.abspath(image_path)
    
    # 写入注册表设置显示模式
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Control Panel\Desktop",
        0, winreg.KEY_SET_VALUE
    )
    style_map = {
        "center":  ("0", "0"),   # WallpaperStyle, TileWallpaper
        "stretch": ("2", "0"),
        "fit":     ("6", "0"),
        "fill":    ("10", "0"),
        "tile":    ("0", "1"),
    }
    ws, tw = style_map.get(style, ("10", "0"))
    winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, ws)
    winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, tw)
    winreg.CloseKey(key)
    
    # 调用 SystemParametersInfoW 立即生效
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, image_path,
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    )
    if not ok:
        raise OSError(f"设置壁纸失败，错误码: {ctypes.GetLastError()}")
    return True


# ============ 获取当前壁纸路径 ============
def get_current_wallpaper() -> str:
    """读取当前壁纸路径"""
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETDESKWALLPAPER, 260, buf, 0
    )
    return buf.value


# ============ 删除图片 ============
def delete_cached_image(filename: str) -> bool:
    """从缓存中删除指定壁纸"""
    path = CACHE_DIR / filename
    if path.exists():
        path.unlink()
        return True
    return False


# ============ 清空缓存 ============
def clear_cache():
    """清空所有缓存壁纸"""
    count = 0
    for f in CACHE_DIR.glob("*"):
        if f.is_file():
            f.unlink()
            count += 1
    return count

from PIL import Image

# 缩略图目录
THUMB_DIR = Path.home() / ".WallpaperTool" / "thumbs"
THUMB_DIR.mkdir(parents=True, exist_ok=True)

# 网络缩略图缓存目录（按 URL 哈希命名，保存缩小后的 JPEG，避免反复下载原尺寸图片）
THUMB_CACHE_DIR = Path.home() / ".WallpaperTool" / "thumb_cache"
THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

THUMB_SIZE = (400, 280)   # 缓存缩略图最大尺寸（卡片显示尺寸的 2 倍左右）


def thumbnail_cache_path(key: str) -> Path:
    """缩略图缓存文件路径（同一 URL 始终映射同一文件）"""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return THUMB_CACHE_DIR / f"{digest}.jpg"


def save_thumbnail_bytes(data: bytes, dest: Path, size=THUMB_SIZE) -> bool:
    """把网络图片缩小后写入缓存；失败返回 False（不影响当次显示）"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail(size, Image.LANCZOS)
            img.save(dest, "JPEG", quality=82)
        return True
    except Exception:
        return False


def generate_thumbnail(image_path: str, size=(320, 220)):
    """为本地图片生成缩略图，缓存到 thumbs 目录"""
    image_path = Path(image_path)
    thumb_path = THUMB_DIR / f"{image_path.stem}_thumb.jpg"
    if thumb_path.exists():
        return thumb_path
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail(size, Image.LANCZOS)
            img.save(thumb_path, "JPEG", quality=80)
        return thumb_path
    except Exception:
        return None


def scan_local_folder(folder: str):
    """扫描本地文件夹里的图片，返回与 API 一致的列表格式"""
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    results = []
    folder = Path(folder)
    if not folder.exists():
        return results
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in exts:
            continue
        try:
            with Image.open(f) as img:
                w, h = img.size
        except Exception:
            continue
        results.append({
            "id": f"local_{f.stem}",
            "url": str(f),
            "thumb": str(f),
            "resolution": f"{w}x{h}",
            "purity": "sfw",
            "colors": [],
            "author": "本地图片",
            "source": "local",
        })
    return results


# ============ 动态壁纸（视频） ============
VIDEO_CACHE_DIR = Path.home() / ".WallpaperTool" / "videos"
VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

LIVELY_HELP = (
    "Windows 系统本身不支持视频桌面壁纸，需要借助免费开源的 Lively Wallpaper 播放。\n\n"
    "1. 下载安装 Lively Wallpaper（免费）：\n"
    "   官网  https://rocksdanister.github.io/lively\n"
    "   或 Microsoft Store 搜索 \"Lively Wallpaper\"\n"
    "2. 安装后重新点击「设置动态壁纸」，本工具会自动调用它。\n"
    "3. 若安装的是非默认路径，可在菜单【设置 → Lively 路径设置...】里\n"
    "   手动指定 livelywallpaper.exe 的位置。"
)


class LivelyNotFoundError(RuntimeError):
    """未找到 Lively Wallpaper（动态壁纸播放器）"""

    def __init__(self, msg=LIVELY_HELP):
        super().__init__(msg)


def download_video(url: str, filename: str = None) -> Path:
    """下载视频 mp4 到本地缓存目录（videos），返回文件路径；已存在直接复用"""
    if not filename:
        filename = url.split("/")[-1].split("?")[0]
        if not filename.lower().endswith(".mp4"):
            filename += ".mp4"
    save_path = VIDEO_CACHE_DIR / filename
    if save_path.exists() and save_path.stat().st_size > 0:
        return save_path
    resp = requests.get(url, stream=True, timeout=60,
                        proxies=_get_proxies_safe(),
                        headers={"User-Agent": _APP_UA})
    resp.raise_for_status()
    tmp = save_path.with_suffix(".part")
    with open(tmp, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    tmp.replace(save_path)
    return save_path


def find_lively_install():
    """定位 Lively Wallpaper，返回 (exe路径, 是否商店版, 包FamilyName, 应用AppId)，找不到返回 None

    覆盖两种安装方式：
    - 普通安装版：livelywallpaper.exe（注册表 / 常见目录）
    - 微软商店 MSIX 版：Build\\Lively.exe（Get-AppxPackage 查询，
      包目录受系统保护，必须通过 Invoke-CommandInDesktopPackage 启动）
    """
    import winreg

    # 1. 用户手动指定的路径（菜单【设置 → Lively 路径设置...】）
    try:
        from config import get_lively_path
        p = get_lively_path()
        if p and Path(p).exists():
            return Path(p), False, "", ""
    except ImportError:
        pass

    # 入口 exe 名称随版本不同：1.x/安装版 livelywallpaper.exe，2.x 用户目录安装版 Lively.exe
    candidates = ["livelywallpaper.exe", "livelywallpaper",
                  "Lively Wallpaper.exe", "Lively.exe", "lively.exe"]
    found = []

    def check_registry(root, key_path):
        try:
            with winreg.OpenKey(root, key_path) as k:
                val = winreg.QueryValueEx(k, "")[0]
        except OSError:
            return
        # 命令形如 "C:\...\livelywallpaper.exe" "%1" ，提取引号里的 exe 路径
        val = val.strip().strip('"')
        exe = val.split('"')[0].strip() if '"' in val else val
        if exe.lower().endswith(".exe") and Path(exe).exists():
            found.append(Path(exe))

    # 2. lively:// URI 协议注册的命令 + 3. App Paths（普通安装版）
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        check_registry(root, r"Software\Classes\lively\shell\open\command")
        for name in candidates:
            check_registry(
                root,
                rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}")
    if found:
        return found[0], False, "", ""

    # 4. 常见安装位置（普通安装版）
    import os
    program_files = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("LOCALAPPDATA", "") + r"\Programs"]
    for base in program_files:
        for sub in ("Lively Wallpaper", "LivelyWallpaper", "lively"):
            for name in candidates:
                exe = Path(base) / sub / name
                if exe.exists():
                    return exe, False, "", ""    # 5. 微软商店（MSIX）版：Get-AppxPackage 查询安装位置，
    #    再从 AppxManifest.xml 里读入口 exe 和应用 Id
    try:
        import subprocess, json
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-AppxPackage *lively* | "
             "Select-Object PackageFamilyName,InstallLocation | ConvertTo-Json"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pkg = json.loads(r.stdout) if r.stdout.strip() else None
        # 只装了一个包时 ConvertTo-Json 输出单个对象而非数组
        if isinstance(pkg, list):
            pkg = pkg[0] if pkg else None
        if pkg and pkg.get("InstallLocation"):
            location = Path(pkg["InstallLocation"])
            manifest = location / "AppxManifest.xml"
            if manifest.exists():
                import re
                xml = manifest.read_text(encoding="utf-8", errors="ignore")
                m = re.search(
                    r'<Application[^>]*Id="([^"]+)"[^>]*Executable="([^"]+)"',
                    xml)
                if m:
                    app_id, exe_rel = m.group(1), m.group(2).replace("/", "\\")
                    exe = location / exe_rel
                    if exe.exists():
                        return exe, True, pkg.get("PackageFamilyName", ""), app_id
    except Exception:
        pass

    # 6. 微软商店应用别名（老版本可能注册了别名）
    windows_apps = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps"
    for name in candidates:
        exe = windows_apps / name
        if exe.exists():
            return exe, False, "", ""
    return None


def find_lively_exe():
    """兼容旧接口：只返回主程序路径，找不到返回 None"""
    info = find_lively_install()
    return info[0] if info else None


def _lively_cli_args(exe: Path, video_path: str):
    """按 Lively 版本生成命令行参数

    - 2.x（入口 Lively.exe）：动词式命令 setwp --file <媒体文件路径>
    - 1.x（入口 livelywallpaper.exe）：旧式参数 --wallpaper-url <file URI>
    """
    if exe.name.lower() == "lively.exe":
        return ["setwp", "--file", video_path]
    return ["--wallpaper-url", Path(video_path).as_uri()]


def _lively_rejected_args(before: float, log_marker):
    """启动后检查 Lively 日志，判断参数是否被拒绝（UnknownOptionError 等）

    只检查启动之后新写入的内容：log_marker 是启动前记录的 (最新日志路径, 文件大小)，
    避免旧日志里的错误误判。
    """
    import time as _time
    deadline = _time.time() + 6
    log_dir = Path.home() / "AppData" / "Local" / "Lively Wallpaper" / "logs"
    old_path, old_size = log_marker
    while _time.time() < deadline:
        try:
            files = sorted(log_dir.glob("*.txt"), key=lambda f: f.stat().st_mtime)
            for f in files:
                if f == old_path:
                    # 只读启动后追加的部分
                    if f.stat().st_size <= old_size:
                        continue
                    with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                        fh.seek(old_size)
                        new_text = fh.read()
                elif f.stat().st_mtime >= before:
                    new_text = f.read_text(encoding="utf-8", errors="ignore")
                else:
                    continue
                if "UnknownOptionError" in new_text or \
                        "|ERROR|Lively.Commandline" in new_text:
                    return True
        except OSError:
            pass
        _time.sleep(0.5)
    return False


def _lively_latest_log():
    """返回当前最新 Lively 日志的 (路径, 大小)，不存在返回 (None, 0)"""
    log_dir = Path.home() / "AppData" / "Local" / "Lively Wallpaper" / "logs"
    try:
        files = sorted(log_dir.glob("*.txt"), key=lambda f: f.stat().st_mtime)
        if files:
            latest = files[-1]
            return latest, latest.stat().st_size
    except OSError:
        pass
    return None, 0


def set_video_wallpaper(video_path: str):
    """用 Lively Wallpaper 设置动态壁纸

    - 普通安装版：直接启动 exe 传参
    - 商店（MSIX）版：包目录受系统保护无法直接启动，
      通过 Invoke-CommandInDesktopPackage 在包上下文里带参启动
    启动后会轮询 Lively 日志几秒，若参数被拒绝则报错而不是假成功。
    """
    import time
    import subprocess
    video_path = os.path.abspath(video_path)
    if not Path(video_path).exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")
    info = find_lively_install()
    if not info:
        raise LivelyNotFoundError()
    exe, is_msix, family, app_id = info
    args = _lively_cli_args(exe, video_path)
    before = time.time()
    log_marker = _lively_latest_log()
    if is_msix:
        # PowerShell 的 -Args 按空格切分并处理引号，路径加引号防切分
        cmd = (
            f'Invoke-CommandInDesktopPackage '
            f'-PackageFamilyName "{family}" '
            f'-AppId "{app_id}" '
            f'-Command "{exe}" '
            f'-Args \'' + " ".join(f'"{a}"' for a in args) + "'")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", cmd],
            cwd=str(exe.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        subprocess.Popen([str(exe)] + args,
                         cwd=str(exe.parent),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if _lively_rejected_args(before, log_marker):
        raise RuntimeError(
            "Lively 拒绝了设置命令（命令行参数与已安装版本不兼容）。\n"
            "请在 Lively 主界面手动导入视频：\n" + video_path)
    return True

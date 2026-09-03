"""
壁纸核心引擎：获取分辨率 / 下载图片 / 设置壁纸
"""
import os
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
def download_image(url: str, filename: str = None) -> Path:
    """
    下载图片到本地缓存目录，返回文件路径
    """
    if not filename:
        filename = url.split("/")[-1].split("?")[0]
        if not filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            filename += ".jpg"
    
    save_path = CACHE_DIR / filename
    try:
        from config import get_proxies   # 延迟导入，避免循环依赖
    except ImportError:                  # 缺少 config.py 时降级为直连
        def get_proxies():
            return None
    resp = requests.get(url, stream=True, timeout=30, proxies=get_proxies())
    resp.raise_for_status()
    
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

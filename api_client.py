import re

import requests
from pathlib import Path
from PIL import Image

# 缺少 config.py 时降级为直连，而不是启动崩溃
try:
    from config import get_proxies
except ImportError:
    def get_proxies():
        return None


# ================= Wallhaven =================
class WallhavenSource:
    name = "wallhaven"
    BASE = "https://wallhaven.cc/api/v1/search"

    def search(self, params, page=1, atleast=None, **kw):
        payload = {"categories": "100", "purity": "100", "page": page}
        if atleast:
            payload["atleast"] = atleast
        payload.update({k: v for k, v in params.items() if v})

        resp = requests.get(self.BASE, params=payload, timeout=15,
                            proxies=get_proxies())
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("data", []):
            results.append({
                "id": item["id"],
                "url": item["path"],
                "thumb": item["thumbs"]["small"],
                "resolution": item["resolution"],
                "purity": item["purity"],
                "colors": item.get("colors", []),
                "author": "",
                "source": self.name,
            })
        meta = data.get("meta", {})
        has_more = meta.get("current_page", 1) < meta.get("last_page", 1)
        return results, has_more


# ================= Unsplash =================
class UnsplashSource:
    name = "unsplash"
    BASE = "https://api.unsplash.com/search/photos"

    def __init__(self, api_key="YOUR_UNSPLASH_KEY"):   # ← 申请后填入
        self.api_key = api_key

    def search(self, params, page=1, atleast=None, **kw):
        headers = {"Authorization": f"Client-ID {self.api_key}"}
        payload = {
            "query": params.get("q", "nature"),
            "page": page, "per_page": 30,
            "orientation": "landscape",
            "content_filter": "high",
        }
        resp = requests.get(self.BASE, headers=headers, params=payload,
                            timeout=15, proxies=get_proxies())
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", []):
            w, h = item["width"], item["height"]
            results.append({
                "id": item["id"],
                "url": f"{item['urls']['raw']}&w={w}&h={h}&fit=crop",
                "thumb": item["urls"]["small"],
                "resolution": f"{w}x{h}",
                "purity": "sfw",
                "colors": [item.get("color", "")],
                "author": item["user"]["name"],
                "source": self.name,
            })
        return results, page < data.get("total_pages", 1)


# ================= Bing =================
class BingSource:
    """Bing 每日壁纸，不支持关键词搜索"""
    name = "bing"
    BASE = "https://www.bing.com/HPImageArchive.aspx"

    def search(self, params, page=1, atleast=None, **kw):
        idx = (page - 1) * 8
        payload = {"format": "js", "idx": idx, "n": 8, "mkt": "zh-CN"}
        resp = requests.get(self.BASE, params=payload, timeout=15,
                            proxies=get_proxies())
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("images", []):
            results.append({
                "id": item["hsh"],
                "url": "https://www.bing.com" + item["url"],
                "thumb": "https://www.bing.com" + item["urlbase"] + "_400x240.jpg",
                "resolution": "1920x1080",
                "purity": "sfw",
                "colors": [],
                "author": item.get("copyright", "Bing"),
                "source": self.name,
            })
        return results, page < 10


# ================= 彼岸图网 =================
class NetbianSource:
    """彼岸图网 (netbian.com)，国内直连，按分类浏览（无关键词搜索接口）"""
    name = "netbian"
    BASE = "https://www.netbian.com"
    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def search(self, params, page=1, atleast=None, **kw):
        slug = params.get("cat") or "fengjing"
        url = f"{self.BASE}/{slug}/" if page <= 1 else f"{self.BASE}/{slug}/index_{page}.htm"
        resp = requests.get(url, timeout=15, headers=self.HEADERS)
        resp.raise_for_status()
        resp.encoding = "gbk"

        results = []
        for desk, attrs in re.findall(r'<a href="(/desk/\d+\.htm)"[^>]*>\s*<img([^>]+)>', resp.text):
            src_m = re.search(r'src="([^"]+)"', attrs)
            if not src_m:
                continue
            thumb = src_m.group(1)
            # small111547Cqvkp1792552547.jpg -> 111547Cqvkp.jpg（实测为 1920x1080 原图）
            orig = re.sub(r"small(\w+)\d{10}", r"\1", thumb)
            title_m = re.search(r'alt="([^"]*)"', attrs)
            title = title_m.group(1).strip() if title_m else ""
            results.append({
                "id": f"netbian_{desk.split('/desk/')[1].split('.')[0]}",
                "url": orig,
                "thumb": thumb,
                "resolution": "高清",   # 公开直链实测均为 1920x1080
                "purity": "sfw",
                "colors": [],
                "author": title[:20] if title else "彼岸图网",
                "source": self.name,
            })
        has_more = bool(re.search(rf'/{slug}/index_{page + 1}\.htm', resp.text))
        return results, has_more


# ================= 本地文件夹 =================
class LocalSource:
    name = "local"

    def __init__(self, folder=None):
        self.folder = folder

    def search(self, params, page=1, atleast=None, **kw):
        if not self.folder:
            return [], False
        folder = Path(self.folder)
        exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        files = sorted([f for f in folder.iterdir() if f.suffix.lower() in exts])

        per_page = 30
        start = (page - 1) * per_page
        batch = files[start:start + per_page]
        has_more = start + per_page < len(files)

        results = []
        for f in batch:
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
                "source": self.name,
            })
        return results, has_more


# ================= 源注册表 =================
SOURCE_REGISTRY = {
    "wallhaven": WallhavenSource(),
    "unsplash":  UnsplashSource(),
    "bing":      BingSource(),
    "netbian":   NetbianSource(),
    "local":     LocalSource(),
}


def get_source(name):
    return SOURCE_REGISTRY[name]

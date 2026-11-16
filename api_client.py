import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import requests
from pathlib import Path
from PIL import Image

from wallpaper_engine import netbian_get, NetbianBlockedError, zzzmh_get

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

    def search(self, params, page=1, atleast=None, **kw):
        slug = params.get("cat") or "fengjing"
        url = f"{self.BASE}/{slug}/" if page <= 1 else f"{self.BASE}/{slug}/index_{page}.htm"
        # 统一走 netbian_get：UA/Accept/Referer + 站内 Cookie，遇到 405 会抛明确异常
        resp = netbian_get(url, timeout=15)
        resp.encoding = "gbk"
        if "__noxExpire" in resp.text[:2000] or "安全验证" in resp.text[:2000]:
            raise NetbianBlockedError()

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
        if not results:
            raise RuntimeError(
                "彼岸图网返回的页面里没有解析到图片列表（可能站点改版或被验证页拦截），\n"
                "请稍后重试，或切换到「Bing 每日」等其他图源。")
        return results, has_more


# ================= 极简壁纸 =================
class ZzzmhSource:
    """极简壁纸 (bz.zzzmh.cn)：国内直连、免登录，支持分类浏览与关键词搜索

    列表接口返回卡片缩略图（640x400），原图直链在详情页里；
    实测免登录可下载的「原图·标准」为 1920x1200（2K/4K/8K 为会员 AI 超分，不可用）。
    """
    name = "zzzmh"
    BASE = "https://bz.zzzmh.cn"
    PER_PAGE = 24

    def search(self, params, page=1, atleast=None, **kw):
        offset = (page - 1) * self.PER_PAGE
        q = (params.get("q") or "").strip()
        cat = params.get("cat") or ""
        query = {"offset": offset}
        if q:                        # 关键词搜索（中文）
            query.update({"q": q, "from": "search"})
        elif cat:                    # 分类浏览
            query.update({"type": cat, "sort": "new"})
        else:
            query["sort"] = "new"

        resp = zzzmh_get(f"{self.BASE}/api/cards?" + urlencode(query), timeout=15)
        data = resp.json()
        cards = data.get("cards") or ""

        items = []
        for m in re.finditer(
                r'<a class="card" href="/wallpaper/([0-9a-f]{32})" data-pid="([0-9a-f]{32})">(.*?)</a>',
                cards, re.S):
            pid, block = m.group(1), m.group(3)
            src = re.search(r'(?:data-)?src="([^"]+)"', block)
            if not src:
                continue
            alt = re.search(r'alt="([^"]*)"', block)
            items.append({
                "pid": pid,
                "thumb": src.group(1),
                "author": (alt.group(1).strip() if alt else "")[:20] or "极简壁纸",
            })
        if not items:
            raise RuntimeError(
                "极简壁纸返回的页面里没有解析到图片列表（可能站点改版），\n"
                "请稍后重试，或切换到其他图源。")

        # 详情页里才有原图直链和真实分辨率，并发获取（实测 24 张约 0.4 秒）
        with ThreadPoolExecutor(max_workers=8) as pool:
            details = list(pool.map(self._detail, [it["pid"] for it in items]))

        results = []
        for it, (url, resolution) in zip(items, details):
            if not url:
                continue
            results.append({
                "id": f"zzzmh_{it['pid']}",
                "url": url,
                "thumb": it["thumb"],
                "resolution": resolution,
                "purity": "sfw",
                "colors": [],
                "author": it["author"],
                "source": self.name,
            })
        if not results:
            raise RuntimeError("极简壁纸原图链接解析失败，请稍后重试。")
        has_more = data.get("next", -1) >= 0
        return results, has_more

    @staticmethod
    def _detail(pid):
        """返回 (原图直链, 实际分辨率)；失败返回 (None, "")"""
        try:
            html = zzzmh_get(f"https://bz.zzzmh.cn/wallpaper/{pid}", timeout=10).text
        except Exception:
            return None, ""
        m = re.search(
            r'<img src="(https://imageai\.zzzmh\.cn/wallpaper/desktop/view/large/'
            r'[0-9a-f]{32}\.webp)"[^>]*width="(\d+)" height="(\d+)"', html)
        if m:
            return m.group(1), f"{m.group(2)}x{m.group(3)}"
        m = re.search(
            r'https://imageai\.zzzmh\.cn/wallpaper/desktop/view/large/[0-9a-f]{32}\.webp',
            html)
        if not m:
            return None, ""
        tier = re.search(r'data-tier="(\d+)\s*[×x]\s*(\d+)"', html)
        return m.group(0), (f"{tier.group(1)}x{tier.group(2)}" if tier else "1920x1200")


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
    "bing":      BingSource(),
    "netbian":   NetbianSource(),
    "zzzmh":     ZzzmhSource(),
    "local":     LocalSource(),
}


def get_source(name):
    return SOURCE_REGISTRY[name]

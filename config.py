"""应用配置：保存代理等设置（JSON 存放在 ~/.WallpaperTool/config.json）"""
import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".WallpaperTool" / "config.json"


def _load():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_proxy_url():
    """返回代理地址字符串，未设置时返回空字符串"""
    return str(_load().get("proxy", "") or "").strip()


def set_proxy_url(url):
    """保存代理地址；传空字符串表示直连"""
    cfg = _load()
    cfg["proxy"] = str(url or "").strip()
    _save(cfg)


def get_proxies():
    """返回 requests 需要的 proxies 字典；未设置返回 None（保持默认行为）"""
    url = get_proxy_url()
    if not url:
        return None
    return {"http": url, "https": url}

"""WallpaperTool 主程序入口"""
import sys
import os
import re
import ctypes
import threading
from collections import OrderedDict
from pathlib import Path

# socks5 代理支持（requests 动态导入，显式引用以便 PyInstaller 打包时收录）
try:
    import socks  # noqa: F401
except ImportError:
    pass

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QPushButton, QLineEdit, QComboBox, QLabel, QScrollArea,
    QProgressBar, QMessageBox, QTabWidget, QFrame, QButtonGroup,
    QFileDialog, QAction, QInputDialog, QCheckBox, QDialog, QSizePolicy
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QPixmap, QIcon, QImage, QImageReader

from wallpaper_engine import (
    get_screen_resolution, download_image, set_wallpaper,
    delete_cached_image, clear_cache, generate_thumbnail, fetch_image_bytes,
    THUMB_CACHE_DIR, thumbnail_cache_path, save_thumbnail_bytes,
    download_video, set_video_wallpaper, LivelyNotFoundError
)
from database import HistoryDB, THUMB_DIR
from api_client import SOURCE_REGISTRY, get_source

# 视频播放组件（预览用）；缺失时预览降级为调用系统默认播放器
try:
    from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
    from PyQt5.QtMultimediaWidgets import QVideoWidget
    from PyQt5.QtCore import QUrl
    MULTIMEDIA_AVAILABLE = True
except ImportError:
    MULTIMEDIA_AVAILABLE = False

# PyInstaller 打包后资源在 sys._MEIPASS 解包目录，源码运行时在脚本目录
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys._MEIPASS)
else:
    APP_DIR = Path(__file__).parent
ICON_PATH = APP_DIR / "image" / "app_icon.jpg"

# config.py 提供应用内代理设置；万一本地缺这个文件，降级运行而不是崩溃
try:
    from config import get_proxy_url, set_proxy_url
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False

    def get_proxy_url():
        return ""

    def set_proxy_url(url):
        pass

# ============ 每个图源对应的分类配置 ============
CATEGORY_MAP = {
    "wallhaven": [
        {"key": "landscape", "label": "风景",  "params": {"q": "landscape"}},
        {"key": "featured",  "label": "精选",  "params": {"sorting": "toplist", "topRange": "1M"}},
        {"key": "anime",     "label": "动漫",  "params": {"categories": "010"}},
        {"key": "beauty",    "label": "美女",  "params": {"q": "women", "categories": "001"}},
        {"key": "scifi",     "label": "科幻",  "params": {"q": "sci-fi"}},
        {"key": "game",      "label": "游戏",  "params": {"q": "video game"}},
        {"key": "car",       "label": "汽车",  "params": {"q": "car"}},
        {"key": "sports",    "label": "体育",  "params": {"q": "sports"}},
    ],
    "bing": [
        {"key": "daily", "label": "每日一图", "params": {}},
    ],
    "netbian": [
        {"key": "fengjing", "label": "风景", "params": {"cat": "fengjing"}},
        {"key": "meinv",    "label": "美女", "params": {"cat": "meinv"}},
        {"key": "dongman",  "label": "动漫", "params": {"cat": "dongman"}},
        {"key": "youxi",    "label": "游戏", "params": {"cat": "youxi"}},
        {"key": "qiche",    "label": "汽车", "params": {"cat": "qiche"}},
        {"key": "dongwu",   "label": "动物", "params": {"cat": "dongwu"}},
        {"key": "juzhao",   "label": "剧照", "params": {"cat": "juzhao"}},
        {"key": "weimei",   "label": "唯美", "params": {"cat": "weimei"}},
        {"key": "keai",     "label": "可爱", "params": {"cat": "keai"}},
        {"key": "huahui",   "label": "花卉", "params": {"cat": "huahui"}},
        {"key": "renwu",    "label": "人物", "params": {"cat": "renwu"}},
        {"key": "jianzhu",  "label": "建筑", "params": {"cat": "jianzhu"}},
        {"key": "tiyu",     "label": "体育", "params": {"cat": "tiyu"}},
        {"key": "jieri",    "label": "节日", "params": {"cat": "jieri"}},
        {"key": "meishi",   "label": "美食", "params": {"cat": "meishi"}},
        {"key": "rili",     "label": "日历", "params": {"cat": "rili"}},
    ],
    "zzzmh": [
        {"key": "风景", "label": "风景", "params": {"cat": "风景"}},
        {"key": "人物", "label": "人物", "params": {"cat": "人物"}},
        {"key": "动物", "label": "动物", "params": {"cat": "动物"}},
        {"key": "食物", "label": "食物", "params": {"cat": "食物"}},
        {"key": "华丽", "label": "华丽", "params": {"cat": "华丽"}},
        {"key": "厚涂", "label": "厚涂", "params": {"cat": "厚涂"}},
        {"key": "浓彩", "label": "浓彩", "params": {"cat": "浓彩"}},
        {"key": "水彩", "label": "水彩", "params": {"cat": "水彩"}},
        {"key": "景观", "label": "景观", "params": {"cat": "景观"}},
    ],
    "local": [],   # 本地源无分类
    "video_pexels": [   # 动态壁纸：Pexels 精选（免 key）
        {"key": "curated", "label": "精选", "params": {}},
    ],
    "video_pixabay": [  # 动态壁纸：Pixabay 视频（需免费 key）
        {"key": "nature",  "label": "自然",  "params": {"q": "nature"}},
        {"key": "ocean",   "label": "海洋",  "params": {"q": "ocean"}},
        {"key": "city",    "label": "城市",  "params": {"q": "city"}},
        {"key": "space",   "label": "太空",  "params": {"q": "space"}},
        {"key": "animal",  "label": "动物",  "params": {"q": "animals"}},
        {"key": "flower",  "label": "花卉",  "params": {"q": "flowers"}},
        {"key": "abstract","label": "抽象",  "params": {"q": "abstract"}},
        {"key": "night",   "label": "夜景",  "params": {"q": "night city"}},
    ],
}

# 极简壁纸国内直连、免登录且支持关键词搜索，作为默认图源（下拉框第一项 = 默认选中）
SOURCE_LABELS = {
    "zzzmh":     "极简壁纸",
    "netbian":   "彼岸图网",
    "bing":      "Bing 每日",
    "wallhaven": "Wallhaven",
    "local":     "本地文件夹",
    "video_pexels":  "动态壁纸·精选（免Key）",
    "video_pixabay": "动态壁纸·Pixabay（需Key）",
}

VIDEO_SOURCES = ("video_pexels", "video_pixabay")

# 这些图源国内无法直连：切换过去时不自动加载，先给出代理提示 + 开关
PROXY_REQUIRED_SOURCES = {"wallhaven"}


# ================= 线程组件 =================
# 缩略图并发上限：一页可能有几十张图，全部同时下载会互相抢占带宽、拖慢首屏
_THUMB_SEMAPHORE = threading.Semaphore(4)

# 未命中内存缓存时才解码/下载；只保留最近用过的若干张（QImage 本身很小）
_THUMB_MEM = OrderedDict()
_THUMB_MEM_MAX = 120


def thumb_mem_get(url):
    image = _THUMB_MEM.get(url)
    if image is not None:
        _THUMB_MEM.move_to_end(url)
    return image


def thumb_mem_put(url, image):
    _THUMB_MEM[url] = image
    _THUMB_MEM.move_to_end(url)
    while len(_THUMB_MEM) > _THUMB_MEM_MAX:
        _THUMB_MEM.popitem(last=False)


class ThumbLoader(QThread):
    """异步加载缩略图（网络图先查磁盘缓存；本地图按显示尺寸解码，不读整张原图）

    线程内只做下载 + QImage 解码（QPixmap 只能在 GUI 线程创建），
    结果通过信号交回主线程转成 QPixmap。
    """
    loaded = pyqtSignal(QImage)
    failed = pyqtSignal()

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            image = self._load()
            if image is None or image.isNull():
                self.failed.emit()
            else:
                self.loaded.emit(image)
        except Exception:
            self.failed.emit()

    def _load(self):
        if not self.url.startswith("http"):
            return self._load_local()
        cache = thumbnail_cache_path(self.url)
        if cache.exists():
            image = QImage(str(cache))
            if not image.isNull():
                return image
            try:
                cache.unlink()   # 缓存损坏则删除后重新下载
            except OSError:
                pass
        # 限制同时下载的数量，其余请求自动排队
        with _THUMB_SEMAPHORE:
            data = fetch_image_bytes(self.url, timeout=10)
        save_thumbnail_bytes(data, cache)   # 缩小后落盘，下次直接读缓存
        image = QImage.fromData(data)
        if image.isNull():
            return image
        return image.scaled(400, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _load_local(self):
        """本地图片用 QImageReader 按目标尺寸解码，避免把 4K 原图整张读入"""
        reader = QImageReader(self.url)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(400, 280, Qt.KeepAspectRatio))
        return reader.read()


class SearchThread(QThread):
    """多图源搜索线程"""
    finished = pyqtSignal(list, bool)   # results, has_more
    error = pyqtSignal(str)

    def __init__(self, source, params, page, atleast):
        super().__init__()
        self.source = source
        self.params = params
        self.page = page
        self.atleast = atleast

    def run(self):
        try:
            results, has_more = self.source.search(
                self.params, page=self.page, atleast=self.atleast)
            self.finished.emit(results, has_more)
        except Exception as e:
            self.error.emit(str(e))


class FavoriteThread(QThread):
    """后台下载收藏图片并生成缩略图，避免点击收藏时界面卡顿"""
    done = pyqtSignal(str, str)      # 图片路径, 缩略图路径
    failed = pyqtSignal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            if self.url.startswith("http"):
                path = download_image(self.url)
            else:
                path = Path(self.url)
            thumb = generate_thumbnail(str(path))
            self.done.emit(str(path), str(thumb) if thumb else "")
        except Exception as e:
            self.failed.emit(str(e))


class ApplyThread(QThread):
    """后台下载并设置壁纸：网络请求、注册表写入和系统调用都不在 GUI 线程执行"""
    done = pyqtSignal(str, str)      # 图片路径, 缩略图路径
    failed = pyqtSignal(str)

    def __init__(self, info, style):
        super().__init__()
        self.info = info
        self.style = style

    def run(self):
        try:
            url = self.info["url"]
            if url.startswith("http"):
                path = download_image(url)
            else:
                path = Path(url)   # 本地源：直接用文件路径
            set_wallpaper(str(path), style=self.style)
            thumb = generate_thumbnail(str(path))
            self.done.emit(str(path), str(thumb) if thumb else "")
        except Exception as e:
            self.failed.emit(str(e))


class VideoApplyThread(QThread):
    """后台下载视频并调用 Lively Wallpaper 设置动态壁纸

    顺带下载封面图生成缩略图，历史记录里能显示视频封面。
    """
    done = pyqtSignal(str, str)      # 视频路径, 封面缩略图路径
    failed = pyqtSignal(str)

    def __init__(self, info):
        super().__init__()
        self.info = info

    def run(self):
        try:
            url = self.info["url"]
            if url.startswith("http"):
                path = download_video(url)
            else:
                path = Path(url)
            set_video_wallpaper(str(path))

            # 用封面图生成缩略图（mp4 本身无法用 PIL 生成）
            thumb = ""
            thumb_url = self.info.get("thumb", "")
            if thumb_url.startswith("http"):
                try:
                    data = fetch_image_bytes(thumb_url, timeout=10)
                    dest = THUMB_DIR / f"{path.stem}_thumb.jpg"
                    if save_thumbnail_bytes(data, dest):
                        thumb = str(dest)
                except Exception:
                    pass
            self.done.emit(str(path), thumb)
        except Exception as e:
            self.failed.emit(str(e))


class DownloadPlayThread(QThread):
    """下载视频（走缓存）后用系统默认播放器打开——预览内嵌播放失败时的兜底"""
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, info):
        super().__init__()
        self.info = info

    def run(self):
        try:
            url = self.info["url"]
            path = download_video(url) if url.startswith("http") else Path(url)
            self.done.emit(str(path))
        except Exception as e:
            self.failed.emit(str(e))


class VideoSaveThread(QThread):
    """把视频另存到用户选择的文件夹（下载走缓存 + 复制，避免重复下载）"""
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, info, dest):
        super().__init__()
        self.info = info
        self.dest = Path(dest)

    def run(self):
        try:
            url = self.info["url"]
            path = download_video(url) if url.startswith("http") else Path(url)
            if path.resolve() != self.dest.resolve():
                import shutil
                shutil.copyfile(path, self.dest)
            self.done.emit(str(self.dest))
        except Exception as e:
            self.failed.emit(str(e))


# ================= 壁纸卡片（搜索结果） =================
class WallpaperCard(QFrame):
    def __init__(self, wallpaper_info, parent=None):
        super().__init__(parent)
        self.info = wallpaper_info
        self.is_fav = False
        self.is_video = wallpaper_info.get("kind") == "video"
        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        self.setLineWidth(1)
        self.setFixedSize(200, 220)
        self.setObjectName("wallCard")
        self.setToolTip("双击或点 👁 播放预览" if self.is_video
                        else "双击或点 👁 预览大图")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(190, 130)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("background:rgba(255,255,255,0.75); border-radius:6px;")
        if self.is_video:
            self.thumb_label.setText("🎬 加载封面…")
        layout.addWidget(self.thumb_label)

        info_label = QLabel(f"ⓘ {self.info['resolution']} · {self.info.get('author', '')}")
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_apply = QPushButton("设动态" if self.is_video else "设置")
        self.btn_apply.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #5b8cff, stop:1 #4a6cf7); border:none; border-radius:6px;"
            "color:white; font-size:12px; padding:4px 0;}"
            "QPushButton:hover{background:#3f5fe8;}")
        self.btn_apply.clicked.connect(self.on_apply)
        self.btn_preview = QPushButton("👁")
        self.btn_preview.setFixedSize(30, 28)
        self.btn_preview.setStyleSheet(
            "QPushButton{background:white; border:1px solid #dfe6f2;"
            "border-radius:6px; font-size:13px; padding:0;}"
            "QPushButton:hover{border-color:#4a6cf7;}")
        self.btn_preview.clicked.connect(self.open_preview)
        self.btn_fav = QPushButton("🤍")
        self.btn_fav.setFixedSize(30, 28)
        self.btn_fav.setStyleSheet(
            "QPushButton{background:white; border:1px solid #dfe6f2;"
            "border-radius:6px; font-size:13px; padding:0;}"
            "QPushButton:hover{border-color:#4a6cf7;}")
        if self.is_video:
            self.btn_fav.setText("💾")
            self.btn_fav.setToolTip("保存 MP4 到本地")
        self.btn_fav.clicked.connect(self.on_toggle_fav)
        btn_row.addWidget(self.btn_apply, 1)
        btn_row.addWidget(self.btn_preview)
        btn_row.addWidget(self.btn_fav)
        layout.addLayout(btn_row)

        self.load_thumb()
        # self._check_fav_state()

    def mouseDoubleClickEvent(self, event):
        self.open_preview()

    def open_preview(self):
        if self.is_video:
            VideoPreviewDialog(self.window(), self.info).exec()
        else:
            PreviewDialog(self.window(), self.info).exec()

    def load_thumb(self):
        url = self.info.get("thumb") or self.info.get("url", "")
        if not url:
            self._on_thumb_failed()
            return
        image = thumb_mem_get(url)
        if image is not None:
            self.set_image(image)
            return
        self._thumb_key = url
        self._thumb_thread = ThumbLoader(url)
        self._thumb_thread.loaded.connect(self._on_image_loaded)
        self._thumb_thread.failed.connect(self._on_thumb_failed)
        self._thumb_thread.start()

    def _on_image_loaded(self, image):
        thumb_mem_put(self._thumb_key, image)
        self.set_image(image)

    def set_image(self, image):
        self.set_pixmap(QPixmap.fromImage(image))

    def _on_thumb_failed(self):
        self.thumb_label.setText("🎬 封面加载失败" if self.is_video else "图片加载失败")
        self.thumb_label.setStyleSheet(
            "background:#eee; color:#999; font-size:12px;")

    def set_pixmap(self, pixmap):
        if pixmap and not pixmap.isNull():
            self.thumb_label.setPixmap(pixmap.scaled(
                self.thumb_label.size(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _check_fav_state(self):
        main = self.window()
        db = getattr(main, "db", None)
        if db and db.is_favorite(self.info["url"]):
            self._set_fav(True)


    def _set_fav(self, fav):
        self.is_fav = fav
        self.btn_fav.setText("❤️" if fav else "🤍")

    def on_toggle_fav(self):
        main = self.window()
        info = self.info
        if self.is_video:
            self.save_video()
            return
        if self.is_fav:
            main.db.remove_favorite(info["url"])
            self._set_fav(False)
            main.refresh_favorites()   # 同步「我的喜欢」列表
            return
        if getattr(self, "_fav_thread", None) is not None and self._fav_thread.isRunning():
            return
        # 下载原图 + 生成缩略图都放到后台线程，按钮先置灰防重复点击
        self.btn_fav.setEnabled(False)
        self.btn_fav.setText("…")
        self._fav_thread = FavoriteThread(info["url"])
        self._fav_thread.done.connect(self._on_fav_done)
        self._fav_thread.failed.connect(self._on_fav_failed)
        self._fav_thread.start()

    def _on_fav_done(self, path, thumb):
        main = self.window()
        info = self.info
        self.btn_fav.setEnabled(True)
        try:
            main.db.add_favorite(
                file_path=path,
                file_name=os.path.basename(path)[:80],
                source_url=info["url"],
                thumb_path=thumb,
                resolution=info["resolution"],
                source=info.get("source", "wallhaven")
            )
            self._set_fav(True)
            main.refresh_favorites()
            main.statusBar().showMessage("已加入收藏", 2000)
        except Exception as e:
            self._set_fav(False)
            QMessageBox.warning(self, "收藏失败", str(e))

    def _on_fav_failed(self, msg):
        self.btn_fav.setEnabled(True)
        self._set_fav(False)
        QMessageBox.warning(self, "收藏失败", msg)

    def save_video(self):
        """视频卡片 💾：把 mp4 另存到用户选择的文件夹"""
        if getattr(self, "_save_thread", None) is not None and self._save_thread.isRunning():
            return
        url = self.info["url"]
        default_name = url.split("/")[-1].split("?")[0] or "wallpaper.mp4"
        dest, _ = QFileDialog.getSaveFileName(
            self, "保存视频", default_name, "MP4 视频 (*.mp4)")
        if not dest:
            return
        self.btn_fav.setEnabled(False)
        self.btn_fav.setText("…")
        self._save_thread = VideoSaveThread(self.info, dest)
        self._save_thread.done.connect(self._on_save_done)
        self._save_thread.failed.connect(self._on_save_failed)
        self._save_thread.start()

    def _on_save_done(self, dest):
        self.btn_fav.setEnabled(True)
        self.btn_fav.setText("💾")
        QMessageBox.information(self, "已保存", f"视频已保存到：\n{dest}")

    def _on_save_failed(self, msg):
        self.btn_fav.setEnabled(True)
        self.btn_fav.setText("💾")
        QMessageBox.warning(self, "保存失败", msg)

    def on_apply(self):
        main = self.window()
        main.apply_wallpaper(self.info)


# ================= 历史记录卡片 =================
class HistoryCard(QFrame):
    def __init__(self, record, parent=None):
        super().__init__(parent)
        (self.rid, self.ts, self.fname, self.fpath,
         self.surl, self.tags, self.res, self.style,
         self.source, self.tpath) = record

        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        self.setLineWidth(1)
        self.setFixedSize(180, 200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(170, 120)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("background:#222; border-radius:4px;")
        layout.addWidget(self.thumb_label)

        ts_short = self.ts[:16].replace("T", " ")
        info = QLabel(f"{ts_short}\n{self.res} · {self.style}")
        info.setAlignment(Qt.AlignCenter)
        info.setStyleSheet("font-size:11px; color:#666;")
        layout.addWidget(info)

        self._build_btn_row(layout)

        self._load_thumb()

    def _build_btn_row(self, layout):
        """历史卡片按钮行：应用 + 删除（无收藏爱心）"""
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_apply = QPushButton("应用")
        btn_apply.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #5b8cff, stop:1 #4a6cf7); border:none; border-radius:6px;"
            "color:white; font-size:12px; padding:4px 0;}"
            "QPushButton:hover{background:#3f5fe8;}")
        btn_apply.clicked.connect(self.on_apply)
        btn_del = QPushButton("🗑")
        btn_del.setFixedSize(30, 28)
        btn_del.setStyleSheet(
            "QPushButton{background:white; border:1px solid #dfe6f2;"
            "border-radius:6px; font-size:12px; padding:0;}"
            "QPushButton:hover{border-color:#e05555;}")
        btn_del.clicked.connect(self.on_delete)
        btn_row.addWidget(btn_apply, 1)
        btn_row.addWidget(btn_del)
        layout.addLayout(btn_row)

    def on_delete(self):
        main = self.window()
        if QMessageBox.question(
                self, "确认",
                f"删除这条历史记录？\n{self.fname}") == QMessageBox.Yes:
            main.db.delete_record(self.rid)
            main.refresh_history()

    def _load_thumb(self):
        pixmap = QPixmap()
        if self.tpath and Path(self.tpath).exists():
            pixmap.load(self.tpath)
        if not pixmap.isNull():
            self._set_pixmap(pixmap)
            return
        # 缩略图缺失时：本地原图或网络图都放到后台线程按显示尺寸解码，
        # 避免生成缩略图时在界面线程解码大图造成卡顿
        key = ""
        if self.fpath and Path(self.fpath).exists():
            key = self.fpath
        elif self.surl and self.surl.startswith("http"):
            key = self.surl
        if not key:
            self._on_thumb_failed()
            return
        image = thumb_mem_get(key)
        if image is not None:
            self._set_image(image)
            return
        self._thumb_key = key
        self._thread = ThumbLoader(key)
        self._thread.loaded.connect(self._on_image_loaded)
        self._thread.failed.connect(self._on_thumb_failed)
        self._thread.start()

    def _on_image_loaded(self, image):
        thumb_mem_put(self._thumb_key, image)
        self._set_image(image)

    def _set_image(self, image):
        self._set_pixmap(QPixmap.fromImage(image))

    def _on_thumb_failed(self):
        self.thumb_label.setText("图片加载失败")
        self.thumb_label.setStyleSheet(
            "background:#222; border-radius:4px; color:#999; font-size:12px;")

    def _set_pixmap(self, pixmap):
        if not pixmap.isNull():
            self.thumb_label.setPixmap(pixmap.scaled(
                self.thumb_label.size(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def on_apply(self):
        main = self.window()
        if self.fpath and Path(self.fpath).exists():
            url = self.fpath
        elif self.surl and self.surl.startswith("http"):
            url = self.surl   # 缓存被清理过：用原始链接重新下载
        else:
            QMessageBox.warning(self, "失败", "图片文件已不存在，且没有可用的下载链接")
            return
        # 统一走主窗口的后台设置流程：不卡界面，带进度与失败提示
        main.apply_wallpaper({
            "url": url,
            "resolution": self.res or "",
            "source": self.source or "",
            "tags": self.tags or "",
        })


# ================= 收藏卡片（复用历史卡片逻辑） =================
class FavoriteCard(HistoryCard):
    def __init__(self, fav_record, parent=None):
        # favorites 表字段: (id, file_path, file_name, source_url,
        #                    thumb_path, resolution, source, created_at)
        # 转换为 HistoryCard 需要的 history 元组顺序
        (fid, fpath, fname, surl, tpath, res, source, created_at) = fav_record
        fake_history = (fid, created_at, fname, fpath, surl,
                        "", res, "fill", source, tpath)
        super().__init__(fake_history, parent)

    def _build_btn_row(self, layout):
        """收藏卡片按钮行：应用 + ❤️ 取消收藏（无删除垃圾桶）"""
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_apply = QPushButton("应用")
        btn_apply.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #5b8cff, stop:1 #4a6cf7); border:none; border-radius:6px;"
            "color:white; font-size:12px; padding:4px 0;}"
            "QPushButton:hover{background:#3f5fe8;}")
        btn_apply.clicked.connect(self.on_apply)
        self.btn_fav = QPushButton("❤️")
        self.btn_fav.setFixedSize(30, 28)
        self.btn_fav.setStyleSheet(
            "QPushButton{background:white; border:1px solid #dfe6f2;"
            "border-radius:6px; font-size:12px; padding:0;}"
            "QPushButton:hover{border-color:#e05555;}")
        self.btn_fav.setToolTip("点击取消喜欢")
        self.btn_fav.clicked.connect(self.on_fav)
        btn_row.addWidget(btn_apply, 1)
        btn_row.addWidget(self.btn_fav)
        layout.addLayout(btn_row)

    def on_fav(self):
        # 收藏页点 ❤️ = 取消收藏并从列表移除（按主键删除，避免 source_url 为空时删不掉）
        main = self.window()
        main.db.delete_favorite_by_id(self.rid)
        main.refresh_favorites()
        main._sync_fav_icons()



# ================= 大图预览 =================
class FullImageLoader(QThread):
    """加载原图全尺寸（复用 fetch_image_bytes 的各图源下载逻辑）"""

    loaded = pyqtSignal(QImage)
    failed = pyqtSignal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            if self.url.startswith("http"):
                data = fetch_image_bytes(self.url, timeout=30)
                image = QImage.fromData(data)
            else:   # 本地源：全尺寸解码
                reader = QImageReader(self.url)
                reader.setAutoTransform(True)
                image = reader.read()
            if image.isNull():
                self.failed.emit("图片解码失败")
            else:
                self.loaded.emit(image)
        except Exception as e:
            self.failed.emit(str(e)[:120])


class PreviewDialog(QDialog):
    """大图预览：双击卡片或点 👁 打开，加载原图并可直接设为壁纸"""

    def __init__(self, parent, info):
        super().__init__(parent)
        self.info = info
        self._thread = None
        self.setWindowTitle("预览")
        self.setMinimumSize(780, 580)
        self.resize(900, 660)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        self.image_label = QLabel("⏳ 正在加载原图…")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(750, 440)
        self.image_label.setWordWrap(True)
        lay.addWidget(self.image_label, 1)

        bottom = QHBoxLayout()
        src_label = SOURCE_LABELS.get(info.get("source", ""), info.get("source", ""))
        meta = f"ⓘ {info.get('resolution', '')} · {src_label}"
        if info.get("author"):
            meta += f" · {info['author']}"
        bottom.addWidget(QLabel(meta))
        bottom.addStretch()
        self.btn_apply = QPushButton("🖼 设为壁纸")
        self.btn_apply.setObjectName("accentBtn")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        bottom.addWidget(self.btn_apply)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        bottom.addWidget(btn_close)
        lay.addLayout(bottom)

        self._load(info.get("url", ""))

    def _load(self, url):
        if not url:
            self.image_label.setText("无可用图片")
            return
        self._thread = FullImageLoader(url)
        self._thread.loaded.connect(self._show)
        self._thread.failed.connect(self._show_failed)
        self._thread.start()

    def _show(self, image):
        self._full_image = image
        self.btn_apply.setEnabled(True)
        self.image_label.setPixmap(QPixmap.fromImage(image).scaled(
            self.image_label.size(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _show_failed(self, msg):
        self.image_label.setText(f"原图加载失败：{msg}")

    def _apply(self):
        main = self.window()
        main.apply_wallpaper(self.info)
        self.accept()   # 设置成功后关闭预览

    def resizeEvent(self, event):
        # 窗口拉伸时按当前显示的图片重新适配
        pix = self.image_label.pixmap()
        if pix and not pix.isNull():
            data = getattr(self, "_full_image", None)
            if data is not None:
                self.image_label.setPixmap(QPixmap.fromImage(data).scaled(
                    self.image_label.size(),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
        super().resizeEvent(event)


# ================= 视频预览（动态壁纸） =================
class VideoPreviewDialog(QDialog):
    """视频预览：内嵌播放器边看边决定；也可直接设为动态壁纸"""

    def __init__(self, parent, info):
        super().__init__(parent)
        self.info = info
        self._main = parent   # 对话框自己是顶层窗口，不能用 self.window() 找主窗
        self._player = None
        self._play_thread = None
        self.setWindowTitle("动态壁纸预览")
        self.setMinimumSize(780, 560)
        self.resize(900, 640)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        self.player_label = None
        self._video_widget = None
        if MULTIMEDIA_AVAILABLE:
            self._video_widget = QVideoWidget()
            self._video_widget.setStyleSheet("background:#111; border-radius:8px;")
            lay.addWidget(self._video_widget, 1)
        else:
            self.player_label = QLabel(
                "本机缺少 Qt 多媒体组件，无法内嵌播放。\n"
                "可直接点「设为动态壁纸」，或关闭后点卡片 💾 保存 MP4 用本地播放器观看。")
            self.player_label.setAlignment(Qt.AlignCenter)
            self.player_label.setWordWrap(True)
            self.player_label.setMinimumSize(750, 420)
            lay.addWidget(self.player_label, 1)

        bottom = QHBoxLayout()
        src_label = SOURCE_LABELS.get(info.get("source", ""), info.get("source", ""))
        meta = f"ⓘ {info.get('resolution', '')} · {src_label} · {info.get('author', '')}"
        bottom.addWidget(QLabel(meta))
        bottom.addStretch()
        self.btn_apply = QPushButton("🎬 设为动态壁纸")
        self.btn_apply.setObjectName("accentBtn")
        self.btn_apply.clicked.connect(self._apply)
        bottom.addWidget(self.btn_apply)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        bottom.addWidget(btn_close)
        lay.addLayout(bottom)

        if MULTIMEDIA_AVAILABLE:
            self._start_player()

    def _start_player(self):
        url = self.info.get("url", "")
        if not url:
            self._video_widget.hide()
            self.player_label = QLabel("无可用视频链接")
            self.player_label.setAlignment(Qt.AlignCenter)
            self.layout().insertWidget(0, self.player_label, 1)
            return
        self._player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        self._player.setMedia(QMediaContent(QUrl(url)))
        self._player.setVideoOutput(self._video_widget)
        self._player.error.connect(self._player_error)
        self._player.play()

    def _player_error(self, error):
        """内嵌播放失败（常见于缺 h264 解码器 0x80040218）：提供系统播放器兜底"""
        if self._video_widget is not None:
            self._video_widget.hide()
        fallback = QVBoxLayout()
        self.player_label = QLabel(
            "内嵌播放失败（系统缺少视频解码器）。\n"
            "不影响设置动态壁纸，可直接点下方「设为动态壁纸」；"
            "也可以先用系统播放器看看效果：")
        self.player_label.setAlignment(Qt.AlignCenter)
        self.player_label.setWordWrap(True)
        self.btn_open_sys = QPushButton("▶ 用系统播放器打开")
        self.btn_open_sys.setObjectName("accentBtn")
        self.btn_open_sys.clicked.connect(self._open_with_system_player)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.btn_open_sys)
        row.addStretch()
        fallback.addWidget(self.player_label, 1)
        fallback.addLayout(row)
        wrap = QWidget()
        wrap.setLayout(fallback)
        self.layout().insertWidget(0, wrap)

    def _open_with_system_player(self):
        if self._play_thread is not None and self._play_thread.isRunning():
            return
        self.btn_open_sys.setEnabled(False)
        self.btn_open_sys.setText("⏳ 正在下载…")
        self._play_thread = DownloadPlayThread(self.info)
        self._play_thread.done.connect(self._on_play_thread_done)
        self._play_thread.failed.connect(self._on_play_thread_failed)
        self._play_thread.start()

    def _on_play_thread_done(self, path):
        self.btn_open_sys.setEnabled(True)
        self.btn_open_sys.setText("▶ 用系统播放器打开")
        try:
            os.startfile(path)   # 调用系统默认播放器
        except OSError as e:
            QMessageBox.warning(self, "打开失败", f"无法调用系统播放器：\n{e}")

    def _on_play_thread_failed(self, msg):
        self.btn_open_sys.setEnabled(True)
        self.btn_open_sys.setText("▶ 用系统播放器打开")
        QMessageBox.warning(self, "下载失败", msg)

    def _apply(self):
        self._main.apply_wallpaper(self.info)
        self.close()

    def closeEvent(self, event):
        if self._player is not None:
            self._player.stop()
        event.accept()


# ================= 全局主题样式 =================
GLOBAL_QSS = """
/* 背景容器全部透明，露出背景图 */
QTabWidget::pane { border: none; background: transparent; }
QTabBar { background: transparent; }
QTabBar::tab {
    background: rgba(255,255,255,0.55); color: #556;
    padding: 10px 24px; margin-right: 4px;
    border-radius: 8px; font-size: 13px; min-width: 72px;
}
QTabBar::tab:selected { background: white; color: #4a6cf7; font-weight: bold; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QMenuBar { background: transparent; }
QMenu { background: white; border: 1px solid #e3e8f0; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 6px 24px; border-radius: 6px; }
QMenu::item:selected { background: #4a6cf7; color: white; }

/* 输入控件 */
QLineEdit, QComboBox {
    background: rgba(255,255,255,0.85); border: 1px solid #dfe6f2;
    border-radius: 8px; padding: 6px 10px; font-size: 13px; color: #333;
}
QLineEdit:focus, QComboBox:focus { border-color: #4a6cf7; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: white; border: 1px solid #e3e8f0;
    selection-background-color: #4a6cf7; selection-color: white;
}

/* 按钮 */
QPushButton {
    background: rgba(255,255,255,0.9); border: 1px solid #dfe6f2;
    border-radius: 8px; padding: 6px 16px; font-size: 13px; color: #333;
}
QPushButton:hover { border-color: #4a6cf7; color: #4a6cf7; }
QPushButton:pressed { background: #eef2ff; }
QPushButton#accentBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #5b8cff, stop:1 #4a6cf7);
    border: none; color: white; font-weight: bold; padding: 8px 22px;
}
QPushButton#accentBtn:hover { background: #3f5fe8; }
QPushButton#accentBtn:pressed { background: #3550cf; }
QPushButton#accentBtn:disabled { background: #b8c6e8; }

/* 壁纸卡片 */
QFrame#wallCard {
    background: rgba(255,255,255,0.82);
    border: 1px solid rgba(227,232,240,0.9);
    border-radius: 10px;
}
QFrame#wallCard:hover {
    background: white; border-color: #7ea0ff;
}

/* 滚动条 */
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: rgba(120,135,180,0.5); border-radius: 4px; min-height: 40px; }
QScrollBar::handle:vertical:hover { background: rgba(90,110,180,0.8); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }
QScrollBar::handle:horizontal { background: rgba(120,135,180,0.5); border-radius: 4px; min-width: 40px; }
QScrollBar::handle:horizontal:hover { background: rgba(90,110,180,0.8); }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* 进度条 */
QProgressBar {
    background: rgba(255,255,255,0.7); border: 1px solid #dfe6f2;
    border-radius: 6px; height: 12px; text-align: center; color: #667;
}
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 #5b8cff, stop:1 #4a6cf7); border-radius: 5px; }

/* 预览对话框 */
QLabel#previewImage { background: transparent; }
QLabel { background: transparent; font-size: 13px; }
"""


# ================= 主窗口 =================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = HistoryDB()
        self.resolution = get_screen_resolution()
        self.current_results = []
        self._seen_ids = set()

        # 默认图源：极简壁纸（国内直连、免登录、支持关键词搜索）
        # 彼岸图网仍保留在下拉中，但它有 WAF 拦截风险，不作为启动默认源
        self.current_source_name = "zzzmh"

        # 本次会话中用户是否已确认过代理可用（确认过之后切图源不再拦截）
        self._proxy_ready = False

        # 无限滚动状态
        self.current_page = 1
        self.has_more = True
        self.is_loading = False
        self.current_search_params = None   # None=尚未搜索过
        self.current_category = None        # 由 _fill_category_bar 设置

        self.init_ui()

    # ---------- UI 构建 ----------
    def init_ui(self):
        self.setWindowTitle("桌面壁纸助手")
        self.setGeometry(100, 100, 1100, 700)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.setStyleSheet(GLOBAL_QSS)
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        # 背景图：border-image 自动拉伸铺满窗口，无需处理 resize
        bg_path = APP_DIR / "image" / "app_bg.jpg"
        if bg_path.exists():
            central.setStyleSheet(
                f"QWidget#centralWidget{{ border-image: url({bg_path.as_posix()}) 0; }}")
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 6, 10, 8)

        self._build_menu()

        info_bar = QLabel(f"🖥 本机分辨率: {self.resolution[0]} × {self.resolution[1]}")
        info_bar.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #3d4a6b; padding: 6px 2px;")
        main_layout.addWidget(info_bar)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # -- Tab 1: 搜索 --
        search_tab = QWidget()
        search_layout = QVBoxLayout(search_tab)

        search_layout.addLayout(self._build_source_bar())
        search_layout.addWidget(self._build_category_bar())

        # 搜索栏
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入英文关键词，如 landscape / game ...")
        self.search_input.returnPressed.connect(self.do_search)
        search_row.addWidget(self.search_input, 1)

        self.style_combo = QComboBox()
        self.style_combo.addItems([
            "fill (填充)", "fit (适应)", "stretch (拉伸)",
            "center (居中)", "tile (平铺)"
        ])
        search_row.addWidget(self.style_combo)

        self.btn_search = QPushButton("🔍 搜索")
        self.btn_search.clicked.connect(self.do_search)
        search_row.addWidget(self.btn_search)
        search_layout.addLayout(search_row)

        # 按默认图源同步控件初始状态：搜索框禁用/启用的判定、填充分类栏
        self._sync_source_controls(self.current_source_name)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        search_layout.addWidget(self.progress)

        # 结果滚动区 + 网格
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.verticalScrollBar().valueChanged.connect(self.on_scroll)
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(10)
        self.scroll.setWidget(self.grid_container)
        search_layout.addWidget(self.scroll, 1)

        self.tabs.addTab(search_tab, "搜索壁纸")

        # -- Tab 2: 我的喜欢 --
        fav_tab = QWidget()
        fav_layout = QVBoxLayout(fav_tab)
        fav_btn_row = QHBoxLayout()
        btn_refresh_fav = QPushButton("🔄 刷新")
        btn_refresh_fav.clicked.connect(self.refresh_favorites)
        fav_btn_row.addWidget(btn_refresh_fav)
        fav_btn_row.addStretch()
        fav_layout.addLayout(fav_btn_row)

        scroll_fav = QScrollArea()
        scroll_fav.setWidgetResizable(True)
        self.fav_grid_container = QWidget()
        self.fav_grid = QGridLayout(self.fav_grid_container)
        self.fav_grid.setSpacing(10)
        scroll_fav.setWidget(self.fav_grid_container)
        fav_layout.addWidget(scroll_fav)
        self.tabs.addTab(fav_tab, "我的喜欢")

        # -- Tab 3: 历史 --
        history_tab = QWidget()
        h_layout = QVBoxLayout(history_tab)
        h_btn_row = QHBoxLayout()
        btn_clear_hist = QPushButton("🗑️ 清空历史")
        btn_clear_hist.clicked.connect(self.clear_history)
        btn_clear_cache = QPushButton("🧹 清理缓存")
        btn_clear_cache.clicked.connect(self.clear_all_cache)
        h_btn_row.addWidget(btn_clear_hist)
        h_btn_row.addWidget(btn_clear_cache)
        h_btn_row.addStretch()
        h_layout.addLayout(h_btn_row)

        scroll_hist = QScrollArea()
        scroll_hist.setWidgetResizable(True)
        self.hist_grid_container = QWidget()
        self.hist_grid = QGridLayout(self.hist_grid_container)
        self.hist_grid.setSpacing(10)
        scroll_hist.setWidget(self.hist_grid_container)
        h_layout.addWidget(scroll_hist)
        self.tabs.addTab(history_tab, "历史记录")

        self.refresh_history()
        self.refresh_favorites()

        # 启动时自动加载"精选"（网络不通时静默提示，不弹窗打断启动）
        self._auto_loading = True
        QTimer.singleShot(100, self.do_search)

    def _build_menu(self):
        menu_bar = self.menuBar()
        settings_menu = menu_bar.addMenu("设置")
        if CONFIG_AVAILABLE:   # 缺少 config.py 时不显示（避免保存无效）
            proxy_action = QAction("代理设置...", self)
            proxy_action.triggered.connect(self.show_proxy_settings)
            settings_menu.addAction(proxy_action)
            pixabay_action = QAction("Pixabay Key 设置...", self)
            pixabay_action.triggered.connect(self.show_pixabay_key_settings)
            settings_menu.addAction(pixabay_action)
            lively_action = QAction("Lively 路径设置...", self)
            lively_action.triggered.connect(self.show_lively_path_settings)
            settings_menu.addAction(lively_action)
        help_menu = menu_bar.addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def show_pixabay_key_settings(self):
        """动态壁纸 Pixabay 视频图源的免费 API Key 配置入口"""
        try:
            from config import get_pixabay_key, set_pixabay_key
        except ImportError:
            return
        current = get_pixabay_key()
        tip = ("Pixabay 视频（动态壁纸图源）需要免费 API Key：\n"
               "1. 注册/登录 https://pixabay.com\n"
               "2. 访问 https://pixabay.com/api/docs/ 查看 \"Your API key\"\n"
               "3. 把 Key 粘贴到这里\n"
               "不使用该图源可不填写（「动态壁纸·精选」免 Key 可用）。\n\n"
               f"当前 Key: {current[:6] + '...' if current else '未设置'}")
        key, ok = QInputDialog.getText(self, "Pixabay Key 设置", tip, text=current)
        if not ok:
            return
        set_pixabay_key(key)
        QMessageBox.information(
            self, "完成",
            "Pixabay Key 已保存。\n切换到「动态壁纸·Pixabay」图源即可搜索视频。")

    def show_lively_path_settings(self):
        """手动指定 Lively Wallpaper 主程序路径（自动定位失败时使用）"""
        try:
            from config import get_lively_path, set_lively_path
        except ImportError:
            return
        current = get_lively_path()
        tip = ("设置动态壁纸需要 Lively Wallpaper（免费开源）。\n"
               "一般情况下无需手动指定（自动定位），仅当安装到了非默认位置时填写。\n"
               "例如: C:\\Program Files\\Lively Wallpaper\\livelywallpaper.exe\n\n"
               f"当前: {current or '未设置（自动定位）'}")
        path, ok = QFileDialog.getOpenFileName(
            self, "选择 Lively Wallpaper 主程序", current or "",
            "可执行文件 (*.exe)")
        if not ok or not path:
            return
        set_lively_path(path)
        QMessageBox.information(self, "完成", f"Lively 路径已保存:\n{path}")

    def show_proxy_settings(self):
        current = get_proxy_url()
        tip = ("部分图源（如 wallhaven）可能无法直连，需要代理。\n"
               "示例: http://127.0.0.1:7890\n"
               "留空 = 不使用代理（直连）")
        url, ok = QInputDialog.getText(
            self, "代理设置",
            f"{tip}\n\n当前代理: {current or '未设置'}",
            text=current)
        if not ok:
            return
        set_proxy_url(url)
        if url.strip():
            QMessageBox.information(
                self, "完成",
                f"代理已保存: {url.strip()}\n重新搜索即可生效。")
        else:
            QMessageBox.information(
                self, "完成", "已切换为直连（不使用代理）。")

    def show_about(self):
        QMessageBox.about(self, "关于 - 桌面壁纸助手", """
            <h3>桌面壁纸助手</h3>
            <p><b>图片版权声明：</b></p>
            <ul align="left">
            <li><b>Wallhaven</b>：图片来自 wallhaven.cc，仅供个人欣赏，请勿商用</li>
            <li><b>Bing</b>：图片来自微软 Bing 每日壁纸，版权归微软及原作者所有</li>
            <li><b>彼岸图网</b>：图片来自 netbian.com，仅供个人欣赏，请勿商用</li>
            <li><b>极简壁纸</b>：图片来自 bz.zzzmh.cn，仅供个人欣赏，请勿商用</li>
            <li><b>动态壁纸·精选</b>：视频来自 Pexels（免费授权），仅供个人桌面欣赏，请勿商用</li>
            <li><b>动态壁纸·Pixabay</b>：视频来自 pixabay.com（免费授权），仅供个人桌面欣赏，请勿商用</li>
            <li><b>本地</b>：图片来自您本机文件夹，版权归原始作者所有</li>
            </ul>
            <p>请尊重原作者版权，勿将图片用于商业用途。</p>
        """)

    # ---------- 图源栏 ----------
    def _build_source_bar(self):
        row = QHBoxLayout()
        row.addWidget(QLabel("图源:"))
        self.source_combo = QComboBox()
        for name, label in SOURCE_LABELS.items():
            self.source_combo.addItem(label, name)
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        row.addWidget(self.source_combo)

        self.btn_pick_folder = QPushButton("📂 选择文件夹...")
        self.btn_pick_folder.clicked.connect(self.pick_local_folder)
        self.btn_pick_folder.hide()
        row.addWidget(self.btn_pick_folder)
        row.addStretch()
        return row

    def on_source_changed(self, index):
        name = self.source_combo.currentData()
        prev = self.current_source_name   # 记住切换前的图源，取消确认时用于回退
        self.current_source_name = name
        self._sync_source_controls(name)
        if name == "local" and not SOURCE_REGISTRY["local"].folder:
            self.current_search_params = None
            self._clear_results_grid()
            return
        # 需要代理的图源：未配置时先弹指引，勾选确认才加载，
        # 取消/关闭则回退到原图源并明确提示原因（避免"选了没反应"）
        need_proxy = (name in PROXY_REQUIRED_SOURCES
                      and not get_proxy_url() and not self._proxy_ready)
        if need_proxy:
            if self._show_proxy_gate(name):
                self._proxy_ready = True
                self.do_search()
            else:
                self._revert_source(prev)
                QMessageBox.information(
                    self, "未切换图源",
                    f"{SOURCE_LABELS[name]} 需要先开启代理，当前无法加载。\n"
                    f"已继续使用「{SOURCE_LABELS.get(prev, prev)}」。")
            return
        self.do_search()

    def _revert_source(self, prev):
        """代理确认被取消/关闭：把图源下拉框与控件状态恢复为切换前"""
        self.current_source_name = prev
        idx = self.source_combo.findData(prev)
        if idx >= 0:
            self.source_combo.blockSignals(True)
            self.source_combo.setCurrentIndex(idx)
            self.source_combo.blockSignals(False)
        self._sync_source_controls(prev)

    def _show_proxy_gate(self, name):
        """需要代理的图源：弹出配置指引，返回是否启用"""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"启用 {SOURCE_LABELS[name]}")
        dialog.setMinimumWidth(520)

        lay = QVBoxLayout(dialog)

        tip = QLabel(
            f"⚠ 启用 {SOURCE_LABELS[name]} 需要完成配置\n\n"
            + self._source_help_text(name))
        tip.setTextFormat(Qt.PlainText)
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 官网链接 + 复制按钮（单独成行）
        for url in self._source_urls(name):
            lay.addWidget(self._make_url_row(url))

        lay.addSpacing(8)

        ck = QCheckBox("我已完成配置（已开启代理），启用此图源")
        lay.addWidget(ck)

        btns = QHBoxLayout()
        btn_cancel = QPushButton("取消")
        btn_ok = QPushButton("启用")
        btn_ok.setEnabled(False)
        ck.toggled.connect(btn_ok.setEnabled)
        btns.addStretch()
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        lay.addLayout(btns)

        btn_cancel.clicked.connect(dialog.reject)
        btn_ok.clicked.connect(dialog.accept)

        return dialog.exec_() == QDialog.Accepted


    def _source_help_text(self, name):
        """不同图源给出不同的排障指引"""
        if name == "wallhaven":
            return (
                "1. Wallhaven 在国内无法直连，需要代理才能访问：\n"
                "   • 开启代理软件的 TUN / 增强（全局）模式；或\n"
                "   • 在菜单【设置 → 代理设置...】里填入代理地址\n"
                "     （例如 http://127.0.0.1:7890）\n"
                "   注意：仅开浏览器系统代理对本程序无效，必须用上面两种方式之一。\n\n"
                "2. 然后勾选下面的开关并加载。"
            )
        else:   # bing / local 一般不需要额外配置
            return (
                "该图源可能因网络问题暂时无法访问，\n"
                "请检查网络连接后，勾选下面的开关并重试。"
            )


    def _clear_results_grid(self):
        """清空搜索结果区"""
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _sync_source_controls(self, name):
        """根据当前图源同步控件状态（启动时和切换图源时都会调用）"""
        self.btn_pick_folder.setVisible(name == "local")
        searchable = name in ("wallhaven", "zzzmh", "video_pixabay")
        self.search_input.setEnabled(searchable)
        self.btn_search.setEnabled(searchable)
        if name == "wallhaven":
            self.search_input.setPlaceholderText("输入英文关键词，如 landscape / game ...")
        elif name == "zzzmh":
            self.search_input.setPlaceholderText("输入中文关键词搜索，如 风景 / 猫 / 星空 ...")
        elif name == "video_pixabay":
            self.search_input.setPlaceholderText("输入英文关键词搜索视频，如 ocean / city ...")
        elif name == "video_pexels":
            self.search_input.setPlaceholderText("精选视频免 Key 直接浏览，选择上方分类即可")
        elif name == "bing":
            self.search_input.setPlaceholderText("Bing 每日一图自动更新，无需搜索")
        elif name == "netbian":
            self.search_input.setPlaceholderText("彼岸图网按分类浏览，选择上方分类即可")
        else:
            self.search_input.setPlaceholderText("请先选择本地壁纸文件夹")
        self._fill_category_bar()

    def pick_local_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择壁纸文件夹")
        if folder:
            SOURCE_REGISTRY["local"].folder = folder
            self.do_search()

    # ---------- 分类栏 ----------
    def _build_category_bar(self):
        self.category_bar = QWidget()
        self.category_bar.setObjectName("categoryBar")
        self.category_bar.setStyleSheet("""
            QWidget#categoryBar {
                background: #fafafa;
                border-bottom: 1px solid #e8e8e8;
            }
            QPushButton#categoryTab {
                border: none;
                border-bottom: 3px solid transparent;
                padding: 0 18px;
                font-size: 14px;
                color: #555;
                background: transparent;
            }
            QPushButton#categoryTab:checked {
                border-bottom: 3px solid #ffa500;
                color: #333;
                font-weight: bold;
            }
            QPushButton#categoryTab:hover:!checked { color: #ffa500; }
        """)
        QHBoxLayout(self.category_bar)
        self.category_bar.layout().setContentsMargins(0, 0, 0, 0)
        # 禁止压缩到 sizeHint 以下，保证按钮溢出时滚动条必然出现
        self.category_bar.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
        self._fill_category_bar()
        # 分类较多（彼岸图网 16 个）时支持横向滚动
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.category_bar)
        scroll.setFixedHeight(50)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{border:none; background:transparent;}"
            "QScrollBar:horizontal{height:8px; background:#f0f0f0; border-radius:4px;}"
            "QScrollBar::handle:horizontal{background:#aaa; border-radius:4px; min-width:30px;}"
            "QScrollBar::handle:horizontal:hover{background:#888;}"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal{width:0;}")
        return scroll

    def _fill_category_bar(self):
        """根据当前源重建分类按钮"""
        layout = self.category_bar.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        cats = CATEGORY_MAP.get(self.current_source_name, [])
        self.category_buttons = []
        if not cats:
            empty = QLabel("此源无分类")
            empty.setStyleSheet("color:#999; padding:8px;")
            layout.addWidget(empty)
            layout.addStretch()
            self.current_category = None
            return

        self.category_group = QButtonGroup(self)
        self.category_group.setExclusive(True)
        for i, cat in enumerate(cats):
            btn = QPushButton(cat["label"])
            btn.setObjectName("categoryTab")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(40)
            btn.clicked.connect(lambda checked, c=cat: self.on_category_clicked(c))
            self.category_group.addButton(btn)
            self.category_buttons.append(btn)
            layout.addWidget(btn)
            if i < len(cats) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet("color: #ddd;")
                sep.setFixedHeight(18)
                layout.addWidget(sep)
        layout.addStretch()
        self.category_buttons[0].setChecked(True)
        self.current_category = cats[0]
        # 强制立即重算几何，确保外层 QScrollArea 及时更新滚动条状态
        layout.activate()

    def on_category_clicked(self, category):
        """点击分类标签：回填搜索框 + 立即搜索"""
        if category is self.current_category:
            return
        self.current_category = category
        q = category["params"].get("q", "")
        self.search_input.setText(q)
        self.do_search()

    # ---------- 搜索 ----------
    def do_search(self):
        # 标记本次是否为启动自动加载（用于决定报错方式），手动搜索后恢复弹窗
        self._error_silent = getattr(self, "_auto_loading", False)
        self._auto_loading = False
        user_q = self.search_input.text().strip()
        source = get_source(self.current_source_name)
        cats = CATEGORY_MAP.get(self.current_source_name, [])

        params = {}
        if cats:
            if self.current_category not in cats:
                self.current_category = cats[0]
            params = dict(self.current_category["params"])
        if user_q:
            params["q"] = user_q

        self.current_search_params = params
        self.current_page = 1
        self.has_more = True
        self._seen_ids.clear()

        self._clear_results_grid()

        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.is_loading = True

        self.search_thread = SearchThread(
            source, params, self.current_page,
            f"{self.resolution[0]}x{self.resolution[1]}")
        self.search_thread.finished.connect(self.on_search_finished)
        self.search_thread.error.connect(self.on_search_error)
        self.search_thread.start()

    def on_search_finished(self, results, has_more):
        self.progress.setVisible(False)
        self.is_loading = False
        self.has_more = has_more
        unique = [r for r in results if r["id"] not in self._seen_ids]
        for r in unique:
            self._seen_ids.add(r["id"])
        self.current_results.extend(unique)
        self._append_cards(unique)

    def on_search_error(self, msg):
        self.progress.setVisible(False)
        self.is_loading = False
        # 启动自动加载失败时一律在结果区提示，绝不弹窗打断首屏
        silent = getattr(self, "_error_silent", False)
        if "人机验证" in msg or "HTTP 405" in msg:
            # 彼岸图网 WAF 拦截：给出可操作的提示，不弹原始报错
            if silent:
                self._show_blocked_hint()
            else:
                QMessageBox.warning(self, "彼岸图网暂时不可用", msg)
            return
        # 网络连不上时给出可操作的建议，而不是原始报错信息
        if "timed out" in msg or "Max retries" in msg or "ConnectionError" in msg:
            host = "图源网站"
            m = re.search(r"host='([^']+)'", msg)
            if m:
                host = m.group(1)
            if silent:
                self._show_net_hint(host)
                return
            QMessageBox.critical(
                self, "搜索失败",
                f"无法连接 {host}（连接超时），该站点可能无法直连。\n\n"
                "解决办法：\n"
                "1. 菜单【设置 → 代理设置...】填入本机代理地址后重试；\n"
                "2. 或改用「极简壁纸」「Bing 每日」「彼岸图网」「本地图源」等可直连的图源。")
        elif silent:
            self._show_generic_hint(msg)
        else:
            QMessageBox.critical(self, "搜索失败", msg)

    def _show_generic_hint(self, msg):
        """启动自动加载遇到非网络错误时的结果区提示（不弹窗）"""
        text = str(msg).strip()
        if len(text) > 200:
            text = text[:200] + "…"
        hint = QLabel(f"⚠ 图片加载失败\n\n{text}\n\n可点击上方分类重试，或切换其他图源。")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:14px; padding:40px;")
        self.grid_layout.addWidget(hint, 0, 0, 1, 5)

    def _show_blocked_hint(self):
        """启动自动加载被 WAF 拦截时，在结果区显示提示（不打断启动）"""
        hint = QLabel(
            "⚠ 彼岸图网触发了人机验证（HTTP 405，站点侧限制）\n\n"
            "可等待 1~5 分钟后点击分类重试，\n"
            "或切换到「极简壁纸」「Bing 每日」等其他图源。")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:14px; padding:40px;")
        self.grid_layout.addWidget(hint, 0, 0, 1, 5)

    def _show_net_hint(self, host):
        """在搜索结果区显示网络提示（代替启动时的报错弹窗）"""
        hint = QLabel(
            f"⚠ 无法连接 {host}，该站点可能无法直连\n\n"
            "可在菜单【设置 → 代理设置...】配置代理后重新搜索，\n"
            "或切换到「Bing 每日」等其他图源。")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:14px; padding:40px;")
        self.grid_layout.addWidget(hint, 0, 0, 1, 5)

    def _append_cards(self, results):
        start_index = self.grid_layout.count()
        for i, w in enumerate(results):
            card = WallpaperCard(w)
            row, col = divmod(start_index + i, 5)
            self.grid_layout.addWidget(card, row, col)
            card._check_fav_state()    # ← 新增

    def on_scroll(self, value):
        if self.is_loading or not self.has_more or self.current_search_params is None:
            return
        sb = self.sender()
        if sb and sb.maximum() - value < 200:
            self.load_next_page()

    def load_next_page(self):
        if self.current_search_params is None:
            return
        self.is_loading = True
        self.current_page += 1
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        self.search_thread = SearchThread(
            get_source(self.current_source_name),
            self.current_search_params, self.current_page,
            f"{self.resolution[0]}x{self.resolution[1]}")
        self.search_thread.finished.connect(self.on_search_finished)
        self.search_thread.error.connect(self.on_search_error)
        self.search_thread.start()

    # ---------- 壁纸操作 ----------
    def apply_wallpaper(self, info):
        """点击「设置」：下载与系统调用交给后台线程，界面保持可响应

        视频卡片（kind=video 或 .mp4 链接）走 VideoApplyThread：
        下载 mp4 后调用 Lively Wallpaper 设置动态壁纸。
        """
        thread = getattr(self, "_apply_thread", None)
        if thread is not None and thread.isRunning():
            return   # 上一次还在执行，忽略重复点击
        self._apply_info = info
        is_video = (info.get("kind") == "video"
                    or str(info.get("url", "")).lower().endswith(".mp4"))
        if is_video:
            self._apply_style = "video"
        else:
            self._apply_style = self.style_combo.currentText().split(" ")[0]
        # 搜索结果没有 tags 字段，沿用搜索框内容；历史/收藏卡片带回原标签
        self._apply_tags = info.get("tags", self.search_input.text())

        msg = "正在下载并设置动态壁纸（{}）...".format(info['resolution']) \
            if is_video else \
            "正在下载并设置壁纸（{}）...".format(info['resolution'])
        self.statusBar().showMessage(msg)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        if is_video:
            self._apply_thread = VideoApplyThread(info)
        else:
            self._apply_thread = ApplyThread(info, self._apply_style)
        self._apply_thread.done.connect(self.on_apply_done)
        self._apply_thread.failed.connect(self.on_apply_failed)
        self._apply_thread.start()

    def on_apply_done(self, path, thumb):
        self.statusBar().clearMessage()
        if not self.is_loading:
            self.progress.setVisible(False)
        info = self._apply_info
        try:
            self.db.add_record(
                file_name=os.path.basename(path),
                file_path=path,
                source_url=info["url"],
                tags=self._apply_tags,
                resolution=info["resolution"],
                style=self._apply_style,
                source=info.get("source", "wallhaven"),
                thumb_path=thumb
            )
            self.refresh_history()
        except Exception as e:
            QMessageBox.warning(self, "记录失败", str(e))
        QMessageBox.information(self, "成功", f"壁纸已切换！\n{info['resolution']}")

    def on_apply_failed(self, msg):
        self.statusBar().clearMessage()
        if not self.is_loading:
            self.progress.setVisible(False)
        if "Lively Wallpaper" in msg:
            # 未安装 Lively：给出安装指引（带可点击链接），而不是原始报错
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("需要安装 Lively Wallpaper")
            box.setText(
                "设置动态壁纸前，需要先安装免费开源的 Lively Wallpaper。\n\n"
                "下载安装后，回到本工具重新点「设动态」即可，无需其它配置。<br>"
                "• 官网下载：<a href='https://rocksdanister.github.io/lively'>"
                "rocksdanister.github.io/lively</a><br>"
                "• 或在 Microsoft Store 搜索 \"Lively Wallpaper\"<br><br>"
                "安装位置非默认时，可在菜单【设置 → Lively 路径设置...】手动指定。")
            box.setTextFormat(Qt.RichText)
            box.setStandardButtons(QMessageBox.Ok)
            box.exec_()
            return
        QMessageBox.critical(self, "设置失败", msg)

    # ---------- 收藏 / 历史 ----------
    def refresh_favorites(self):
        while self.fav_grid.count():
            item = self.fav_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        favs = self.db.get_favorites(500)
        for i, f in enumerate(favs):
            card = FavoriteCard(f)
            row, col = divmod(i, 5)
            self.fav_grid.addWidget(card, row, col)

    def _sync_fav_icons(self):
        """收藏状态变化后同步搜索结果卡片上的爱心图标"""
        for i in range(self.grid_layout.count()):
            w = self.grid_layout.itemAt(i).widget()
            if isinstance(w, WallpaperCard):
                w._set_fav(self.db.is_favorite(w.info["url"]))

    def refresh_history(self):
        while self.hist_grid.count():
            item = self.hist_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        records = self.db.get_all(100)
        for i, r in enumerate(records):
            card = HistoryCard(r)
            row, col = divmod(i, 5)
            self.hist_grid.addWidget(card, row, col)

    def clear_history(self):
        if QMessageBox.question(self, "确认", "确定清空所有历史记录?") == QMessageBox.Yes:
            self.db.clear_all()
            self.refresh_history()

    def clear_all_cache(self):
        count = clear_cache()
        for folder in (THUMB_DIR, THUMB_CACHE_DIR):
            for f in folder.glob("*"):
                if f.is_file():
                    f.unlink()
                    count += 1
        _THUMB_MEM.clear()
        self.refresh_history()
        self.refresh_favorites()
        QMessageBox.information(self, "完成", f"已清理 {count} 个文件")

    def closeEvent(self, event):
        thread = getattr(self, "_apply_thread", None)
        if thread is not None and thread.isRunning():
            thread.wait(5000)   # 等后台设置结束，避免退出时线程被销毁
        self.db.close()
        event.accept()

    def _make_url_row(self, url):
        """一行：可点击的链接 + 复制按钮"""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel(f"🔗 <a href='{url}'>{url}</a>")
        lbl.setTextFormat(Qt.RichText)
        lbl.setOpenExternalLinks(True)   # 点链接直接开浏览器
        h.addWidget(lbl, 1)

        btn = QPushButton("复制")
        btn.setFixedWidth(60)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                border: 1px solid #d0d0d0; border-radius: 4px;
                padding: 2px 8px; background: #f5f5f5;
            }
            QPushButton:hover { background: #e8f0fe; border-color: #4a90d9; }
        """)

        def copy_url():
            QApplication.clipboard().setText(url)
            btn.setText("已复制 ✓")
            btn.setStyleSheet(btn.styleSheet().replace("#f5f5f5", "#e6ffe6"))
            QTimer.singleShot(1500, lambda: btn.setText("复制"))

        btn.clicked.connect(copy_url)
        h.addWidget(btn)
        return w

    def _source_urls(self, name):
        if name == "wallhaven":
            return ["https://wallhaven.cc"]
        return []

    
# ================= 程序入口 =================
if __name__ == "__main__":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WallpaperTool.Main")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

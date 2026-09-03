"""WallpaperTool 主程序入口"""
import sys
import os
import re
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
    QFileDialog, QAction, QInputDialog, QCheckBox, QDialog
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QPixmap

from wallpaper_engine import (
    get_screen_resolution, download_image, set_wallpaper,
    delete_cached_image, clear_cache, generate_thumbnail
)
from database import HistoryDB, THUMB_DIR
from api_client import SOURCE_REGISTRY, get_source

# config.py 提供应用内代理设置；万一本地缺这个文件，降级运行而不是崩溃
try:
    from config import get_proxy_url, set_proxy_url, get_proxies
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False

    def get_proxy_url():
        return ""

    def set_proxy_url(url):
        pass

    def get_proxies():
        return None

# ============ 每个图源对应的分类配置 ============
CATEGORY_MAP = {
    "wallhaven": [
        {"key": "featured",  "label": "精选",  "params": {"sorting": "toplist", "topRange": "1M"}},
        {"key": "landscape", "label": "风景",  "params": {"q": "landscape"}},
        {"key": "anime",     "label": "动漫",  "params": {"categories": "010"}},
        {"key": "beauty",    "label": "美女",  "params": {"q": "women", "categories": "001"}},
        {"key": "scifi",     "label": "科幻",  "params": {"q": "sci-fi"}},
        {"key": "game",      "label": "游戏",  "params": {"q": "video game"}},
        {"key": "car",       "label": "汽车",  "params": {"q": "car"}},
        {"key": "sports",    "label": "体育",  "params": {"q": "sports"}},
    ],
    "unsplash": [
        {"key": "featured",  "label": "精选",  "params": {"q": "nature"}},
        {"key": "landscape", "label": "风景",  "params": {"q": "landscape,mountain"}},
        {"key": "anime",     "label": "动漫",  "params": {"q": "anime,illustration"}},
        {"key": "beauty",    "label": "美女",  "params": {"q": "portrait,woman"}},
        {"key": "scifi",     "label": "科幻",  "params": {"q": "sci-fi,futuristic"}},
        {"key": "game",      "label": "游戏",  "params": {"q": "gaming,neon"}},
        {"key": "car",       "label": "汽车",  "params": {"q": "car,supercar"}},
        {"key": "sports",    "label": "体育",  "params": {"q": "sports"}},
    ],
    "bing": [
        {"key": "daily", "label": "每日一图", "params": {}},
    ],
    "local": [],   # 本地源无分类
}

# Bing 国内可直连，作为默认图源（下拉框第一项 = 默认选中）
SOURCE_LABELS = {
    "bing":      "Bing 每日",
    "wallhaven": "Wallhaven",
    "unsplash":  "Unsplash",
    "local":     "本地文件夹",
}

# 这些图源国内无法直连：切换过去时不自动加载，先给出代理提示 + 开关
PROXY_REQUIRED_SOURCES = {"wallhaven", "unsplash"}


# ================= 线程组件 =================
class ThumbLoader(QThread):
    """异步加载网络缩略图"""
    loaded = pyqtSignal(QPixmap)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        import requests
        try:
            resp = requests.get(self.url, timeout=10, proxies=get_proxies())
            pixmap = QPixmap()
            pixmap.loadFromData(resp.content)
            self.loaded.emit(pixmap)
        except Exception:
            pass


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


# ================= 壁纸卡片（搜索结果） =================
class WallpaperCard(QFrame):
    def __init__(self, wallpaper_info, parent=None):
        super().__init__(parent)
        self.info = wallpaper_info
        self.is_fav = False
        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        self.setLineWidth(1)
        self.setFixedSize(200, 220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(190, 130)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("background:#eee;")
        layout.addWidget(self.thumb_label)

        info_label = QLabel(f"ⓘ {self.info['resolution']} · {self.info.get('author', '')}")
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)

        btn_row = QHBoxLayout()
        self.btn_apply = QPushButton("设置")
        self.btn_apply.clicked.connect(self.on_apply)
        self.btn_fav = QPushButton("🤍")
        self.btn_fav.setFixedWidth(36)
        self.btn_fav.setStyleSheet("border:none; background:transparent; font-size:18px;")
        self.btn_fav.clicked.connect(self.on_toggle_fav)
        btn_row.addWidget(self.btn_apply, 2)
        btn_row.addWidget(self.btn_fav, 1)
        layout.addLayout(btn_row)

        self.load_thumb()
        # self._check_fav_state()

    def load_thumb(self):
        url = self.info.get("thumb") or self.info.get("url", "")
        if url.startswith("http"):
            self._thumb_thread = ThumbLoader(url)
            self._thumb_thread.loaded.connect(self.set_pixmap)
            self._thumb_thread.start()
        else:
            self.set_pixmap(QPixmap(url))

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
        if self.is_fav:
            main.db.remove_favorite(info["url"])
            self._set_fav(False)
        else:
            try:
                # 在线图源先下载到本地缓存，避免 URL 失效
                if info["url"].startswith("http"):
                    path = download_image(info["url"])
                else:
                    path = Path(info["url"])
                thumb = generate_thumbnail(str(path))
                main.db.add_favorite(
                    file_path=str(path),
                    file_name=path.name[:80],
                    source_url=info["url"],
                    thumb_path=str(thumb) if thumb else "",
                    resolution=info["resolution"],
                    source=info.get("source", "wallhaven")
                )
                self._set_fav(True)
            except Exception as e:
                QMessageBox.warning(self, "收藏失败", str(e))

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
        btn_apply = QPushButton("应用")
        btn_apply.clicked.connect(self.on_apply)
        btn_del = QPushButton("🗑")
        btn_del.setFixedWidth(30)
        btn_del.setStyleSheet("border:none; background:transparent; font-size:14px;")
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
        elif self.fpath and Path(self.fpath).exists():
            t = generate_thumbnail(self.fpath)
            if t:
                pixmap.load(str(t))
        elif self.surl and self.surl.startswith("http"):
            self._thread = ThumbLoader(self.surl)
            self._thread.loaded.connect(self._set_pixmap)
            self._thread.start()
            return
        self._set_pixmap(pixmap)

    def _set_pixmap(self, pixmap):
        if not pixmap.isNull():
            self.thumb_label.setPixmap(pixmap.scaled(
                self.thumb_label.size(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def on_apply(self):
        if Path(self.fpath).exists():
            style = self.style if self.style else "fill"
            set_wallpaper(self.fpath, style=style)
            QMessageBox.information(self, "成功", f"已切换到: {self.fname}")
        else:
            QMessageBox.warning(self, "失败", "图片文件已不存在")


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
        btn_apply = QPushButton("应用")
        btn_apply.clicked.connect(self.on_apply)
        self.btn_fav = QPushButton("❤️")
        self.btn_fav.setFixedWidth(30)
        self.btn_fav.setStyleSheet("border:none; background:transparent; font-size:16px;")
        self.btn_fav.setToolTip("点击取消喜欢")
        self.btn_fav.clicked.connect(self.on_fav)
        btn_row.addWidget(btn_apply, 1)
        btn_row.addWidget(self.btn_fav)
        layout.addLayout(btn_row)

    def on_fav(self):
        # 收藏页点 ❤️ = 取消收藏并从列表移除
        main = self.window()
        main.db.remove_favorite(self.surl)
        main.refresh_favorites()



# ================= 主窗口 =================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = HistoryDB()
        self.resolution = get_screen_resolution()
        self.current_results = []

        # 默认图源：Bing 每日（国内直连可用，无需代理）
        self.current_source_name = "bing"

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
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        self._build_menu()

        info_bar = QLabel(f"🖥 本机分辨率: {self.resolution[0]} × {self.resolution[1]}")
        info_bar.setStyleSheet("font-size: 14px; color: #666; padding: 4px;")
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

        # 按默认图源（Bing）同步控件初始状态：禁用搜索框、填充分类栏
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

        self.tabs.addTab(search_tab, "🔍 搜索壁纸")

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
        self.tabs.addTab(fav_tab, "❤️ 我的喜欢")

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
        self.tabs.addTab(history_tab, "📜 历史记录")

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
        help_menu = menu_bar.addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

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
            <li><b>Unsplash</b>：图片来自 Unsplash，遵循
                <a href="https://unsplash.com/license">Unsplash License</a>，可免费商用</li>
            <li><b>Bing</b>：图片来自微软 Bing 每日壁纸，版权归微软及原作者所有</li>
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
        # 需要代理的图源：尚未配置应用内代理、且本次会话没确认过代理时，
        # 先弹指引（含官网链接）；勾选确认才加载，取消/关闭则自动切回原图源
        if (name in PROXY_REQUIRED_SOURCES
                and not get_proxy_url() and not self._proxy_ready):
            if self._show_proxy_gate(name):
                self._proxy_ready = True
                self.do_search()
            else:
                self._revert_source(prev)
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
        """需要代理/Key 的图源：弹出配置指引，返回是否启用"""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"启用 {SOURCE_LABELS[name]}")
        dialog.setMinimumWidth(520)

        lay = QVBoxLayout(dialog)

        tip = QLabel(
            f"⚠ {SOURCE_LABELS[name]} 无法访问\n\n"
            + self._source_help_text(name))
        tip.setTextFormat(Qt.PlainText)
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 官网链接 + 复制按钮（单独成行）
        for url in self._source_urls(name):
            lay.addWidget(self._make_url_row(url))

        lay.addSpacing(8)

        ck = QCheckBox("我已完成配置（已开代理 / 已填 Key），启用此图源")
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
        if name == "unsplash":
            return (
                "1. Unsplash 需要配置自己的 API Key 才能使用：\n"
                "   • 打开下方官网 → 注册账号 → Your apps → New Application\n"
                "   • 勾选同意条款 → 填写应用名 → 创建后复制 Access Key 填入程序\n\n"
                "2. Unsplash 在国内可能无法直连，如需访问请开启代理：\n"
                "   • 开启代理软件（系统代理或 TUN/增强模式均可）\n"
                "   • 或在菜单【设置 → 代理设置...】里填入代理地址\n\n"
                "3. 完成配置后，勾选下面的开关并加载。"
            )

        elif name == "wallhaven":
            return (
                "1. Wallhaven 在国内无法直连，需要代理才能访问：\n"
                "   • 开启代理软件（系统代理或 TUN/增强模式均可）\n"
                "   • 或在菜单【设置 → 代理设置...】里填入代理地址\n\n"
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
        searchable = name in ("wallhaven", "unsplash")
        self.search_input.setEnabled(searchable)
        self.btn_search.setEnabled(searchable)
        if searchable:
            self.search_input.setPlaceholderText("输入英文关键词，如 landscape / game ...")
        elif name == "bing":
            self.search_input.setPlaceholderText("Bing 每日一图自动更新，无需搜索")
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
        self._fill_category_bar()
        return self.category_bar

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
        self.current_results.extend(results)
        self._append_cards(results)

    def on_search_error(self, msg):
        self.progress.setVisible(False)
        self.is_loading = False
        # 网络连不上时给出可操作的建议，而不是原始报错信息
        if "timed out" in msg or "Max retries" in msg or "ConnectionError" in msg:
            host = "图源网站"
            m = re.search(r"host='([^']+)'", msg)
            if m:
                host = m.group(1)
            if getattr(self, "_error_silent", False):
                # 启动自动加载失败：不打断启动，仅在结果区显示提示
                self._show_net_hint(host)
                return
            QMessageBox.critical(
                self, "搜索失败",
                f"无法连接 {host}（连接超时），该站点可能无法直连。\n\n"
                "解决办法：\n"
                "1. 菜单【设置 → 代理设置...】填入本机代理地址后重试；\n"
                "2. 或改用「Bing 每日」「本地图源」等可直连的图源。")
        else:
            QMessageBox.critical(self, "搜索失败", msg)

    def _show_net_hint(self, host):
        """在搜索结果区显示网络提示（代替启动时的报错弹窗）"""
        hint = QLabel(
            f"⚠ 无法连接 {host}，该站点可能无法直连\n\n"
            "可在菜单【设置 → 代理设置...】配置代理后重新搜索，\n"
            "或切换到「Bing 每日」「本地图源」。")
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
        try:
            url = info["url"]
            if url.startswith("http"):
                path = download_image(url)
            else:
                path = url   # 本地源：直接用文件路径
            style = self.style_combo.currentText().split(" ")[0]
            set_wallpaper(path, style=style)
            thumb = generate_thumbnail(str(path))
            self.db.add_record(
                file_name=os.path.basename(str(path)),
                file_path=str(path),
                source_url=url,
                tags=self.search_input.text(),
                resolution=info["resolution"],
                style=style,
                source=info.get("source", "wallhaven"),
                thumb_path=str(thumb) if thumb else ""
            )
            self.refresh_history()
            QMessageBox.information(self, "成功", f"壁纸已切换！\n{info['resolution']}")
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))

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
        for f in THUMB_DIR.glob("*"):
            if f.is_file():
                f.unlink()
                count += 1
        self.refresh_history()
        self.refresh_favorites()
        QMessageBox.information(self, "完成", f"已清理 {count} 个文件")

    def closeEvent(self, event):
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
        if name == "unsplash":
            return [
                "https://unsplash.com/developers",     # 申请 Key 的控制台
                "https://unsplash.com/documentation",  # API 文档（可选）
            ]
        elif name == "wallhaven":
            return ["https://wallhaven.cc"]
        return []

    
# ================= 程序入口 =================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

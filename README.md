# WallpaperTool

一个基于 PyQt5 的 Windows 桌面壁纸管理工具，支持多图源搜索、一键设置壁纸、收藏管理与历史记录。

## 功能特性

- **多图源搜索**
  - Bing 每日壁纸（国内直连，默认图源）
  - Wallhaven 壁纸库
  - Unsplash 高清摄影
  - 本地文件夹浏览
- **分类浏览** — 风景、动漫、美女、科幻、游戏、汽车、体育等预设分类
- **一键设壁纸** — 支持 5 种显示模式：居中、拉伸、适应、填充、平铺
- **收藏夹** — 收藏喜欢的壁纸，随时回看
- **历史记录** — SQLite 本地存储，自动记录每次设置的壁纸
- **缩略图加载** — 异步线程加载网络缩略图，界面不卡顿
- **代理支持** — 内置 SOCKS5 代理设置，国内访问 Wallhaven / Unsplash 无障碍
- **缓存管理** — 自动缓存下载的壁纸，支持一键清理

## 技术栈

| 组件 | 说明 |
|------|------|
| Python 3 | 运行时 |
| PyQt5 | GUI 框架 |
| requests | HTTP 请求 |
| Pillow | 图片处理 / 缩略图生成 |
| pysocks | SOCKS5 代理支持 |
| SQLite | 历史记录与收藏存储 |
| Windows API (ctypes) | 调用 SystemParametersInfoW 设置壁纸 |

## 项目结构

```
WallpaperTool/
├── main.py              # 主程序入口，GUI 界面与交互逻辑
├── api_client.py        # 图源适配器（Bing / Wallhaven / Unsplash / 本地）
├── wallpaper_engine.py  # 壁纸核心引擎（下载、设置、缩略图、缓存）
├── database.py          # SQLite 历史记录与收藏管理
├── config.py            # 应用配置（代理设置持久化）
├── requirements.txt     # Python 依赖
└── 壁纸助手.spec         # PyInstaller 打包配置
```

## 快速开始

### 环境要求

- Windows 10 / 11
- Python 3.8+

### 安装

```bash
git clone https://github.com/your-username/WallpaperTool.git
cd WallpaperTool
pip install -r requirements.txt
```

### 运行

```bash
python main.py
```

### 打包为 EXE（可选）

```bash
pip install pyinstaller
pyinstaller 壁纸助手.spec
```

打包后的程序位于 `dist/` 目录。

## 配置说明

- **代理设置** — 在应用内设置页面配置代理地址，格式如 `socks5://127.0.0.1:1080`
- **Unsplash API Key** — 如需使用 Unsplash 图源，在 `api_client.py` 中填入你的 API Key（[申请地址](https://unsplash.com/developers)）
- **数据存储位置** — `~/.WallpaperTool/`，包含缓存图片、缩略图、历史数据库和配置文件

## 开源协议

MIT License

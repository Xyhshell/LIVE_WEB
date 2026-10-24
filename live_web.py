# -*- coding: utf-8 -*-

from __future__ import annotations

import os

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
os.environ.setdefault("OPENCV_VIDEOIO_DEBUG", "0")
os.environ.setdefault("OPENCV_FFMPEG_DEBUG", "0")

import sys
import json
import time
import hmac
import queue
import socket
import secrets
import ctypes
import threading
import traceback
import webbrowser
import subprocess
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit, parse_qs, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie

import cv2
import numpy as np

try:
    from cv2_enumerate_cameras import enumerate_cameras
    HAS_ENUM = True
except ImportError:
    HAS_ENUM = False

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pystray
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

try:
    import pythoncom
    HAS_COM = True
except ImportError:
    HAS_COM = False

try:
    import winreg
    HAS_WINREG = True
except ImportError:
    HAS_WINREG = False


# ============================================================
#                      全局常量
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
RECORDING_DIR = BASE_DIR / "recordings"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE: Path | None = None
LOG_KEEP_DAYS = 30
CONFIG_FILE = BASE_DIR / "config.json"
ICON_FILE = BASE_DIR / "app_icon.ico"

WEB_HOST = "0.0.0.0"
WEB_PORT = 8765
STREAM_MAX_WIDTH = 1600
STREAM_FPS = 15.0
STREAM_QUALITY = 80
SNAPSHOT_QUALITY = 95
SESSION_HOURS = 24
COOKIE_NAME = "cam_session"

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".ts")

# openh264 优先查找的 DLL 名称（运行目录下）
PREFERRED_H264_DLLS = (
    "openh264-2.5.0-win64.dll",
    "openh264-2.4.1-win64.dll",
    "openh264-2.4.0-win64.dll",
    "openh264-2.3.1-win64.dll",
    "openh264-2.3.0-win64.dll",
    "openh264-2.2.0-win64.dll",
    "openh264-2.1.1-win64.dll",
    "openh264.dll",
)


# ============================================================
#                   日志系统（UTF-8）
# ============================================================
def _setup_stdout_utf8():
    """让控制台和 stdout/stderr 都使用 UTF-8，彻底避免中文乱码。"""
    # 1) 尝试把 Windows 控制台代码页切到 UTF-8 (65001)
    if sys.platform.startswith("win"):
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    # 2) Python 文本层也按 UTF-8 走
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _clean_old_logs(days=LOG_KEEP_DAYS):
    try:
        if not LOG_DIR.exists():
            return
        cutoff = time.time() - days * 86400
        for f in LOG_DIR.glob("monitor_*.log"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _migrate_logs_to_utf8():
    """把历史 GBK 日志迁移为 UTF-8，避免读取时乱码。"""
    try:
        if not LOG_DIR.exists():
            return
        for f in LOG_DIR.glob("monitor_*.log"):
            try:
                with open(f, "rb") as fh:
                    data = fh.read()
            except Exception:
                continue
            if not data:
                continue
            # 优先按 utf-8-sig 解码，能过就跳过
            try:
                data.decode("utf-8-sig")
                continue
            except UnicodeDecodeError:
                pass
            # 尝试 GBK 解码
            try:
                text = data.decode("gbk", errors="replace")
            except Exception:
                continue
            try:
                with open(f, "w", encoding="utf-8", errors="replace") as fh:
                    fh.write(text)
            except Exception:
                pass
    except Exception:
        pass


def init_log():
    global LOG_FILE
    _setup_stdout_utf8()
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        LOG_FILE = LOG_DIR / f"monitor_{today}.log"
        # 使用 utf-8（无 BOM），与追加/读取保持一致
        if not LOG_FILE.exists():
            try:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write("")
            except Exception:
                pass
        _clean_old_logs()
        _migrate_logs_to_utf8()
    except Exception:
        LOG_FILE = BASE_DIR / "monitor.log"


def log(msg):
    global LOG_FILE
    if LOG_FILE is None:
        init_log()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"

    # 控制台输出：优先 print，失败时回退到 buffer 直写
    try:
        print(line, flush=True)
    except (UnicodeEncodeError, UnicodeError):
        try:
            buf = getattr(sys.stdout, "buffer", None)
            if buf is not None:
                buf.write(line.encode("utf-8", "replace") + b"\n")
                buf.flush()
        except Exception:
            pass
    except Exception:
        pass

    # 文件输出：统一 UTF-8
    try:
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_log_files():
    result = []
    try:
        if not LOG_DIR.exists():
            return result
        for f in LOG_DIR.glob("monitor_*.log"):
            try:
                st = f.stat()
                date_str = f.stem.replace("monitor_", "")
                result.append({
                    "name": f.name,
                    "date": date_str,
                    "size": st.st_size,
                    "size_text": _format_size(st.st_size),
                    "mtime": st.st_mtime,
                })
            except Exception:
                continue
    except Exception:
        pass
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


def read_log_file(name: str, max_lines: int = 1500,
                  max_bytes: int = 512 * 1024):
    safe_name = os.path.basename(name or "")
    if not safe_name.startswith("monitor_") or not safe_name.endswith(".log"):
        return None
    if "/" in safe_name or "\\" in safe_name or ".." in safe_name:
        return None
    target = LOG_DIR / safe_name
    if not target.exists() or not target.is_file():
        return None
    try:
        size = target.stat().st_size
        with open(target, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()
            data = f.read()

        # 多种编码兜底，避免历史 GBK 数据出现乱码
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "cp936", "gb18030"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = data.decode("utf-8", errors="replace")

        # 去掉可能残留的 BOM
        if text.startswith("\ufeff"):
            text = text[1:]

        lines = text.splitlines()
        return lines[-max_lines:]
    except Exception as e:
        log(f"[Web] 读取日志失败: {e}")
        return None


def delete_log_file(name: str) -> dict:
    """删除单个日志文件（仅限 monitor_*.log）。"""
    safe_name = os.path.basename(name or "")
    if not safe_name.startswith("monitor_") or not safe_name.endswith(".log"):
        return {"ok": False, "error": "无效的日志文件名"}
    if "/" in safe_name or "\\" in safe_name or ".." in safe_name:
        return {"ok": False, "error": "无效的日志文件名"}
    target = LOG_DIR / safe_name
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": "文件不存在"}
    try:
        target.unlink()
        log(f"[Web] 已删除日志 {safe_name}")
        return {"ok": True, "name": safe_name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_all_logs() -> dict:
    """删除全部 monitor_*.log 日志文件。"""
    removed = 0
    errors = []
    try:
        if not LOG_DIR.exists():
            return {"ok": True, "removed": 0}
        for f in list(LOG_DIR.glob("monitor_*.log")):
            try:
                if f.is_file():
                    f.unlink()
                    removed += 1
            except Exception as e:
                errors.append(f"{f.name}: {e}")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    log(f"[Web] 已批量删除日志 {removed} 个")
    return {"ok": True, "removed": removed, "errors": errors}


# ============================================================
#                        配置
# ============================================================
DEFAULT_CONFIG = {
    "camera_index": 0,
    "camera_backend": "CAP_DSHOW",
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "output_dir": str(RECORDING_DIR),
    "quality": 90,
    "retry_interval": 3.0,
    "max_retries": 10,
    "segment_minutes": 30,
    "first_run_done": False,
    "prefer_h264": False,
    "keep_alive": True,
    "occupancy_monitor_enabled": True,
    "occupancy_poll_interval": 2.0,
    "occupancy_confirm_count": 2,
    "occupancy_yield_seconds": 30,
    "yield_resume_delay": 3.0,
    "open_fail_threshold": 3,
    "process_monitor_enabled": True,
    "process_poll_interval": 2.0,
    "extra_webcam_apps": [],
    "min_file_size_mb": 1,
    "static_detection_enabled": True,
    "static_frame_timeout": 30,
    "static_diff_threshold": 3.0,
    "openh264_dll_path": "",
    "measure_fps_enabled": True,
    "measure_fps_seconds": 1.5,
    "container_format": "mkv",
    "web_auth": {},
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            log(f"[Config] 读取失败: {e}")
    cfg["output_dir"] = str(RECORDING_DIR)
    return cfg


def save_config(cfg):
    try:
        cfg["output_dir"] = str(RECORDING_DIR)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)
    except Exception as e:
        log(f"[Config] 保存失败: {e}")


def get_min_size_bytes(cfg):
    try:
        mb = float(cfg.get("min_file_size_mb", 1))
    except (TypeError, ValueError):
        mb = 1.0
    if mb < 0:
        mb = 0.0
    return int(mb * 1024 * 1024)


def clean_small_videos(output_dir, min_size_bytes):
    if min_size_bytes <= 0:
        return 0
    try:
        d = Path(output_dir)
        if not d.exists():
            return 0
        count = 0
        for ext in ("*.mp4", "*.mkv", "*.avi", "*.ts"):
            for f in d.glob(ext):
                try:
                    if f.is_file() and f.stat().st_size < min_size_bytes:
                        size = f.stat().st_size
                        f.unlink()
                        count += 1
                        log(f"[Clean] 删除小文件 {f.name} ({size/1024:.0f}KB)")
                except Exception as e:
                    log(f"[Clean] 删除失败 {f.name}: {e}")
        if count:
            log(f"[Clean] 共清理 {count} 个小文件")
        return count
    except Exception as e:
        log(f"[Clean] 扫描失败: {e}")
        return 0


# ============================================================
#                       应用图标
# ============================================================
def create_app_icon(size=256, dot_color="#e74c3c"):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = max(2, size // 32)
    d.rounded_rectangle(
        (pad, pad, size - pad, size - pad), radius=size // 6, fill="#2c3e50"
    )

    cx, cy = size // 2, size // 2
    r_out = int(size * 0.36)
    d.ellipse((cx - r_out, cy - r_out, cx + r_out, cy + r_out), fill="#ecf0f1")
    r_mid = int(size * 0.28)
    d.ellipse((cx - r_mid, cy - r_mid, cx + r_mid, cy + r_mid), fill="#3498db")
    r_in = int(size * 0.16)
    d.ellipse((cx - r_in, cy - r_in, cx + r_in, cy + r_in), fill="#1a252f")

    r_h = max(2, int(size * 0.06))
    hx = cx - r_mid + r_h
    hy = cy - r_mid + r_h
    d.ellipse((hx, hy, hx + r_h * 2, hy + r_h * 2), fill="#ffffff")

    dot_r = max(4, size // 8)
    margin = size // 12
    d.ellipse(
        (size - margin - dot_r * 2, margin, size - margin, margin + dot_r * 2),
        fill=dot_color, outline="#ffffff", width=max(1, size // 64),
    )
    return img


def ensure_icon_file():
    if ICON_FILE.exists():
        return ICON_FILE
    if not HAS_PIL:
        return None
    try:
        img = create_app_icon(256)
        img.save(
            ICON_FILE, format="ICO",
            sizes=[(16, 16), (32, 32), (48, 48), (64, 64),
                   (128, 128), (256, 256)],
        )
        return ICON_FILE
    except Exception as e:
        log(f"[Icon] 生成失败: {e}")
        return None


# ============================================================
#                     openh264 路径 / 加载
# ============================================================
def setup_openh264(cfg):
    """配置 openh264 DLL 搜索路径，并尝试加载。

    优先查找运行目录下的 openh264-2.5.0-win64.dll，
    成功/失败都会写入日志。
    """
    if not sys.platform.startswith("win"):
        log("[H264] 非 Windows 平台，跳过 openh264 配置")
        return None

    search_dirs = []

    # 用户显式配置的路径
    cfg_path = (cfg.get("openh264_dll_path") or "").strip()
    if cfg_path:
        try:
            p = Path(cfg_path)
            if p.exists():
                search_dirs.append(str(p if p.is_dir() else p.parent))
        except Exception:
            pass

    # 运行目录优先
    try:
        search_dirs.append(str(BASE_DIR))
    except Exception:
        pass
    # 当前工作目录
    try:
        search_dirs.append(os.getcwd())
    except Exception:
        pass

    # 把目录加入 DLL 搜索路径
    seen = set()
    for d in search_dirs:
        try:
            d = os.path.abspath(d)
        except Exception:
            continue
        if d in seen:
            continue
        seen.add(d)
        cur_path = os.environ.get("PATH", "")
        if d not in cur_path.split(os.pathsep):
            os.environ["PATH"] = d + os.pathsep + cur_path
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(d)
        except Exception:
            pass

    # 1) 优先按精确名称查找
    found = None
    for d in seen:
        for nm in PREFERRED_H264_DLLS:
            try:
                cand = Path(d) / nm
                if cand.exists() and cand.is_file():
                    found = str(cand)
                    break
            except Exception:
                continue
        if found:
            break

    # 2) 回退到通配符
    if not found:
        for d in seen:
            try:
                matches = sorted(Path(d).glob("openh264*.dll"))
            except Exception:
                matches = []
            if matches:
                found = str(matches[0])
                break

    if not found:
        log("[H264] 未找到 openh264 DLL，将使用内置编码器（mp4v/MJPG/XVID）")
        return None

    # 3) 用 ctypes 真正尝试加载
    loaded_ok = False
    load_err = ""
    try:
        ctypes.WinDLL(found)  # 立即加载一次，验证依赖完整
        loaded_ok = True
    except Exception as e:
        load_err = str(e)

    if loaded_ok:
        log(f"[H264] openh264 加载成功: {found}")
    else:
        log(f"[H264] openh264 加载失败: {found} ({load_err})")
        log("[H264] OpenCV 仍可能在编码时按需加载，若失败会自动回退")

    return found


# ============================================================
#              已知摄像头软件 + 进程扫描
# ============================================================
KNOWN_WEBCAM_APPS = {
    "wechat.exe", "weixin.exe", "wechatappex.exe",
    "qq.exe", "tim.exe", "qqprotect.exe",
    "dingtalk.exe", "wemeetapp.exe", "wemeet.exe",
    "feishu.exe", "lark.exe",
    "zoom.exe", "teams.exe", "ms-teams.exe",
    "skype.exe", "skypeapp.exe",
    "discord.exe", "slack.exe", "telegram.exe",
    "obs64.exe", "obs32.exe",
    "streamlabs obs.exe", "streamlabs.exe",
    "bandicam.exe", "camtasia.exe",
    "xsplit.core.exe", "xsplit.broadcaster.exe",
    "amcap.exe", "youcam.exe",
    "droidcam.exe", "ivcam.exe", "epoccam.exe",
    "manycam.exe", "splitcam.exe",
    "windowscamera.exe", "camera.exe", "cameraapp.exe",
    "logitune.exe", "logioptionplus.exe", "logi_camera.exe",
    "microsoft.photos.exe",
    "snippingtool.exe",
}


def get_running_processes():
    if not sys.platform.startswith("win"):
        return set()
    try:
        kwargs = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=3, **kwargs
        )
        procs = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith('"'):
                continue
            end = line.find('","')
            if end > 0:
                procs.add(line[1:end].lower())
        return procs
    except Exception as e:
        log(f"[Proc] tasklist 失败: {e}")
        return set()


# ============================================================
#              摄像头占用检测（注册表）
# ============================================================
class WebcamOccupancyDetector:
    REG_BASE = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion"
        r"\CapabilityAccessManager\ConsentStore\webcam"
    )
    TIMESTAMP_VALUE = "LastUsedTimeStop"

    SYSTEM_WHITELIST = {
        "systemsettings.exe", "svchost.exe", "searchapp.exe", "searchhost.exe",
        "explorer.exe", "dwm.exe", "runtimebroker.exe", "taskmgr.exe",
        "consent.exe", "sihost.exe",
    }

    def __init__(self, my_pids=None, self_keywords=None):
        self.my_pids = set(my_pids or [])
        self.self_keywords = [k.lower() for k in (self_keywords or []) if k]
        self._available = HAS_WINREG and sys.platform.startswith("win")

    def _is_self(self, app):
        pid = app.get("pid")
        if pid is not None and pid in self.my_pids:
            return True
        key = (app.get("path") or "").lower()
        name = (app.get("name") or "").lower()
        for kw in self.self_keywords:
            if kw and (kw in key or kw in name):
                return True
        return False

    def _is_whitelisted(self, app):
        name = (app.get("name") or "").lower()
        path = (app.get("path") or "").lower()
        for w in self.SYSTEM_WHITELIST:
            if w == name or w in path:
                return True
        return False

    def get_active_apps(self):
        if not self._available:
            return []
        apps = []
        try:
            base_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self.REG_BASE, 0, winreg.KEY_READ
            )
        except OSError:
            return []
        try:
            for sub_name in ("NonPackaged", "Packaged"):
                try:
                    sub_key = winreg.OpenKey(base_key, sub_name, 0, winreg.KEY_READ)
                except OSError:
                    continue
                try:
                    apps.extend(self._scan_subkey(sub_key, sub_name))
                finally:
                    winreg.CloseKey(sub_key)
        finally:
            winreg.CloseKey(base_key)
        return apps

    def _scan_subkey(self, parent_key, category):
        results = []
        idx = 0
        while True:
            try:
                child_name = winreg.EnumKey(parent_key, idx)
            except OSError:
                break
            idx += 1
            reg_path = f"{self.REG_BASE}\\{category}\\{child_name}"
            info = self._read_app_info(reg_path, child_name)
            if info is not None:
                results.append(info)
        return results

    def _read_app_info(self, reg_path, app_key_name):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ
            )
        except OSError:
            return None
        try:
            try:
                stop_val, _ = winreg.QueryValueEx(key, self.TIMESTAMP_VALUE)
            except OSError:
                return None
            if stop_val != 0:
                return None

            app_name = app_key_name
            try:
                app_sub = winreg.OpenKey(key, "Application", 0, winreg.KEY_READ)
                try:
                    app_name, _ = winreg.QueryValueEx(app_sub, "DisplayName")
                except OSError:
                    pass
                finally:
                    winreg.CloseKey(app_sub)
            except OSError:
                pass

            pid = None
            if "#" in app_key_name:
                try:
                    pid = int(app_key_name.rsplit("#", 1)[-1])
                except ValueError:
                    pid = None

            return {
                "name": app_name,
                "pid": pid,
                "path": app_key_name.replace("#", "\\"),
            }
        finally:
            winreg.CloseKey(key)

    def get_other_apps(self):
        apps = self.get_active_apps()
        return [a for a in apps
                if not self._is_self(a) and not self._is_whitelisted(a)]


# ============================================================
#                      摄像头工具
# ============================================================
def get_backend_flag(name):
    return {
        "CAP_DSHOW": cv2.CAP_DSHOW,
        "CAP_MSMF": cv2.CAP_MSMF,
        "CAP_ANY": cv2.CAP_ANY,
        "CAP_V4L2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
        "CAP_AVFOUNDATION": getattr(cv2, "CAP_AVFOUNDATION", cv2.CAP_ANY),
    }.get(name, cv2.CAP_ANY)


def list_cameras():
    cams = []
    if HAS_ENUM:
        try:
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
            seen = set()
            for be in backends:
                try:
                    for info in enumerate_cameras(be):
                        key = (info.index, info.name)
                        if key in seen:
                            continue
                        seen.add(key)
                        cams.append({
                            "index": info.index,
                            "name": info.name or f"Camera {info.index}",
                            "backend": "CAP_DSHOW" if be == cv2.CAP_DSHOW else
                                       "CAP_MSMF" if be == cv2.CAP_MSMF else "CAP_ANY",
                            "vid": getattr(info, "vid", 0),
                            "pid": getattr(info, "pid", 0),
                        })
                except Exception:
                    continue
        except Exception as e:
            log(f"[Enum] 失败: {e}")
    if not cams:
        for i in range(10):
            cap = cv2.VideoCapture(i, cv2.CAP_ANY)
            if cap.isOpened():
                cams.append({
                    "index": i, "name": f"Camera {i}",
                    "backend": "CAP_ANY", "vid": 0, "pid": 0,
                })
            cap.release()
    return cams


def measure_actual_fps(cap, sample_seconds=1.5, warmup_frames=5):
    try:
        for _ in range(warmup_frames):
            if not cap.read()[0]:
                return None
        start = time.time()
        count = 0
        while True:
            if time.time() - start >= sample_seconds:
                break
            ret, _ = cap.read()
            if not ret:
                break
            count += 1
        elapsed = time.time() - start
        if elapsed < 0.2 or count < 3:
            return None
        return count / elapsed
    except Exception as e:
        log(f"[FPS] 实测失败: {e}")
        return None


def choose_fps(measured, declared, configured):
    if measured and 1 <= measured <= 120:
        return float(measured), "实测"
    if declared and 1 <= declared <= 240:
        return float(declared), "声明"
    try:
        c = float(configured)
        if 1 <= c <= 240:
            return c, "配置"
    except (TypeError, ValueError):
        pass
    return 30.0, "默认"


def get_codec_candidates(container, prefer_h264):
    c = (container or "mkv").lower()
    if c == "avi":
        return ["MJPG", "XVID"]
    if c == "mkv":
        return ["avc1", "mp4v", "XVID"] if prefer_h264 else ["mp4v", "avc1", "XVID"]
    if c == "ts":
        return ["avc1", "mp4v"] if prefer_h264 else ["mp4v", "avc1"]
    return ["avc1", "mp4v"] if prefer_h264 else ["mp4v", "avc1"]


def create_video_writer(path, width, height, fps, quality=90,
                        prefer_h264=False, container="mkv"):
    codecs = get_codec_candidates(container, prefer_h264)
    if (container or "").lower() == "avi":
        width = width - (width % 2)
        height = height - (height % 2)
    for codec in codecs:
        try:
            fourcc = cv2.VideoWriter_fourcc(*codec)
            writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
            if writer.isOpened():
                try:
                    writer.set(cv2.VIDEOWRITER_PROP_QUALITY, quality)
                except Exception:
                    pass
                return writer, codec
            writer.release()
        except Exception as e:
            log(f"[Writer] {codec} 创建失败: {e}")
    return None, None


# ============================================================
#               帧广播队列（供网页推送使用）
# ============================================================
class FrameBroadcaster:
    __slots__ = ("_q", "_cond", "_frame", "_seq", "_closed")

    def __init__(self, maxsize: int = 2):
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._closed = False

    def put_nowait(self, frame):
        try:
            self._q.put_nowait(frame)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(frame)
            except queue.Full:
                pass

        with self._cond:
            self._frame = frame
            self._seq += 1
            self._cond.notify_all()

    def get_nowait(self):
        return self._q.get_nowait()

    def get(self, block=True, timeout=None):
        return self._q.get(block=block, timeout=timeout)

    def empty(self):
        return self._q.empty()

    def full(self):
        return self._q.full()

    def qsize(self):
        return self._q.qsize()

    def wait_frame(self, last_seq: int, timeout: float = 1.0):
        with self._cond:
            if self._seq == last_seq and not self._closed:
                self._cond.wait(timeout)
            return self._frame, self._seq

    def latest(self):
        with self._cond:
            return self._frame

    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()


# ============================================================
#                       录制核心
# ============================================================
class CameraRecorder:
    STATE_IDLE = "IDLE"
    STATE_STARTING = "STARTING"
    STATE_STREAMING = "STREAMING"
    STATE_RECORDING = "RECORDING"
    STATE_YIELDING = "YIELDING"
    STATE_RECONNECTING = "RECONNECTING"
    STATE_ERROR = "ERROR"

    STATIC_SAMPLE_W = 160
    STATIC_SAMPLE_H = 120

    def __init__(self, config, on_error=None, on_status=None, on_state_change=None):
        self.cfg = config
        self.on_error = on_error or (lambda m: None)
        self.on_status = on_status or (lambda m: None)
        self.on_state_change = on_state_change or (lambda s, d: None)

        self.frame_queue = FrameBroadcaster(maxsize=2)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False
        self._current_file = None
        self._segment_start = None
        self._yield_until = 0.0
        self._state = self.STATE_IDLE
        self._state_detail = ""
        self._consecutive_open_failures = 0

        self._recording_enabled = threading.Event()
        self._recording_enabled.set()

        self.current_measured_fps = None
        self.current_declared_fps = None
        self.current_used_fps = None

    def set_recording_enabled(self, enabled: bool):
        if enabled:
            self._recording_enabled.set()
        else:
            self._recording_enabled.clear()

    def is_recording_enabled(self) -> bool:
        return self._recording_enabled.is_set()

    def _set_state(self, state, detail=""):
        old = self._state
        self._state = state
        self._state_detail = detail
        if old != state:
            try:
                self.on_state_change(state, detail)
            except Exception:
                pass
            log(f"[State] {old} -> {state} ({detail})")

    def get_state(self):
        return self._state

    def get_state_detail(self):
        return self._state_detail

    def is_running(self):
        return self._running and not self._stop_event.is_set()

    def is_actively_recording(self):
        return (self._state == self.STATE_RECORDING
                and self.is_recording_enabled())

    def force_yield(self, duration=30, reason=""):
        self._yield_until = time.time() + duration
        detail = f"让路 {duration}s"
        if reason:
            detail += f" ({reason})"
        self._set_state(self.STATE_YIELDING, detail)
        self.on_status(detail)

    def clear_yield(self):
        self._yield_until = 0.0

    def is_yielding(self):
        return time.time() < self._yield_until

    def get_yield_remaining(self):
        return max(0.0, self._yield_until - time.time())

    def set_yield_remaining(self, seconds):
        if seconds < self.get_yield_remaining():
            self._yield_until = time.time() + seconds

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="CameraRecorder", daemon=True
            )
            self._thread.start()
            self._running = True
            self.on_status("录制线程已启动")
            return True

    def stop(self, timeout=8.0):
        self._stop_event.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)
        self._running = False
        self._set_state(self.STATE_IDLE, "已停止")
        self.on_status("录制线程已停止")

    def get_current_file(self):
        return self._current_file

    def _run(self):
        try:
            self._run_impl()
        except Exception as e:
            log(f"[Recorder] 致命异常: {e}\n{traceback.format_exc()}")
            self._set_state(self.STATE_ERROR, str(e)[:80])
        finally:
            self._running = False
            if self._state != self.STATE_ERROR:
                self._set_state(self.STATE_IDLE, "线程退出")
            self.on_status("录制线程退出")

    def _open_camera(self):
        idx = self.cfg["camera_index"]
        backend = get_backend_flag(self.cfg.get("camera_backend", "CAP_DSHOW"))
        max_retries = int(self.cfg.get("max_retries", 10))
        delay = float(self.cfg.get("retry_interval", 3.0))
        fail_threshold = int(self.cfg.get("open_fail_threshold", 3))
        yield_sec = int(self.cfg.get("occupancy_yield_seconds", 30))

        self._set_state(self.STATE_STARTING, "正在打开摄像头...")

        for attempt in range(1, max_retries + 1):
            if self._stop_event.is_set():
                return None
            if self.is_yielding():
                self._set_state(self.STATE_YIELDING, "让路等待中")
                self._stop_event.wait(timeout=1.0)
                continue

            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                try:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg["width"])
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg["height"])
                    cap.set(cv2.CAP_PROP_FPS, self.cfg["fps"])
                except Exception:
                    pass
                ok, _ = cap.read()
                if ok:
                    self._consecutive_open_failures = 0
                    return cap
                cap.release()

            self._consecutive_open_failures += 1
            self._set_state(
                self.STATE_RECONNECTING,
                f"打开失败 {self._consecutive_open_failures}/{fail_threshold}"
            )
            self.on_status(f"摄像头打开失败 {self._consecutive_open_failures} 次")

            if self._consecutive_open_failures >= fail_threshold:
                self._consecutive_open_failures = 0
                log(f"[Recorder] 连续打开失败 {fail_threshold} 次，让路 {yield_sec}s")
                self.force_yield(duration=yield_sec, reason="打开失败兜底")
                return None

            time.sleep(delay)

        self.on_error("摄像头重试耗尽，无法打开")
        self._set_state(self.STATE_ERROR, "无法打开摄像头")
        return None

    def _make_output_path(self):
        out_dir = Path(self.cfg.get("output_dir") or RECORDING_DIR)
        if out_dir.resolve() != RECORDING_DIR.resolve():
            out_dir = RECORDING_DIR
            self.cfg["output_dir"] = str(RECORDING_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = "." + str(self.cfg.get("container_format", "mkv")).lower()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return out_dir / f"record_{ts}{ext}"

    def _finalize_writer(self, writer, out_path, delete=False):
        if writer is not None:
            try:
                writer.release()
            except Exception:
                pass
        if out_path is None:
            return
        try:
            if not out_path.exists():
                return
        except Exception:
            return

        try:
            size = out_path.stat().st_size
        except Exception:
            size = 0

        if delete:
            try:
                out_path.unlink()
                self.on_status(f"画面静止，删除 {out_path.name}")
            except Exception:
                pass
            return

        min_size = get_min_size_bytes(self.cfg)
        if min_size > 0 and size < min_size:
            try:
                out_path.unlink()
                self.on_status(
                    f"删除小文件 {out_path.name} "
                    f"({size/1024:.0f}KB < {min_size/1024/1024:.1f}MB)"
                )
            except Exception:
                pass
        else:
            self.on_status(
                f"已保存 {out_path.name} ({size/1024/1024:.1f}MB)"
            )

    def _run_impl(self):
        while not self._stop_event.is_set():
            if self.is_yielding():
                self._set_state(self.STATE_YIELDING, "让路中")
                self._stop_event.wait(timeout=0.5)
                continue

            cap = self._open_camera()
            if cap is None:
                if self.is_yielding():
                    continue
                if self._stop_event.wait(timeout=3.0):
                    break
                continue

            real_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.cfg["width"]
            real_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.cfg["height"]
            declared_fps = cap.get(cv2.CAP_PROP_FPS)
            self.current_declared_fps = float(declared_fps) if declared_fps else None

            measured_fps = None
            if bool(self.cfg.get("measure_fps_enabled", True)):
                sample_sec = float(self.cfg.get("measure_fps_seconds", 1.5))
                measured_fps = measure_actual_fps(cap, sample_seconds=sample_sec)
                self.current_measured_fps = measured_fps

            use_fps, source = choose_fps(
                measured_fps, declared_fps, self.cfg.get("fps", 30)
            )
            self.current_used_fps = use_fps
            log(f"[Recorder] 帧率: 实测={measured_fps and f'{measured_fps:.2f}'} "
                f"声明={declared_fps} → 使用 {use_fps:.2f} ({source})")

            reason = "NORMAL"
            try:
                reason = self._stream_loop(cap, real_w, real_h, use_fps)
            except Exception as e:
                log(f"[Recorder] stream_loop 异常: {e}\n{traceback.format_exc()}")
                reason = "ERROR"
            finally:
                try:
                    cap.release()
                except Exception:
                    pass

            if self._stop_event.is_set():
                break
            if reason == "YIELDING":
                continue
            if reason == "NORMAL":
                break
            if reason == "ERROR":
                if self._stop_event.wait(timeout=3.0):
                    break
                continue

            self._set_state(self.STATE_RECONNECTING, "摄像头中断，重连中...")
            if self._stop_event.wait(timeout=2.0):
                break

    def _stream_loop(self, cap, real_w, real_h, use_fps):
        writer = None
        out_path = None
        segment_start = 0.0
        fail_count = 0

        static_enabled = bool(self.cfg.get("static_detection_enabled", True))
        static_timeout = float(self.cfg.get("static_frame_timeout", 30))
        diff_threshold = float(self.cfg.get("static_diff_threshold", 3.0))
        prev_gray = None
        last_change_time = time.time()
        check_counter = 0
        last_static_notice = 0.0
        segment_sec = int(self.cfg.get("segment_minutes", 30)) * 60

        if not self.is_recording_enabled():
            self._set_state(self.STATE_STREAMING, "预览中")

        try:
            while not self._stop_event.is_set():
                if self.is_yielding():
                    self._finalize_writer(writer, out_path)
                    writer = None
                    out_path = None
                    self._set_state(self.STATE_YIELDING, "让路中")
                    return "YIELDING"

                ok, frame = cap.read()
                if not ok or frame is None:
                    fail_count += 1
                    if fail_count >= 5:
                        self._finalize_writer(writer, out_path)
                        writer = None
                        out_path = None
                        self._set_state(self.STATE_RECONNECTING, "连续读帧失败")
                        return "RECONNECT"
                    time.sleep(0.05)
                    continue
                fail_count = 0

                self.frame_queue.put_nowait(frame)

                if self.is_recording_enabled():
                    if writer is None:
                        out_path = self._make_output_path()
                        container = str(self.cfg.get("container_format", "mkv")).lower()
                        writer, codec = create_video_writer(
                            out_path, real_w, real_h, use_fps,
                            quality=int(self.cfg.get("quality", 90)),
                            prefer_h264=bool(self.cfg.get("prefer_h264", False)),
                            container=container,
                        )
                        if writer is None:
                            out_path = None
                            time.sleep(0.5)
                            continue
                        segment_start = time.time()
                        self._current_file = str(out_path)
                        self._set_state(
                            self.STATE_RECORDING,
                            f"{out_path.name} "
                            f"({real_w}x{real_h}@{use_fps:.1f} {container.upper()})"
                        )
                        self.on_status(f"开始录制 {out_path.name}")
                        last_change_time = time.time()
                        prev_gray = None
                        check_counter = 0

                    try:
                        writer.write(frame)
                    except Exception as e:
                        log(f"[Recorder] 写帧异常: {e}")
                        self._finalize_writer(writer, out_path)
                        writer = None
                        out_path = None
                        continue

                    if static_enabled:
                        check_counter += 1
                        if check_counter % 3 == 0:
                            try:
                                small = cv2.resize(
                                    frame,
                                    (self.STATIC_SAMPLE_W, self.STATIC_SAMPLE_H),
                                    interpolation=cv2.INTER_AREA
                                )
                                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                                if prev_gray is not None:
                                    diff = float(cv2.absdiff(gray, prev_gray).mean())
                                    if diff > diff_threshold:
                                        last_change_time = time.time()
                                prev_gray = gray
                            except Exception:
                                pass

                        static_elapsed = time.time() - last_change_time
                        if static_elapsed > static_timeout:
                            self.on_status(
                                f"画面静止 {static_elapsed:.0f}s，刷新录制"
                            )
                            self._finalize_writer(writer, out_path, delete=True)
                            writer = None
                            out_path = None
                            last_change_time = time.time()
                            prev_gray = None
                            continue
                        elif static_elapsed > static_timeout - 3 and \
                                time.time() - last_static_notice > 5:
                            last_static_notice = time.time()
                            self.on_status(
                                f"画面静止，"
                                f"{static_timeout - static_elapsed:.0f}s 后刷新"
                            )

                    if segment_sec > 0 and (time.time() - segment_start) > segment_sec:
                        self._finalize_writer(writer, out_path)
                        writer = None
                        out_path = None
                        self.on_status("达到分段时长，切分文件")
                        continue

                else:
                    if writer is not None:
                        self._finalize_writer(writer, out_path)
                        writer = None
                        out_path = None
                        self.on_status("录制已停止，预览继续")
                    if self._state != self.STATE_STREAMING:
                        self._set_state(self.STATE_STREAMING, "预览中")

            self._finalize_writer(writer, out_path)
            return "NORMAL"

        except Exception as e:
            log(f"[Recorder] _stream_loop 异常: {e}\n{traceback.format_exc()}")
            self._finalize_writer(writer, out_path)
            return "ERROR"


# ============================================================
#              占用监控
# ============================================================
class OccupancyMonitor(threading.Thread):
    def __init__(self, recorder, config, my_pids=None, self_keywords=None,
                 check_interval=2.0):
        super().__init__(daemon=True, name="OccupancyMonitor")
        self.recorder = recorder
        self.cfg = config
        self.check_interval = check_interval
        self.detector = WebcamOccupancyDetector(
            my_pids=my_pids, self_keywords=self_keywords,
        )
        self._stop = threading.Event()
        self._consecutive_busy = 0
        self._known_procs = set(KNOWN_WEBCAM_APPS)
        extra = config.get("extra_webcam_apps", [])
        if isinstance(extra, list):
            for p in extra:
                if p and isinstance(p, str):
                    self._known_procs.add(p.lower())
        self._baseline_procs = None
        self._last_proc_scan = 0.0
        self._proc_scan_interval = float(config.get("process_poll_interval", 2.0))
        self._process_monitor_enabled = bool(
            config.get("process_monitor_enabled", True)
        )

    def _scan_known_running(self):
        running = get_running_processes()
        return running & self._known_procs

    def _detect_new_webcam_procs(self):
        now = time.time()
        if now - self._last_proc_scan < self._proc_scan_interval:
            return set()
        self._last_proc_scan = now
        current = self._scan_known_running()
        if self._baseline_procs is None:
            self._baseline_procs = current
            return set()
        new = current - self._baseline_procs
        self._baseline_procs = current
        return new

    def run(self):
        log("[Occupancy] 占用监控已启动")
        while not self._stop.wait(self.check_interval):
            try:
                self._tick()
            except Exception as e:
                log(f"[Occupancy] 异常: {e}")

    def _tick(self):
        if not self.cfg.get("occupancy_monitor_enabled", True):
            return
        yield_seconds = int(self.cfg.get("occupancy_yield_seconds", 30))
        confirm_count = int(self.cfg.get("occupancy_confirm_count", 2))

        if self.recorder.is_yielding():
            others = self.detector.get_other_apps() if self.detector._available else []
            reg_busy = len(others) > 0
            remain = self.recorder.get_yield_remaining()
            if reg_busy:
                if remain < 5:
                    self.recorder.force_yield(
                        duration=yield_seconds, reason="持续占用"
                    )
            else:
                if remain > 8:
                    self.recorder.set_yield_remaining(5.0)
            return

        new_procs = set()
        if self._process_monitor_enabled:
            new_procs = self._detect_new_webcam_procs()
        if new_procs:
            names = ", ".join(sorted(new_procs))
            self.recorder.force_yield(
                duration=yield_seconds, reason=f"启动 {names}"
            )
            self._consecutive_busy = 0
            return

        others = self.detector.get_other_apps() if self.detector._available else []
        if others:
            self._consecutive_busy += 1
            if self._consecutive_busy >= confirm_count:
                names = ", ".join(a.get("name", "?") for a in others[:3])
                self.recorder.force_yield(
                    duration=yield_seconds, reason=f"占用 {names}"
                )
                self._consecutive_busy = 0
        else:
            self._consecutive_busy = 0

    def stop(self):
        self._stop.set()


# ============================================================
#                   保活守护
# ============================================================
class RecorderSupervisor(threading.Thread):
    def __init__(self, recorder, should_run_getter, check_interval=5.0):
        super().__init__(daemon=True, name="RecorderSupervisor")
        self.recorder = recorder
        self.should_run_getter = should_run_getter
        self.check_interval = check_interval
        self._stop = threading.Event()

    def run(self):
        log("[Supervisor] 保活守护已启动")
        while not self._stop.wait(self.check_interval):
            try:
                if (self.should_run_getter()
                        and not self.recorder.is_running()
                        and self.recorder.get_state() != CameraRecorder.STATE_YIELDING):
                    log("[Supervisor] 检测到录制线程已停止，自动重启")
                    self.recorder.start()
            except Exception as e:
                log(f"[Supervisor] 异常: {e}")

    def stop(self):
        self._stop.set()


# ============================================================
#                     登录验证
# ============================================================
def compute_totp(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now()
    return f"{now.day:02d}{now.hour:02d}{now.minute:02d}"


class AuthManager:
    """明文密码 + 口令的会话验证。"""

    def __init__(self, cfg: dict):
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}

        auth = cfg.get("web_auth") or {}
        username = auth.get("username") or "admin"
        password = auth.get("password") or ""

        if not password:
            password = secrets.token_urlsafe(9)
            cfg["web_auth"] = {"username": username, "password": password}
            try:
                save_config(cfg)
            except Exception:
                pass

            log("=" * 64)
            log("[Web] 首次运行，已生成网页登录凭证：")
            log(f"[Web]   用户名 : {username}")
            log(f"[Web]   密码   : {password}")
            log("[Web]   凭证已明文保存在 config.json 的 web_auth 字段，")
            log("[Web]   可直接打开文件查看或修改。")
            log("=" * 64)

        self.username = username
        self.password = password

    def verify(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        u_ok = hmac.compare_digest(str(username), str(self.username))
        p_ok = hmac.compare_digest(str(password), str(self.password))
        return u_ok and p_ok

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        expires = time.time() + SESSION_HOURS * 3600
        now = time.time()
        with self._lock:
            for t in [k for k, v in self._sessions.items() if v < now]:
                self._sessions.pop(t, None)
            self._sessions[token] = expires
        return token

    def check_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            exp = self._sessions.get(token)
            if exp is None:
                return False
            if exp < time.time():
                self._sessions.pop(token, None)
                return False
            return True

    def destroy_session(self, token: str | None):
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)


# ============================================================
#                      工具函数
# ============================================================
def _html_escape(s: str) -> str:
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def _get_lan_ip() -> str:
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        ip = ""
    if ip and not ip.startswith("127."):
        return ip
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = info[4][0]
            if addr and not addr.startswith("127.") and ":" not in addr:
                return addr
    except Exception:
        pass
    return "127.0.0.1"


def _format_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _format_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _safe_filename(name: str) -> str | None:
    if not name or not isinstance(name, str):
        return None
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        return None
    if name != os.path.basename(name):
        return None
    if not name.lower().endswith(VIDEO_EXTS):
        return None
    return name


def _open_in_explorer(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except Exception as e:
        log(f"[Open] 失败: {e}")


_PLACEHOLDER_CACHE: dict = {}


def _make_placeholder(width: int = 1280, height: int = 720) -> bytes:
    key = (width, height)
    hit = _PLACEHOLDER_CACHE.get(key)
    if hit is not None:
        return hit
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (16, 18, 24)
    step, color = 48, (28, 32, 42)
    for x in range(0, width, step):
        cv2.line(img, (x, 0), (x, height), color, 1)
    for y in range(0, height, step):
        cv2.line(img, (0, y), (width, y), color, 1)
    text, scale, thick = "NO SIGNAL", 1.5, 3
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    cv2.putText(
        img, text, ((width - tw) // 2, (height + th) // 2),
        cv2.FONT_HERSHEY_SIMPLEX, scale, (105, 115, 135), thick, cv2.LINE_AA,
    )
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    data = buf.tobytes() if ok else b""
    _PLACEHOLDER_CACHE[key] = data
    return data


# ============================================================
#          网页模板（外部 HTML 文件，脚本同级目录加载）
# ============================================================
def _load_html_file(filename: str) -> str:
    """从脚本同级目录加载 HTML 模板文件。

    如果文件缺失或读取失败，返回一个简短的占位页面，
    保证主程序其余功能不受影响。
    """
    try:
        path = BASE_DIR / filename
        if path.exists() and path.is_file():
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        log(f"[HTML] 未找到模板文件: {filename}")
    except Exception as e:
        log(f"[HTML] 读取模板失败 {filename}: {e}")
    safe_name = (
        str(filename).replace("&", "&amp;")
        .replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>模板缺失</title></head>"
        "<body style='padding:40px;font-family:sans-serif;"
        "color:#222;background:#fff'>"
        "<h1>页面模板缺失</h1>"
        f"<p>请确认 <code>{safe_name}</code> 与本程序位于同一目录。</p>"
        "</body></html>"
    )


LOGIN_HTML    = _load_html_file("login.html")
INDEX_HTML    = _load_html_file("preview.html")
SETTINGS_HTML = _load_html_file("settings.html")
FILES_HTML    = _load_html_file("files.html")
LOGS_HTML     = _load_html_file("logs.html")


def _render_login(error: str = "") -> bytes:
    err_block = f'<div class="err">{_html_escape(error)}</div>' if error else ""
    html = LOGIN_HTML.replace("<!--ERR-->", err_block)
    return html.encode("utf-8")


# ============================================================
#                     HTTP 服务
# ============================================================
class _PreviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    app = None


class _PreviewHandler(BaseHTTPRequestHandler):
    server_version = "CamMonitor/6.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    @property
    def app(self):
        return self.server.app

    # --------------------------------------------------------
    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        query = parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                self._handle_index()
            elif path == "/stream":
                self._handle_stream()
            elif path == "/snapshot.jpg":
                self._handle_snapshot()
            elif path == "/status":
                self._handle_status()
            elif path == "/login":
                self._handle_login_get()
            elif path == "/logout":
                self._handle_logout()
            elif path == "/settings":
                self._handle_settings_page()
            elif path == "/files":
                self._handle_files_page()
            elif path == "/logs":
                self._handle_logs_page()
            elif path == "/api/cameras":
                self._handle_api_cameras()
            elif path == "/api/settings":
                self._handle_api_settings_get()
            elif path == "/api/files":
                self._handle_api_files_list()
            elif path == "/api/files/download":
                self._handle_api_files_download(query)
            elif path == "/api/logs/files":
                self._handle_api_logs_files()
            elif path == "/api/logs":
                self._handle_api_logs_read(query)
            elif path == "/favicon.ico":
                self._handle_favicon()
            else:
                self._send_error(404, "Not Found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log(f"[Web] 处理 {path} 异常: {e}")

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if path == "/login":
                self._handle_login_post()
            elif path == "/api/settings":
                self._handle_api_settings_post()
            elif path == "/api/action":
                self._handle_api_action_post()
            elif path == "/api/recording":
                self._handle_api_recording_post()
            elif path == "/api/files/delete":
                self._handle_api_files_delete()
            elif path == "/api/files/delete_all":
                self._handle_api_files_delete_all()
            elif path == "/api/logs/delete":
                self._handle_api_logs_delete()
            elif path == "/api/logs/delete_all":
                self._handle_api_logs_delete_all()
            else:
                self._send_error(404, "Not Found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log(f"[Web] 处理 POST {path} 异常: {e}")

    def do_HEAD(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            data = INDEX_HTML.encode("utf-8")
            self._send_headers("text/html; charset=utf-8", len(data))
        else:
            self._send_error(404, "Not Found")

    # --------------------------------------------------------
    def _send_headers(self, ctype, length, extra=None, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()

    def _send_bytes(self, data: bytes, ctype: str, extra=None):
        self._send_headers(ctype, len(data), extra=extra)
        if data:
            self.wfile.write(data)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code, msg):
        self._send_json({"error": msg, "code": code}, status=code)

    def _redirect(self, location: str, extra_headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()

    def _read_body(self, max_size: int = 64 * 1024) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return b""
        if length <= 0 or length > max_size:
            return b""
        try:
            return self.rfile.read(length)
        except Exception:
            return b""

    # --------------------------------------------------------
    #                   登录 / 会话
    # --------------------------------------------------------
    def _get_token(self):
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        try:
            cookie = SimpleCookie()
            cookie.load(raw)
            morsel = cookie.get(COOKIE_NAME)
            if morsel:
                return morsel.value
        except Exception:
            pass
        return None

    def _is_authed(self) -> bool:
        return self.app.auth.check_session(self._get_token())

    def _require_auth_redirect(self) -> bool:
        if self._is_authed():
            return False
        self._redirect("/login")
        return True

    def _require_auth_json(self) -> bool:
        if self._is_authed():
            return False
        self._send_error(401, "Unauthorized")
        return True

    # --------------------------------------------------------
    def _handle_index(self):
        if self._require_auth_redirect():
            return
        self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")

    def _handle_settings_page(self):
        if self._require_auth_redirect():
            return
        self._send_bytes(SETTINGS_HTML.encode("utf-8"), "text/html; charset=utf-8")

    def _handle_files_page(self):
        if self._require_auth_redirect():
            return
        self._send_bytes(FILES_HTML.encode("utf-8"), "text/html; charset=utf-8")

    def _handle_logs_page(self):
        if self._require_auth_redirect():
            return
        self._send_bytes(LOGS_HTML.encode("utf-8"), "text/html; charset=utf-8")

    def _handle_login_get(self):
        if self._is_authed():
            self._redirect("/")
            return
        self._send_bytes(_render_login(""), "text/html; charset=utf-8")

    def _handle_login_post(self):
        body = self._read_body(8 * 1024)
        try:
            form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        except Exception:
            form = {}
        username = (form.get("username") or [""])[0]
        password = (form.get("password") or [""])[0]
        totp_input = (form.get("totp") or [""])[0].strip()

        expected_totp = compute_totp()
        cred_ok = self.app.auth.verify(username, password)
        totp_ok = hmac.compare_digest(totp_input, expected_totp)

        if not cred_ok or not totp_ok:
            time.sleep(0.6)
            if not cred_ok:
                log(f"[Web] 登录失败（凭证错误）：用户={username!r} "
                    f"来源={self.client_address[0]}")
                msg = "用户名或密码不正确"
            else:
                log(f"[Web] 登录失败（口令错误）：用户={username!r} "
                    f"来源={self.client_address[0]}")
                msg = "口令不正确"
            self._send_bytes(_render_login(msg), "text/html; charset=utf-8")
            return

        token = self.app.auth.create_session()
        log(f"[Web] 登录成功：用户={username!r} 来源={self.client_address[0]}")
        cookie = (
            f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_HOURS * 3600}; "
            f"HttpOnly; SameSite=Lax"
        )
        self._redirect("/", extra_headers=[("Set-Cookie", cookie)])

    def _handle_logout(self):
        try:
            self.app.auth.destroy_session(self._get_token())
        except Exception:
            pass
        self._redirect(
            "/login",
            extra_headers=[(
                "Set-Cookie",
                f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
            )],
        )

    # --------------------------------------------------------
    #                    API 路由
    # --------------------------------------------------------
    def _handle_api_cameras(self):
        if self._require_auth_json():
            return
        try:
            cams = list_cameras() or []
        except Exception as e:
            log(f"[Web] 枚举摄像头失败: {e}")
            cams = []
        simplified = []
        for c in cams:
            try:
                simplified.append({
                    "index": int(c.get("index", 0)),
                    "name": str(c.get("name") or f"Camera {c.get('index', 0)}"),
                    "backend": str(c.get("backend") or ""),
                })
            except Exception:
                continue
        self._send_json({"cameras": simplified})

    def _handle_api_settings_get(self):
        if self._require_auth_json():
            return
        cfg = self.app.core_app.cfg if self.app.core_app else {}
        keys = (
            "camera_index", "camera_backend",
            "width", "height", "fps", "quality",
            "segment_minutes", "container_format",
            "min_file_size_mb", "retry_interval", "max_retries",
            "static_detection_enabled", "static_frame_timeout",
            "static_diff_threshold",
            "occupancy_monitor_enabled", "process_monitor_enabled",
            "occupancy_yield_seconds", "occupancy_confirm_count",
            "open_fail_threshold", "occupancy_poll_interval",
            "process_poll_interval",
            "prefer_h264", "openh264_dll_path",
            "measure_fps_enabled",
        )
        self._send_json({k: cfg.get(k) for k in keys})

    def _handle_api_settings_post(self):
        if self._require_auth_json():
            return
        body = self._read_body()
        if not body:
            self._send_error(400, "Empty Body")
            return
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            self._send_error(400, "Bad JSON")
            return
        if not isinstance(data, dict):
            self._send_error(400, "Bad Data")
            return
        try:
            restarted = self.app.apply_settings(data)
        except Exception as e:
            log(f"[Web] 应用设置失败: {e}")
            self._send_error(500, "Apply Failed")
            return
        self._send_json({"ok": True, "restarted": bool(restarted)})

    def _handle_api_action_post(self):
        if self._require_auth_json():
            return
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8", "replace")) if body else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        action = str(data.get("action") or "").strip()
        self._send_json(self.app.run_action(action))

    def _handle_api_recording_post(self):
        if self._require_auth_json():
            return
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8", "replace")) if body else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        action = str(data.get("action") or "").strip().lower()
        result = self.app.toggle_recording(action)
        self._send_json(result)

    # ---------- 文件管理 ----------
    def _handle_api_files_list(self):
        if self._require_auth_json():
            return
        self._send_json({"files": self.app.scan_recordings()})

    def _handle_api_files_delete(self):
        if self._require_auth_json():
            return
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8", "replace")) if body else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        name = str(data.get("name") or "").strip()
        self._send_json(self.app.delete_recording(name))

    def _handle_api_files_delete_all(self):
        if self._require_auth_json():
            return
        self._send_json(self.app.delete_all_recordings())

    def _handle_api_files_download(self, query):
        if self._require_auth_json():
            return
        try:
            name = (query.get("name") or [""])[0]
        except Exception:
            name = ""
        name = unquote(name)
        safe = _safe_filename(name)
        if not safe:
            self._send_error(400, "Bad Name")
            return
        try:
            target = (RECORDING_DIR / safe).resolve()
            if not str(target).startswith(str(RECORDING_DIR.resolve())):
                self._send_error(403, "Forbidden")
                return
            if not target.exists() or not target.is_file():
                self._send_error(404, "Not Found")
                return
            size = target.stat().st_size
        except Exception:
            self._send_error(500, "Stat Failed")
            return

        range_header = self.headers.get("Range")
        start, end, status = 0, size - 1, 200
        if range_header:
            try:
                rng = range_header.strip().replace("bytes=", "")
                parts = rng.split("-", 1)
                if parts[0]:
                    start = int(parts[0])
                if len(parts) > 1 and parts[1]:
                    end = int(parts[1])
                if start < 0:
                    start = 0
                if end >= size:
                    end = size - 1
                if start > end:
                    start, end = 0, size - 1
                status = 206
            except Exception:
                start, end, status = 0, size - 1, 200

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{safe}"',
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        try:
            with open(target, "rb") as f:
                f.seek(start)
                remaining = length
                chunk_size = 64 * 1024
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log(f"[Web] 下载失败: {e}")

    # ---------- 日志 ----------
    def _handle_api_logs_files(self):
        if self._require_auth_json():
            return
        self._send_json({"files": get_log_files()})

    def _handle_api_logs_read(self, query):
        if self._require_auth_json():
            return
        try:
            name = (query.get("name") or [""])[0]
        except Exception:
            name = ""
        name = unquote(name)
        lines = read_log_file(name)
        if lines is None:
            self._send_error(404, "Log Not Found")
            return
        self._send_json({
            "name": name,
            "lines": lines,
            "size": len("\n".join(lines)),
        })

    def _handle_api_logs_delete(self):
        if self._require_auth_json():
            return
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8", "replace")) if body else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        name = str(data.get("name") or "").strip()
        self._send_json(delete_log_file(name))

    def _handle_api_logs_delete_all(self):
        if self._require_auth_json():
            return
        self._send_json(delete_all_logs())

    # --------------------------------------------------------
    #                   预览
    # --------------------------------------------------------
    def _handle_status(self):
        if self._require_auth_json():
            return
        self._send_json(self.app.status_payload())

    def _handle_snapshot(self):
        if self._require_auth_json():
            return
        frame = self.app.broadcaster.latest()
        if frame is None:
            self._send_error(503, "No Frame")
            return
        data = self.app.encode_snapshot(frame)
        if not data:
            self._send_error(500, "Encode Failed")
            return
        self._send_bytes(
            data, "image/jpeg",
            extra={"Content-Disposition": 'inline; filename="snapshot.jpg"'},
        )

    def _handle_favicon(self):
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        self._send_bytes(png, "image/png",
                         extra={"Cache-Control": "max-age=86400"})

    # --------------------------------------------------------
    def _write_part(self, data: bytes) -> bool:
        try:
            self.wfile.write(b"--frameboundary\r\n")
            self.wfile.write(b"Content-Type: image/jpeg\r\n")
            self.wfile.write(b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n")
            self.wfile.write(data)
            self.wfile.write(b"\r\n")
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _handle_stream(self):
        if not self._is_authed():
            self._send_error(401, "Unauthorized")
            return

        app = self.app
        self.send_response(200)
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=frameboundary"
        )
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        with app._clients_lock:
            app._clients += 1

        last_seq = -1
        last_sent = 0.0
        placeholder = None

        try:
            while not app._stopped.is_set():
                if not app.auth.check_session(self._get_token()):
                    break

                frame, seq = app.broadcaster.wait_frame(last_seq, timeout=1.0)
                now = time.time()

                if frame is None or seq == last_seq:
                    if placeholder is None:
                        placeholder = _make_placeholder()
                    if now - last_sent >= 1.0:
                        if not self._write_part(placeholder):
                            break
                        last_sent = now
                    continue

                if now - last_sent < app.frame_interval:
                    last_seq = seq
                    continue

                data = app.encode_stream_jpeg(frame, seq)
                last_seq = seq
                if not data:
                    continue
                if not self._write_part(data):
                    break
                last_sent = now

        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as e:
            log(f"[Web] 推流异常: {e}")
        finally:
            with app._clients_lock:
                app._clients = max(0, app._clients - 1)


# ============================================================
#                    网页服务封装
# ============================================================
class LanPreviewServer(threading.Thread):
    _CAMERA_KEYS = {"camera_index", "camera_backend", "width", "height", "fps"}
    _INT_KEYS = {
        "camera_index", "width", "height", "quality", "segment_minutes",
        "max_retries", "occupancy_yield_seconds", "occupancy_confirm_count",
        "open_fail_threshold",
    }
    _FLOAT_KEYS = {
        "fps", "min_file_size_mb", "retry_interval",
        "static_frame_timeout", "static_diff_threshold",
        "occupancy_poll_interval", "process_poll_interval",
    }
    _BOOL_KEYS = {
        "static_detection_enabled", "occupancy_monitor_enabled",
        "process_monitor_enabled", "measure_fps_enabled", "prefer_h264",
    }
    _STR_KEYS = {
        "container_format", "openh264_dll_path",
    }
    _FORBIDDEN_KEYS = {"output_dir"}

    def __init__(self, broadcaster, core_app,
                 host=WEB_HOST, port=WEB_PORT,
                 stream_max_width=STREAM_MAX_WIDTH,
                 stream_fps=STREAM_FPS,
                 stream_quality=STREAM_QUALITY,
                 snapshot_quality=SNAPSHOT_QUALITY):
        super().__init__(daemon=True, name="LanPreviewServer")
        self.broadcaster = broadcaster
        self.core_app = core_app
        self.host = host
        self.port = int(port)
        self.actual_port = int(port)
        self.stream_max_width = int(stream_max_width)
        self.frame_interval = 1.0 / max(1.0, float(stream_fps))
        self.stream_quality = int(stream_quality)
        self.snapshot_quality = int(snapshot_quality)

        self.lan_ip = _get_lan_ip()
        self.url = ""
        self.started_ok = False

        self._httpd = None
        self._stopped = threading.Event()
        self._enc_lock = threading.Lock()
        self._enc_seq = -1
        self._enc_buf = None

        self._clients = 0
        self._clients_lock = threading.Lock()

        self.auth = AuthManager(core_app.cfg if core_app else {})

    def start(self) -> bool:
        if self._httpd is not None:
            return self.started_ok

        httpd = None
        for offset in range(10):
            try:
                httpd = _PreviewHTTPServer(
                    (self.host, self.port + offset), _PreviewHandler
                )
                self.actual_port = self.port + offset
                break
            except OSError:
                continue

        if httpd is None:
            log(f"[Web] 端口 {self.port}~{self.port + 9} 全部被占用，"
                f"局域网预览未启动")
            return False

        httpd.app = self
        self._httpd = httpd
        self.url = f"http://{self.lan_ip}:{self.actual_port}"
        self.started_ok = True
        super().start()
        return True

    def run(self):
        log(f"[Web] 局域网预览地址: {self.url}")
        log(f"[Web] 本机预览地址  : http://127.0.0.1:{self.actual_port}")
        try:
            self._httpd.serve_forever(poll_interval=0.5)
        except Exception as e:
            log(f"[Web] 服务异常退出: {e}")
        finally:
            try:
                self._httpd.server_close()
            except Exception:
                pass
            log("[Web] 服务已停止")

    def stop(self):
        self._stopped.set()
        try:
            self.broadcaster.close()
        except Exception:
            pass
        httpd = self._httpd
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass

    def encode_stream_jpeg(self, frame, seq: int):
        with self._enc_lock:
            if seq == self._enc_seq and self._enc_buf is not None:
                return self._enc_buf

            img = frame
            if self.stream_max_width > 0:
                h, w = img.shape[:2]
                if w > self.stream_max_width:
                    scale = self.stream_max_width / float(w)
                    img = cv2.resize(
                        img,
                        (max(2, int(w * scale)), max(2, int(h * scale))),
                        interpolation=cv2.INTER_AREA,
                    )

            ok, buf = cv2.imencode(
                ".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), self.stream_quality]
            )
            if not ok:
                return None
            data = buf.tobytes()
            self._enc_seq = seq
            self._enc_buf = data
            return data

    def encode_snapshot(self, frame):
        try:
            ok, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.snapshot_quality]
            )
            return buf.tobytes() if ok else None
        except Exception:
            return None

    def status_payload(self):
        rec = getattr(self.core_app, "recorder", None) if self.core_app else None
        state, fps, filename = "UNKNOWN", None, ""
        if rec is not None:
            try: state = rec.get_state()
            except Exception: pass
            try: fps = rec.current_used_fps
            except Exception: pass
            try:
                cur = rec.get_current_file()
                filename = os.path.basename(cur) if cur else ""
            except Exception: pass

        frame = self.broadcaster.latest()
        h, w = frame.shape[:2] if frame is not None else (0, 0)
        with self._clients_lock:
            clients = self._clients

        recording_running = bool(rec and rec.is_recording_enabled())

        return {
            "state": state,
            "width": int(w), "height": int(h),
            "fps": float(fps) if fps else None,
            "clients": clients,
            "file": filename,
            "seq": self.broadcaster.seq,
            "recording_running": recording_running,
        }

    def apply_settings(self, data: dict) -> bool:
        app = self.core_app
        if app is None or not isinstance(getattr(app, "cfg", None), dict):
            return False

        cfg = app.cfg
        need_restart = False

        for k, v in data.items():
            if k in self._FORBIDDEN_KEYS:
                continue
            if k not in cfg:
                continue
            try:
                if k in self._BOOL_KEYS:
                    v = bool(v)
                elif k in self._INT_KEYS:
                    v = int(v)
                elif k in self._FLOAT_KEYS:
                    v = float(v)
                elif k in self._STR_KEYS:
                    v = str(v)
            except (TypeError, ValueError):
                continue

            if cfg.get(k) != v:
                if k in self._CAMERA_KEYS:
                    need_restart = True
                cfg[k] = v

        cfg["output_dir"] = str(RECORDING_DIR)

        try:
            save_config(cfg)
        except Exception as e:
            log(f"[Web] 保存配置失败: {e}")

        if need_restart and hasattr(app, "ensure_recording"):
            def _restart():
                try:
                    app.ensure_recording(restart=True)
                    log("[Web] 录制线程已按新参数重启")
                except Exception as e:
                    log(f"[Web] 重启录制线程失败: {e}")
            threading.Thread(target=_restart, daemon=True).start()

        return need_restart

    def toggle_recording(self, action: str) -> dict:
        app = self.core_app
        if app is None:
            return {"ok": False, "error": "no app"}

        rec = app.recorder
        if action == "start":
            try:
                rec.set_recording_enabled(True)
                log("[Web] 录制已开启")
                return {
                    "ok": True, "action": "start",
                    "running": rec.is_recording_enabled(),
                }
            except Exception as e:
                log(f"[Web] 开启录制失败: {e}")
                return {"ok": False, "error": str(e),
                        "running": rec.is_recording_enabled()}

        if action == "stop":
            try:
                rec.set_recording_enabled(False)
                log("[Web] 录制已关闭（预览继续）")
                return {
                    "ok": True, "action": "stop",
                    "running": rec.is_recording_enabled(),
                }
            except Exception as e:
                log(f"[Web] 关闭录制失败: {e}")
                return {"ok": False, "error": str(e),
                        "running": rec.is_recording_enabled()}

        if action == "status":
            return {
                "ok": True, "action": "status",
                "running": rec.is_recording_enabled(),
            }

        return {"ok": False, "error": "unknown action"}

    def run_action(self, action: str) -> dict:
        app = self.core_app
        if app is None:
            return {"ok": False, "error": "no app"}

        if action == "clean_small":
            try:
                n = clean_small_videos(
                    RECORDING_DIR,
                    get_min_size_bytes(app.cfg),
                )
                return {"ok": True, "action": action, "removed": int(n or 0)}
            except Exception as e:
                return {"ok": False, "action": action, "error": str(e)}

        if action == "clean_logs":
            try:
                res = delete_all_logs()
                return {"ok": bool(res.get("ok")),
                        "action": action,
                        "removed": int(res.get("removed") or 0),
                        "error": res.get("error")}
            except Exception as e:
                return {"ok": False, "action": action, "error": str(e)}

        if action == "restart_recording":
            try:
                def _restart():
                    try:
                        app.ensure_recording(restart=True)
                    except Exception as e:
                        log(f"[Web] 重启录制失败: {e}")
                threading.Thread(target=_restart, daemon=True).start()
                return {"ok": True, "action": action}
            except Exception as e:
                return {"ok": False, "action": action, "error": str(e)}

        if action == "refresh_cameras":
            return {"ok": True, "action": action}

        return {"ok": False, "error": "unknown action"}

    def scan_recordings(self) -> list:
        result = []
        try:
            if not RECORDING_DIR.exists():
                return result
            for f in RECORDING_DIR.iterdir():
                if not f.is_file():
                    continue
                if f.suffix.lower() not in VIDEO_EXTS:
                    continue
                try:
                    st = f.stat()
                except Exception:
                    continue
                result.append({
                    "name": f.name,
                    "size": int(st.st_size),
                    "size_text": _format_size(st.st_size),
                    "mtime": float(st.st_mtime),
                    "mtime_text": _format_time(st.st_mtime),
                })
        except Exception as e:
            log(f"[Web] 扫描录制目录失败: {e}")
        result.sort(key=lambda x: x["mtime"], reverse=True)
        return result

    def delete_recording(self, name: str) -> dict:
        safe = _safe_filename(name)
        if not safe:
            return {"ok": False, "error": "无效的文件名"}
        try:
            target = (RECORDING_DIR / safe).resolve()
            if not str(target).startswith(str(RECORDING_DIR.resolve())):
                return {"ok": False, "error": "越权访问"}
        except Exception:
            return {"ok": False, "error": "路径解析失败"}

        if not target.exists() or not target.is_file():
            return {"ok": False, "error": "文件不存在"}

        try:
            rec = getattr(self.core_app, "recorder", None)
            cur = rec.get_current_file() if rec else None
            if cur:
                try:
                    if Path(cur).resolve() == target:
                        return {"ok": False, "error": "文件正在录制中，无法删除"}
                except Exception:
                    pass
        except Exception:
            pass

        try:
            target.unlink()
            log(f"[Web] 删除文件 {safe}")
            return {"ok": True, "name": safe}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def delete_all_recordings(self) -> dict:
        cur_resolved = None
        try:
            rec = getattr(self.core_app, "recorder", None)
            cur = rec.get_current_file() if rec else None
            if cur:
                cur_resolved = Path(cur).resolve()
        except Exception:
            cur_resolved = None

        removed = 0
        errors = []
        try:
            if not RECORDING_DIR.exists():
                return {"ok": True, "removed": 0}
            for f in list(RECORDING_DIR.iterdir()):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in VIDEO_EXTS:
                    continue
                try:
                    fr = f.resolve()
                    if cur_resolved and fr == cur_resolved:
                        continue
                    f.unlink()
                    removed += 1
                except Exception as e:
                    errors.append(f"{f.name}: {e}")
        except Exception as e:
            return {"ok": False, "error": str(e)}

        log(f"[Web] 批量删除文件 {removed} 个")
        return {"ok": True, "removed": removed, "errors": errors}


# ============================================================
#                       应用主类
# ============================================================
class CameraMonitorApp:
    def __init__(self):
        try:
            RECORDING_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        self.cfg = load_config()
        self.recorder = CameraRecorder(
            self.cfg,
            on_error=self._on_error,
            on_status=self._on_status,
            on_state_change=self._on_state_change,
        )
        self.broadcaster: FrameBroadcaster = self.recorder.frame_queue

        self.tray_icon = None
        self.web_server: LanPreviewServer | None = None
        self.web_url = ""
        self.local_url = ""

        self._status_text = "就绪"
        self._quitting = False
        self._should_run = False
        self._supervisor: RecorderSupervisor | None = None
        self._occupancy_monitor: OccupancyMonitor | None = None

    def _collect_self_pids(self):
        pids = [os.getpid()]
        try:
            import multiprocessing
            for child in multiprocessing.active_children():
                pids.append(child.pid)
        except Exception:
            pass
        return pids

    def _collect_self_keywords(self):
        kws = set()
        try:
            if sys.executable:
                kws.add(Path(sys.executable).stem.lower())
                kws.add(Path(sys.executable).name.lower())
        except Exception:
            pass
        kws.add("python.exe")
        kws.add("pythonw.exe")
        try:
            script_path = os.path.abspath(__file__)
            kws.add(Path(script_path).stem.lower())
            kws.add(Path(script_path).parent.name.lower())
        except Exception:
            pass
        return [k for k in kws if k]

    def ensure_recording(self, restart=False):
        self._should_run = True
        try:
            if restart and self.recorder.is_running():
                log("[App] 关键参数变化，重启录制线程")
                self.recorder.stop(timeout=3.0)
                time.sleep(0.3)
            if not self.recorder.is_running():
                log("[App] 启动录制线程")
                self.recorder.start()
        except Exception as e:
            log(f"[App] ensure_recording 异常: {e}")

    def _start_web_server(self) -> bool:
        try:
            server = LanPreviewServer(
                broadcaster=self.broadcaster,
                core_app=self,
                host=WEB_HOST,
                port=WEB_PORT,
                stream_max_width=STREAM_MAX_WIDTH,
                stream_fps=STREAM_FPS,
                stream_quality=STREAM_QUALITY,
                snapshot_quality=SNAPSHOT_QUALITY,
            )
            if not server.start():
                log("[Web] 局域网预览启动失败")
                return False
            self.web_server = server
            self.web_url = server.url
            self.local_url = f"http://127.0.0.1:{server.actual_port}"
            return True
        except Exception as e:
            log(f"[Web] 启动异常: {e}\n{traceback.format_exc()}")
            return False

    def run(self):
        if not (HAS_PIL and HAS_TRAY):
            log("缺少依赖：请安装 Pillow 与 pystray")
            return

        if HAS_COM:
            try: pythoncom.CoInitialize()
            except Exception: pass

        try:
            setup_openh264(self.cfg)
        except Exception as e:
            log(f"[H264] 配置失败: {e}")

        try:
            clean_small_videos(RECORDING_DIR, get_min_size_bytes(self.cfg))
        except Exception as e:
            log(f"[Clean] 启动清理失败: {e}")

        web_ok = self._start_web_server()

        log("")
        log("=" * 64)
        log(f"  录制目录  : {RECORDING_DIR}")
        if web_ok and self.web_server:
            log(f"  局域网访问: {self.web_url}")
            log(f"  本机访问  : {self.local_url}")
        else:
            log("  [警告] 局域网预览未能启动")
        log("=" * 64)
        log("")

        try:
            self._create_tray()
            if self.tray_icon:
                self.tray_icon.run_detached()
                log("[Tray] 托盘已启动")
        except Exception as e:
            log(f"[Tray] 启动失败: {e}")

        if self.cfg.get("keep_alive", True):
            self._supervisor = RecorderSupervisor(
                self.recorder, lambda: self._should_run, check_interval=5.0
            )
            self._supervisor.start()

        if self.cfg.get("occupancy_monitor_enabled", True):
            self._occupancy_monitor = OccupancyMonitor(
                self.recorder, self.cfg,
                my_pids=self._collect_self_pids(),
                self_keywords=self._collect_self_keywords(),
                check_interval=float(self.cfg.get("occupancy_poll_interval", 2.0)),
            )
            self._occupancy_monitor.start()

        self._should_run = True
        self.recorder.start()
        log("[App] 录制线程已启动（默认开启录制）")

        try:
            while not self._quitting:
                time.sleep(0.5)
        except KeyboardInterrupt:
            log("[App] 收到 Ctrl+C")

        self._cleanup()

    def _cleanup(self):
        if getattr(self, "_cleaned_up", False):
            return
        self._cleaned_up = True
        log("[App] 开始清理")

        try: self.recorder.stop(timeout=3.0)
        except Exception: pass
        for t in (self._supervisor, self._occupancy_monitor):
            try:
                if t: t.stop()
            except Exception: pass
        try:
            if self.web_server: self.web_server.stop()
        except Exception: pass
        try:
            if self.tray_icon: self.tray_icon.stop()
        except Exception: pass

        if HAS_COM:
            try: pythoncom.CoUninitialize()
            except Exception: pass
        log("[App] 清理完成")

    # --------------------------------------------------------
    #                         托盘
    # --------------------------------------------------------
    def _make_icon_image(self, color):
        try:
            if not HAS_PIL:
                return None
            return create_app_icon(64, dot_color=color)
        except Exception:
            return None

    def _create_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("复制 Web 地址", self._tray_copy_web, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )
        self.tray_icon = pystray.Icon(
            "CameraMonitor",
            self._make_icon_image("#e74c3c"),
            "摄像头监控",
            menu,
        )

    def _tray_copy_web(self, icon, item):
        url = self.web_url or self.local_url
        if not url:
            log("[Tray] Web 地址不可用")
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.run(
                    f'echo {url}| clip', shell=True, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            elif sys.platform == "darwin":
                subprocess.run("pbcopy", input=url.encode("utf-8"),
                               check=False)
            else:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=url.encode("utf-8"), check=False,
                )
        except Exception:
            pass
        log(f"[Tray] Web 地址已复制：{url}")

    def _quit(self, icon, item):
        if self._quitting:
            return
        log("[App] 用户从托盘请求退出")
        self._quitting = True
        self._should_run = False
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass

    def _update_tray_state(self, state):
        if not self.tray_icon:
            return
        rec = self.recorder
        try:
            if state == rec.STATE_RECORDING:
                self.tray_icon.title = "摄像头监控 - 录制中"
                self.tray_icon.icon = self._make_icon_image("#e74c3c")
            elif state == rec.STATE_STREAMING:
                self.tray_icon.title = "摄像头监控 - 预览中"
                self.tray_icon.icon = self._make_icon_image("#3498db")
            elif state == rec.STATE_YIELDING:
                self.tray_icon.title = "摄像头监控 - 让路中"
                self.tray_icon.icon = self._make_icon_image("#3498db")
            elif state == rec.STATE_ERROR:
                self.tray_icon.title = "摄像头监控 - 错误"
                self.tray_icon.icon = self._make_icon_image("#e74c3c")
            else:
                self.tray_icon.title = "摄像头监控 - 已停止"
                self.tray_icon.icon = self._make_icon_image("#95a5a6")
        except Exception:
            pass

    def _on_error(self, msg):
        self._status_text = f"错误: {msg.splitlines()[0]}"
        log(f"[Error] {msg}")

    def _on_status(self, msg):
        self._status_text = msg
        log(f"[Status] {msg}")

    def _on_state_change(self, state, detail):
        log(f"[StateChange] {state}: {detail}")
        try:
            self._update_tray_state(state)
        except Exception:
            pass


# ============================================================
#                        入口
# ============================================================
def main():
    init_log()
    log("=" * 64)
    log("预览启动")
    log(f"版本 0.6.0 · {BASE_DIR}")
    log(f"录制目录：{RECORDING_DIR}")
    log("=" * 64)

    if not HAS_TRAY:
        log("[FATAL] 缺少依赖 pystray，请执行：pip install pystray")
        return
    if not HAS_PIL:
        log("[FATAL] 缺少依赖 Pillow，请执行：pip install Pillow")
        return

    try:
        ensure_icon_file()
    except Exception:
        pass

    app = CameraMonitorApp()
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log(f"[Main] 崩溃: {e}\n{traceback.format_exc()}")
    finally:
        try:
            app._cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    main()
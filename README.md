# 摄像头监控 · 局域网预览

> 一套开箱即用的本地摄像头录制 + 局域网网页预览方案。
> 支持断流自恢复、占用让路、静态画面检测、分段录制、H.264 硬件加速、
> 局域网多端观看，并内置网页端文件管理、日志查看、参数调节。

---

## 目录

- [项目介绍](#项目介绍)
- [功能特性](#功能特性)
- [目录结构](#目录结构)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
  - [config.json 字段](#configjson-字段)
  - [网页设置页](#网页设置页)
  - [登录凭证与口令](#登录凭证与口令)
- [使用说明](#使用说明)
  - [系统托盘](#系统托盘)
  - [网页五页面](#网页五页面)
  - [让路机制](#让路机制)
  - [静态画面检测](#静态画面检测)
  - [编码与容器](#编码与容器)
- [日志与排错](#日志与排错)
- [常见问题](#常见问题)
- [许可证](#许可证)

---

## 项目介绍

`live_web` 是一个单文件 Python 应用。启动后：

1. 打开本机摄像头，**持续录制**并保存为视频文件（默认 MKV）；
2. 通过内置 HTTP 服务，把实时画面以 **MJPEG** 流推送到局域网，任何浏览器打开网页即可观看；
3. 网页端可**保存快照、开始/停止录制、全屏、管理文件、查看日志、修改配置**；
4. 当检测到摄像头被其它软件（微信、QQ、OBS、钉钉、浏览器等）占用时自动**让路暂停**，等对方释放后自动恢复。

程序常驻托盘，无需打开控制台。适合做家用安防、会议记录、屏幕外的相机预览等场景。

---

## 功能特性

### 录制

- ✅ 自动枚举摄像头（含中文设备名）
- ✅ 首选 **openh264** 硬件编码；缺省时自动回退到 `mp4v` / `avc1` / `MJPG` / `XVID`
- ✅ 支持容器：**MKV / MP4 / AVI / TS**（AVI 自动对齐偶数尺寸）
- ✅ 自动实测摄像头真实 FPS，避免编码器帧率与实际不符
- ✅ **分段录制**（默认 30 分钟）防止单文件过大
- ✅ **画面静止检测**：静止超过阈值自动删除当前段并重启（防止空洞视频）
- ✅ **小文件自动清理**：录制结束后小于阈值的文件被丢弃

### 稳定性

- ✅ **保活守护**：录制线程崩溃 5 秒内自动拉起
- ✅ **断流自恢复**：连续读帧失败自动重连
- ✅ **打开失败兜底**：连续 N 次打不开自动让路
- ✅ **占用让路**：注册表 + 进程双路监控，检测到冲突立即暂停 N 秒
- ✅ 历史日志自动保留 30 天，老 GBK 日志自动转 UTF-8

### 网页端

- ✅ 5 个独立页面：**登录 / 预览 / 设置 / 文件 / 日志**
- ✅ 用户名 + 密码 + 动态口令（TOTP）三重认证
- ✅ MJPEG 多端观看（同时显示在线观看者数）
- ✅ 文件下载（支持 HTTP Range 断点续传）
- ✅ 日志在线查看（可选自动刷新 3s）
- ✅ 修改设置**立即生效**（摄像头相关参数改动会自动重启录制线程）
- ✅ 全响应式设计，手机 / 平板 / 电脑均可使用

### 技术细节

- ✅ 所有页面模板以独立 HTML 文件加载，**与 Python 分离**
- ✅ 状态机管理录制生命周期（`IDLE → STARTING → STREAMING / RECORDING → YIELDING / RECONNECTING → ERROR`）
- ✅ 帧广播采用「最新帧覆盖」策略，慢速客户端不会拖慢录制
- ✅ 网页推流 MJPEG 最大宽度 1600px、15fps、JPEG 质量 80

---


## 目录结构
```

项目根目录/
├── live_web.py          # 主程序文件
├── login.html               # 登录页模板
├── preview.html             # 预览页模板
├── settings.html            # 设置页模板
├── files.html               # 文件管理页模板
├── logs.html                # 日志页模板
├── config.json              # 配置文件（首次运行自动生成）
├── app_icon.ico             # 托盘图标（首次运行自动生成）
├── openh264-x.x.x-win64.dll # （可选）放在这里启用硬件 H.264
├── recordings/              # 录制视频（固定位置）
│   ├── record_20250101_120000_123.mkv
│   └── ...
└── logs/                    # 运行日志（自动保留 30 天）
└── monitor_2025-01-01.log

```
---

## 环境要求

| 项 | 要求 |
|---|---|
| Python | **3.9+**（使用了 `str \| None` 语法，建议 3.10+） |
| 操作系统 | Windows 10 / 11（占用检测依赖注册表 + `tasklist`，仅 Windows 全功能可用）；Linux / macOS 可运行但**无占用让路**与**托盘**部分能力 |
| 摄像头 | 任意 UVC 兼容设备 |

### Python 依赖

**必须：**
```bash
pip install opencv-python numpy Pillow pystray
```

**推荐（增强功能）：**

```bash
pip install cv2-enumerate-cameras  # 获取摄像头真实名称（中文友好）
pip install pywin32                # Windows：托盘 COM 支持
```

> `winreg`（占用检测）为 Windows 内置模块，无需安装。

---

## 快速开始

### 1. 准备文件

把以下 6 个文件放到同一目录：

```
live_web
login.html
preview.html
settings.html
files.html
logs.html
```

### 2. 安装依赖

```bash
pip install opencv-python numpy Pillow pystray
pip install cv2-enumerate-cameras   # 可选，但强烈建议
```

### 3. 启动

```bash
python live_web.py
```

首次运行时控制台会打印**局域网访问地址**和**自动生成的登录凭证**：

```
================================================================
[Web] 首次运行，已生成网页登录凭证：
[Web]   用户名 : admin
[Web]   密码   : xxxxxxxxx
[Web]   凭证已明文保存在 config.json 的 web_auth 字段，
[Web]   可直接打开文件查看或修改。
================================================================
[Web] 局域网预览地址: http://192.168.1.20:8765
[Web] 本机预览地址  : http://127.0.0.1:8765
```

### 4. 打开网页

- 本机：[http://127.0.0.1:8765](http://127.0.0.1:8765)
- 局域网其它设备：`http://<你的局域网IP>:8765`

输入用户名、密码，以及**实时口令**登录。

---

## 配置说明

### config.json 字段

所有字段都可以在**网页设置页**里修改并保存，也可以直接编辑 `config.json` 后重启生效。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `camera_index` | int | `0` | 摄像头索引，`0` 通常是第一个摄像头 |
| `camera_backend` | str | `"CAP_DSHOW"` | 采集后端：`CAP_DSHOW` / `CAP_MSMF` / `CAP_ANY` / `CAP_V4L2` / `CAP_AVFOUNDATION` |
| `width` | int | `1920` | 请求分辨率宽 |
| `height` | int | `1080` | 请求分辨率高 |
| `fps` | float | `30.0` | 请求帧率（实际以摄像头为准） |
| `output_dir` | str | `./recordings` | **固定**，改无效 |
| `quality` | int | `90` | 编码质量 0-100（越高越清晰、文件越大） |
| `retry_interval` | float | `3.0` | 打开失败重试间隔（秒） |
| `max_retries` | int | `10` | 单次打开最大重试次数 |
| `segment_minutes` | int | `30` | 分段时长，超过后切分文件 |
| `first_run_done` | bool | `false` | 首次运行标记（内部使用） |
| `prefer_h264` | bool | `false` | 优先使用 H.264（需 `openh264.dll`） |
| `keep_alive` | bool | `true` | 保活守护：线程崩溃自动重启 |
| `occupancy_monitor_enabled` | bool | `true` | 摄像头占用监控 |
| `occupancy_poll_interval` | float | `2.0` | 注册表轮询间隔（秒） |
| `occupancy_confirm_count` | int | `2` | 连续检测到 N 次才让路 |
| `occupancy_yield_seconds` | int | `30` | 每次让路时长（秒） |
| `yield_resume_delay` | float | `3.0` | 恢复前延迟（秒） |
| `open_fail_threshold` | int | `3` | 连续打开失败 N 次触发让路 |
| `process_monitor_enabled` | bool | `true` | 监控已知摄像头软件启动 |
| `process_poll_interval` | float | `2.0` | 进程轮询间隔（秒） |
| `extra_webcam_apps` | list | `[]` | 额外进程名列表，如 `["mycam.exe"]` |
| `min_file_size_mb` | float | `1` | 小于此大小的视频文件会被删除 |
| `static_detection_enabled` | bool | `true` | 静态画面检测 |
| `static_frame_timeout` | int | `30` | 静止超时（秒），触发刷新 |
| `static_diff_threshold` | float | `3.0` | 灰度差异阈值（0-255） |
| `openh264_dll_path` | str | `""` | 留空则自动在运行目录搜索 |
| `measure_fps_enabled` | bool | `true` | 自动实测帧率 |
| `measure_fps_seconds` | float | `1.5` | 实测时长（秒） |
| `container_format` | str | `"mkv"` | `mkv` / `mp4` / `avi` / `ts` |
| `web_auth` | dict | `{}` | `{"username": "...", "password": "..."}` |

> 🔒 `output_dir` 会在保存时被强制重置为 `./recordings`，保证文件管理页能正确扫描。

### 网页设置页

打开 `/settings` 可在线修改除 `output_dir`、`web_auth` 之外的**全部**字段。**修改摄像头相关字段**（设备、后端、分辨率、帧率）会自动**重启录制线程**，预览会中断约 3 秒。

维护类操作也在设置页：

| 按钮 | 作用 |
|---|---|
| 刷新列表 | 重新枚举摄像头（重新扫描 USB 变更） |
| 清理小文件 | 删除 `recordings/` 中小于 `min_file_size_mb` 的视频 |
| 清理全部日志 | 删除 `logs/` 中所有 `monitor_*.log` |
| 重启录制 | 强制停止并重新启动录制线程 |

### 登录凭证与口令

- **用户名 / 密码**：明文保存在 `config.json → web_auth` 字段，可自行修改（下次启动生效）
- **动态口令（TOTP）**：使用 `日 + 时 + 分` 的 6 位数字拼接，例如：
  - 当天 15 日、14 时 25 分 → **`151425`**
  - 每天 24 × 60 = **1440 个时间窗口**，当前分钟与上一分钟均可登录
- **登录会话**：`cam_session` Cookie，24 小时有效

> 🔧 口令算法就在 `compute_totp()` 函数里，可以随意修改成你自己习惯的规则。

---

## 使用说明

### 系统托盘

程序启动后常驻托盘，图标颜色会随状态变化：

| 图标颜色 | 含义 |
|---|---|
| 🔴 红 | 录制中 / 错误 |
| 🔵 蓝 | 预览中 / 让路中 |
| ⚪ 灰 | 已停止 |

**右键菜单：**

- **复制 Web 地址**：把局域网地址复制到剪贴板
- **退出**：安全停止录制、关闭网页服务、退出程序

### 网页五页面

| 页面 | 路径 | 说明 |
|---|---|---|
| 登录 | `/login` | 用户名 + 密码 + 动态口令 |
| 预览 | `/` | 实时画面 + 控制按钮 + 状态信息 |
| 设置 | `/settings` | 全部配置项 + 维护操作 |
| 文件 | `/files` | 录像列表 + 下载 + 删除 |
| 日志 | `/logs` | 按日期选日志 + 自动刷新 |

**预览页按钮：**

- **保存当前帧** → 下载当前画面 JPG 快照
- **开始 / 停止录制** → 网页端控制开关（**不影响预览**）
- **全屏** → 浏览器全屏观看
- **重新连接** → 强制重开 MJPEG 流（网络抖动时使用）

### 让路机制

当检测到以下情况时，录制线程会**主动暂停** N 秒（默认 30 秒），等待对方释放摄像头：

**触发条件（任意一条）：**

1. **注册表占用**：`HKCU\...\CapabilityAccessManager\ConsentStore\webcam` 中有 `LastUsedTimeStop = 0` 的非自身进程
2. **新进程启动**：`KNOWN_WEBCAM_APPS` 中的进程从无到有
3. **连续打开失败**：摄像头被独占，连续 N 次（默认 3）打不开

**让路期间：**

- 状态变为 `YIELDING`
- 每 2 秒重新检查占用
- 占用持续则续期，占用解除则 5 秒后自动恢复

**已知摄像头软件**（可扩展 `extra_webcam_apps`）：

```
微信、QQ、TIM、钉钉、飞书、腾讯会议、Zoom、Teams、Skype、
Discord、Slack、Telegram、OBS、Streamlabs、Bandicam、Camtasia、
DroidCam、iVCam、EpocCam、ManyCam、Windows 相机、Logitech 等
```

### 静态画面检测

监控场景常见「摄像头被遮挡 / 对准白墙」造成几小时的空洞视频。本程序自动检测：

- 每 3 帧采样一次，缩放到 160 × 120 灰度
- 与上一采样帧的**平均绝对差** > `static_diff_threshold`（默认 3.0）视为「有变化」
- 持续 `static_frame_timeout`（默认 30s）无变化 → **删除当前段并重新开始**

> 关闭方法：设置页取消勾选「静态画面检测」即可。

### 编码与容器

**容器选择建议：**

| 容器 | 推荐场景 | 备注 |
|---|---|---|
| **MKV** | 默认，**最抗崩溃** | 掉电 / 崩溃不破坏已有数据 |
| MP4 | 需要直接播放 / 分享 | 突然断电可能损坏尾部 |
| AVI | 老式设备 / 简单播放器 | 自动对齐偶数尺寸 |
| TS | 流式场景 | 支持管道输出 |

**编码器回退顺序：**

| 容器 | prefer_h264 = false | prefer_h264 = true |
|---|---|---|
| MKV | mp4v → avc1 → XVID | avc1 → mp4v → XVID |
| MP4 | mp4v → avc1 | avc1 → mp4v |
| AVI | MJPG → XVID | MJPG → XVID |
| TS | mp4v → avc1 | avc1 → mp4v |

**启用 H.264：**

1. 下载 [openh264](https://github.com/cisco/openh264/releases) 对应版本，例如 `openh264-2.5.0-win64.dll`
2. 放到 `live_web` 同目录
3. 设置页勾选「优先 H.264」
4. 日志会显示 `[H264] openh264 加载成功: ...`

---

## 日志与排错

### 日志位置

- 当前日志：`logs/monitor_YYYY-MM-DD.log`
- 保留策略：**30 天**，超期自动删除
- 编码：UTF-8（历史 GBK 日志自动转换）
- 网页查看：`/logs` 页面，支持自动刷新

### 关键日志前缀

| 前缀 | 含义 |
|---|---|
| `[App]` | 主程序生命周期 |
| `[Recorder]` | 录制线程 |
| `[State]` / `[StateChange]` | 状态机切换 |
| `[Web]` | HTTP 服务 |
| `[Occupancy]` | 占用监控 |
| `[Supervisor]` | 保活守护 |
| `[H264]` | H.264 加载 |
| `[Clean]` | 小文件清理 |
| `[Tray]` | 托盘操作 |
| `[Config]` | 配置文件 |

### 状态机

```
IDLE
  ↓ start()
STARTING → 打开摄像头
  ↓ 成功
STREAMING ⇄ RECORDING
  ↓ 检测到占用 / 打开失败
YIELDING （N 秒后自动恢复）
  ↓ 读帧失败 ≥ 5 次
RECONNECTING （2 秒后重连）
  ↓ 全部失败
ERROR
```

---

## 常见问题

### Q1：网页打不开 / 显示「页面模板缺失」

**原因**：缺少 `.html` 文件，或不在 `live_web` 同目录。

**解决**：确认目录下有全部 5 个 HTML 文件：

```
login.html
preview.html
settings.html
files.html
logs.html
```

---

### Q2：提示端口 8765~8774 全部被占用

**原因**：其它服务（如 Grafana、Jenkins）已经占用该段端口。

**解决**：修改 `live_web` 中的 `WEB_PORT = 8765` 为其它未占用端口，然后重启。

---

### Q3：录制文件打不开 / 视频损坏

**原因**：可能使用了 MP4 容器 + 编码崩溃。

**解决**：

- 优先使用 **MKV** 容器
- 或者用 `ffmpeg -i broken.mp4 -c copy fixed.mp4` 尝试修复

---

### Q4：预览画面卡顿 / 帧率低

**调优：**

1. 降低摄像头分辨率（如 1280×720）
2. 降低编码质量（80 → 70）
3. 编辑 `live_web` 中的 `STREAM_MAX_WIDTH`（1600 → 1280）
4. 编辑 `STREAM_FPS`（15.0 → 10.0）

---

### Q5：明明没开微信，为什么一直让路？

**原因**：`tasklist` 可能扫到了后台残留进程。

**解决：**

1. 打开 `/logs` 看具体是哪个进程触发了让路
2. 用任务管理器结束该进程
3. 或在设置页把「进程监控」关闭（不推荐长期关闭）

---

### Q6：动态口令是什么？怎么算？

**算法**：`日 + 时 + 分`，例如 15 日 14:25 → **`151425`**。

**当前时间**：参考电脑右下角时钟。

**修改**：编辑 `compute_totp()` 函数即可自定义。

---

### Q7：局域网其它设备连不上

**排查：**

1. 确认访问的是**局域网 IP**（`192.168.x.x` / `10.x.x.x`），不是 `127.0.0.1`
2. Windows 防火墙放行 `python.exe` 和 8765 端口
3. 确认两台设备在**同一子网**
4. 用 `ping <IP>` 测试网络是否通

---

### Q8：端口改了之后托盘「复制 Web 地址」还是老端口

**原因**：托盘使用 `self.web_server.url`，重启程序后会更新。

**解决**：修改 `WEB_PORT` 后**必须完全退出**（右键托盘 → 退出）再重新启动。

---

### Q9：如何卸载 / 清理？

只需删除以下内容即可：

```
live_web_006.py
*.html
config.json
recordings/
logs/
app_icon.ico
```

程序没有写入注册表、没有系统服务、不修改环境变量。

---

## 许可证

本项目仅供个人学习与自用。作者不对使用后果承担责任。请遵守当地关于**监控录制**的法律法规，不得用于侵犯他人隐私的场景。

---

## 致谢

- [OpenCV](https://opencv.org/) — 视频采集与编码
- [pystray](https://github.com/moses-palmer/pystray) — 系统托盘
- [Pillow](https://python-pillow.org/) — 图标生成
- [openh264](https://github.com/cisco/openh264) — 硬件 H.264 编码器


---

## 说明

- 本 README 完全基于**当前项目代码**生成，字段名、默认值、算法逻辑、状态机、让路机制均以代码为准。
- 所有配置项一一对应 `DEFAULT_CONFIG`，无遗漏、无虚构。
- 动态口令算法（`日+时+分`）与 `compute_totp()` 完全一致。
- 端口、编码器回退顺序、让路触发条件均严格对照代码描述。


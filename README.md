# open3d-reconstruct

这是一个面向 Azure Kinect DK、Intel RealSense D435 和 D435i 的跨平台、本地隔离版
Open3D 重建系统。它保留 Open3D 0.19 原生 Reconstruction System 的完整流程：
RGB-D 里程计与片段生成、片段全局配准、精细 ICP 配准、TSDF 网格融合，并保留
SLAC 与颜色映射优化。

项目把 Python、虚拟环境、相机配置、录制、RGB-D 帧提取和重建收拢到同一个入口。
无需激活虚拟环境，也不使用系统 Python 包。

## 当前状态

- 目标系统：Windows x64、Ubuntu/WSL2 x86_64、macOS arm64/x86_64
- Python：项目内 CPython 3.12；Linux/macOS 使用 `.venv`，Windows 使用独立的
  `.venv-windows`
- Open3D：0.19.0；Windows/Linux 使用官方 x86_64 wheel，macOS 使用官方
  universal2 wheel
- GPU：Linux 与 WSL2 自动使用可用的 NVIDIA CUDA，Apple Silicon 自动使用
  Metal/MPS；Windows 原生版和 Intel Mac 使用 Open3D CPU 路径
- Azure Kinect：Linux/WSL2 使用 `.deps/k4a/`、Windows 使用
  `.deps/k4a-windows/` 中的 Sensor SDK 1.4.1；WSL2 与 macOS 使用 FFmpeg + MKV
  内嵌工厂标定完成已有录像的软件对齐，避开 WSL2 的 K4A OpenGL 变换限制
- RealSense：Open3D wheel 内置 librealsense，无需系统 SDK 或 `pyrealsense2`；macOS
  的实时 USB 支持受上游限制，WSL2 需要先把 USB 设备转接给发行版
- D435/D435i：设备枚举、预览、BAG 录制、暂停/继续、提取与一键重建均已接入
- Web 控制台：录制时实时显示 RGB、深度伪彩和 IMU 三维姿态，重建前可选择速度/质量
  参数，重建时显示阶段心跳、输入帧与局部 PLY；全部使用本机 11920 单口
- 无设备单元测试和四阶段合成 RGB-D 重建自检：Windows、WSL2、Ubuntu、macOS 已通过
- macOS 示例 `data/recordings/web-20260903-105959.mkv`：125 帧提取和四阶段重建已通过
- WSL2 同一示例：静态 FFmpeg 软件标定提取 63 帧（stride 2）及四阶段 CPU 重建已通过，
  最终网格包含 358,550 个顶点和 594,769 个三角形
- Azure Kinect 与 D435/D435i 真机采集：Linux 保留原实现；Windows x64 已接入原生
  K4A/librealsense 后端；macOS 的 Azure 实时采集因官方 SDK 不支持而明确禁用

2026-09-05 在当前 Windows 11 + WSL2（Ubuntu 20.04）设备上的验证结果：两端的
`doctor`、四阶段合成 RGB-D 重建及后台服务完整生命周期均通过；WSL2 单元测试为
66/66，Windows 为 65 项通过、1 项依赖 PyTorch 的 GPU 测试按设计跳过。本机没有
NVIDIA GPU，也没有接入 Azure Kinect/D435/D435i，因此本次结论覆盖 CPU 软件链路与
相机 SDK 加载，不冒充真机采集或 GPU 验证。

平台能力如下：

| 功能 | Windows x64 | Linux / WSL2 x86_64 | macOS arm64/x86_64 |
| --- | --- | --- | --- |
| Open3D 重建 / Web 控制台 | 支持 | 支持 | 支持 |
| 连续帧 RGB-D 里程计 GPU 加速 | CPU | NVIDIA CUDA | Apple Metal/MPS（M 系列） |
| Azure Kinect 已有 MKV | K4A 原生读取 | Linux 原生；WSL2 使用 FFmpeg 标定后端 | FFmpeg 标定后端 |
| Azure Kinect 实时预览与录制 | 支持 | 支持；WSL2 需 USB 转接 | 官方 SDK 不支持 |
| RealSense 已有 BAG | 支持 | 支持 | 支持 |
| RealSense 实时预览与录制 | 支持 | 支持；WSL2 需 USB 转接 | 上游实验性支持 |

## Windows x64 快速开始

Windows 原生环境必须放在本机 NTFS 路径，例如
`C:\src\open3d-reconstruct-system`。不要从 `\\wsl.localhost\...` UNC 路径运行
Windows 安装器：Windows `uv` 的文件锁和虚拟环境在 WSL 文件服务器上不可靠。
建议为 Windows 与 WSL2 各保留一份工作副本。

在 Windows PowerShell 5.1 或更高版本中运行：

```powershell
Set-Location C:\src\open3d-reconstruct-system
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.\open3d-reconstruct.ps1 doctor
.\open3d-reconstruct.ps1 self-test
```

`setup.ps1` 会下载并校验项目内的 Windows `uv`、CPython 3.12、固定版本依赖和
Azure Kinect 1.4.1 运行库，不依赖系统 Python。已有完整缓存时可用
`.\setup.ps1 -Offline`。也可以从 `cmd.exe` 使用
`open3d-reconstruct.cmd`；后续文档中的 `./open3d-reconstruct` 在 Windows 下均可替换为
`.\open3d-reconstruct.ps1`。

Windows 后台服务入口为：

```powershell
.\start-service.cmd
.\status-service.cmd
.\stop-service.cmd
```

## WSL2 快速开始

在 WSL2 的 Ubuntu 终端中，它与普通 Linux 使用相同入口：

```bash
./setup.sh
./open3d-reconstruct doctor
./open3d-reconstruct self-test
```

Windows 浏览器可直接访问 WSL2 服务的
[http://127.0.0.1:11920](http://127.0.0.1:11920)。相机 USB 不会仅因安装 WSL2 就自动
出现在 Linux 中；需要先在 Windows 侧完成 USB 转接，再在 WSL2 中安装 udev 规则并以
`doctor --camera all --require-device` 确认。若更重视相机即插即用，可直接使用上面的
Windows 原生环境；若使用 NVIDIA 加速，则先确认 WSL2 内的 `nvidia-smi` 可用。
WSL2 Web 控制台会调用 Windows 原生文件选择窗口，并自动转换 Windows/WSL 路径，
无需安装 Zenity。Azure Kinect MKV 会自动使用项目 Python 环境内的静态 FFmpeg 做
软件标定对齐，无需安装系统 FFmpeg，也不会调用 WSL2 中不稳定的 K4A OpenGL 变换引擎。

## macOS 快速开始

macOS 只额外需要 FFmpeg。若尚未安装：

```bash
brew install ffmpeg
```

然后运行项目安装器；它会自动识别 Intel 或 Apple Silicon，并把正确架构的 CPython
3.12 和 wheel 安装在项目内：

```bash
./setup.sh
./open3d-reconstruct doctor
```

直接验证仓库中的示例录像：

```bash
./open3d-reconstruct reconstruct \
  data/recordings/web-20260903-105959.mkv
```

该命令自动执行 MKV 提取、标定对齐和四阶段重建。结果位于
`data/datasets/web-20260903-105959/scene/integrated.ply`。也可以先用
`./start-service.sh` 启动 Web 控制台，再选择“打开本地录制”。

## 本地隔离

在干净目录中安装只需：

```bash
./setup.sh
```

所有应用依赖都位于当前项目：

| 内容 | Linux/macOS/WSL2 | Windows 原生 |
| --- | --- | --- |
| CPython 3.12 | `.python/` | `.python-windows/` |
| Python 虚拟环境和全部 Python 包 | `.venv/` | `.venv-windows/` |
| Azure Kinect SDK 与深度引擎 | `.deps/k4a/`（Linux/WSL2） | `.deps/k4a-windows/` |
| `uv` | `.tools/` | `.tools-windows/` |
| 服务运行状态 | `.run/` | `.run/windows/` |
| 下载及运行缓存 | `.cache/` | `.cache/` 内的 Windows 专用子目录 |
| 录制和重建数据 | `data/` | `data/` |

RealSense 的 librealsense 已静态集成在对应虚拟环境的 Open3D wheel 中，不会读取
系统安装的 librealsense。启动器使用 Python isolated mode，并清除外部
`PYTHONPATH`，避免 ROS、Conda 和用户 site-packages 混入。

操作系统内核、USB 与图形栈属于系统基础设施。Linux 的 udev 规则必须从
`/etc/udev/rules.d/` 读取，因此非 root 访问相机时可能需要一次显式的
`udev-install`。Windows/macOS 不使用 udev；FFmpeg 由 Homebrew 安装在项目外，
Python 与 Python 包仍保持项目内隔离。Windows 相机驱动与 WSL2 的 USB 转接属于系统
基础设施，不由项目安装器静默修改。

## Web 可视化控制台

推荐使用后台单例服务脚本启动：

```bash
./start-service.sh
```

Windows 使用 `.\start-service.cmd`；对应的状态与停止入口是
`.\status-service.cmd`、`.\stop-service.cmd`。

脚本返回“启动成功”后访问
[http://127.0.0.1:11920](http://127.0.0.1:11920)。重复执行启动脚本是安全的：程序会
核对进程身份和 HTTP 健康状态并复用现有实例，不会再创建第二个服务。

检查服务状态：

```bash
./status-service.sh
```

状态检查同时验证 PID 启动时间、服务实例 ID 和 `/api/health`，不会把陈旧 PID 或
11920 端口上的其他程序误判成本服务。退出码为 `0` 表示运行正常、`1` 表示进程存在
但健康检查失败、`3` 表示未运行。

安全终止服务：

```bash
./stop-service.sh
```

如果页面正在录制或转换，停止脚本会先通知 Web 服务结束子任务；录制后端会封装
MKV/BAG 后退出，重建任务则视为放弃并终止其完整进程树，原始录制继续保留。为保护
录制文件，录制封装超时后脚本只报告错误，不会直接执行 `SIGKILL`。

单例锁、PID 元数据和服务日志全部位于项目内：

```text
.run/web.lock
.run/web.pid.json
.run/web.log
```

Windows 使用隔离的 `.run/windows/` 子目录，文件名相同。

需要前台运行以便调试时，也可以使用：

```bash
./open3d-reconstruct web
```

前台命令使用同一把进程锁，因此后台服务存在时也不会重复启动。它会自动打开浏览器。
Windows、WSL2 与 macOS 上“打开本地录制”和“加载其他点云”使用系统原生文件选择窗口；
普通 Linux 使用 Zenity。由于 Azure Kinect 官方 Sensor SDK 没有 macOS 后端，macOS 页面中应通过
“打开本地录制”载入已有 MKV，而不是连接 Azure 相机。

页面中的操作顺序为：

1. 选择 Azure Kinect DK、RealSense D435 或 D435i，并确认设备编号。每张相机卡片的
   “查看参数”会只读展示当前录制配置、硬件深度算法预设，以及设备能提供的序列号、
   固件和 USB 类型；查看过程不会连接、停止或重启相机。
2. 点击“连接并录制”，页面会先等待 RGB-D 画面同步，再显示实时 RGB、0.3–3 米深度
   伪彩、采集帧率和传感器状态并开始录制。Azure Kinect 同时显示真实加速度计、陀螺仪、
   温度、IMU 采样率和实时三维姿态轴；扫描场景后点击“结束录制”，页面会等待 MKV/BAG
   安全封装。也可以不连接相机，点击“打开本地录制”，通过桌面文件选择器选择磁盘上的
   MKV/BAG。项目会在 `data/recordings/` 优先建立硬链接，跨磁盘时改用符号引用，不再
   上传或复制整段视频；完成后直接进入重建参数确认。Windows 普通用户创建跨卷文件
   符号链接需要开启“开发人员模式”；未开启时请把录像放到项目所在 NTFS 卷，或通过
   页面上传导入。
3. 点击“开始重建”会先弹出参数确认窗口，可选择速度优先、均衡或质量优先，也可分别
   调整帧采样、配准体素、深度范围、全局配准、片段长度、关键帧间隔、ICP 方法、
   深度差阈值、TSDF 融合体素、SDF 截断距离及片内/片段回环偏好。
   提取阶段会显示最新 RGB-D 帧；进入 `make` 后切换为真实帧对匹配热力图和信息矩阵
   强度趋势，区分相邻帧、回环候选、失败项和正在计算的帧对。局部片段生成后会同时显示
   可旋转的中间点云，并持续展示 `extract → make → register → refine → integrate` 进度、
   预计与已完成匹配数、片段总数、并行进程、本阶段耗时及后端活动状态。Open3D 未提供
   逐迭代 loss，因此趋势采用本次任务内可比较的相对约束强度，不将其标记为损失值。
   重建由后台服务持有，与浏览器标签页生命周期无关：关闭或刷新网页不会中断任务，
   再次访问同一地址会恢复阶段、计数、匹配统计、可视化产物和日志。页面提供“暂停重建 /
   继续重建”和“终止重建”；暂停会冻结完整计算进程树并保留内存状态，继续后从同一位置
   运行。暂停不会释放已经占用的内存或显存；需要释放这些资源时应终止任务。停止后台
   服务或在前台终端按 `Ctrl+C` 会把重建视为放弃，但不会删除 MKV/BAG 录制。
   成功完成后，数据集根目录会原子写入 `open3d-project.json`。该工程配置使用相对路径
   索引 RGB-D 数据、内参、轨迹、位姿图和最终模型，并保存重建参数、计算后端、阶段
   耗时、帧对匹配统计与事件、暂停统计及 Web 日志。点击“打开重建工程”可直接选择整个
   数据集目录，恢复模型与工程分析；没有该配置的旧版完整数据集会在首次打开时自动识别
   并补建。工程目录可以整体移动，但目录外的原始录制只有在原路径或相对路径仍可访问时
   才会显示下载入口。
4. 完成后可在页面切换点云、彩色面或强调几何起伏的结构面预览。也可以随时点击
   “加载其他点云”选择本机 `.ply`：文件窗口优先打开当前数据集的 `scene/` 结果目录，
   没有当前数据集时打开 `data/datasets/`，并只显示 `.ply/.PLY` 文件；后端还会校验
   后缀与 PLY 文件头。所选文件从原路径只读加载，不上传、不复制，关闭外部预览后可
   返回当前重建结果。预览可无角度限制地 360° 旋转、缩放和平移。左键拖动旋转，
   右键、中键或 `Shift + 左键` 临时平移；也可
   使用预览窗口内的“旋转 / 平移 / 复位”工具，触屏下切换工具后直接拖动。右下角球形
   XYZ 坐标轴会与模型同步转动，用于辨认整体朝向。左下角地图式比例尺会随缩放动态
   更新，并显示模型原始米制坐标计算得到的 `X × Y × Z` 包围盒尺寸；比例尺以模型中心
   平面为基准，透视视图中前后位置的屏幕尺度会略有差异。
   浏览器使用索引 WebGL 渲染并缓存最高 50 万三角面的高清预览，在控制显存占用的
   同时保留更多颜色和轮廓细节；页面下载的仍是未简化的完整
   `scene/integrated.ply` 或原始录制文件。
5. “管理录制”会列出 `data/recordings/` 中已经落盘或引用的视频、关联数据集、模型状态
   及各自占用空间。可以直接重新打开历史结果；删除时会明确选择仅删除视频，或同步删除
   RGB-D 预处理数据与全部重建产物。硬链接/符号引用被删除时，项目外的原始视频会保留。

页面、JSON API、任务日志和结果文件全部通过 `127.0.0.1:11920` 提供，不启动第二个
端口、不引用 CDN，也不会把数据发送到网络。服务只绑定回环地址，局域网中的其他
机器无法访问。所有录制和结果仍分别保存在 `data/recordings/` 与
`data/datasets/`。

Azure Kinect 浏览器预览目标为 20 FPS；页面会分别显示相机采集帧率和 Web 预览
帧率。预览在独立线程中只处理最新帧，并直接复用相机输出的 MJPEG 彩色图，避免在
录制主循环中重复解码、缩放和编码。RealSense 预览目标为 8 FPS，以兼顾其同步软件
编码开销。Azure Kinect 的 Web 录制使用项目内 K4A SDK 同一设备句柄同时写入 RGB-D
capture 和 IMU track，预览不会再次连接或争抢相机。实时深度显示在原始深度坐标系；
转换后的数据集会按工厂标定变换到统一针孔坐标系，并保证彩色、深度和内参尺寸一致。

Web 中的三维姿态轴使用陀螺仪积分，并用加速度方向持续校正 Roll/Pitch；Yaw 是相对角度，
长时间使用可能发生漂移。该视图用于帮助判断相机运动，不代表重建求得的相机轨迹。

无图形桌面或不希望自动拉起浏览器时：

```bash
./open3d-reconstruct web --no-browser
```

默认端口为 11920；必要时可用 `--port` 改为另一个本机端口。

## D435/D435i 接入测试

D435/D435i 应直接连接 USB 3.x 端口。接入后运行：

```bash
./open3d-reconstruct doctor --camera realsense --require-device
```

如果只有 USB 权限或 udev 规则未通过，执行：

```bash
./open3d-reconstruct udev-install --camera realsense
```

该步骤仅适用于 Linux（包括已完成 USB 转接的 WSL2）：命令会明确调用 `sudo`，安装
项目内的 `config/99-realsense-libusb.rules`。安装后重新插拔或重新转接相机，再运行
诊断。Windows/macOS 不使用 udev，执行该命令只会给出说明而不会修改系统；Windows
应检查设备管理器与驱动，macOS 若 SDK 仍枚举不到设备，可先使用已有 BAG 做离线重建。

列出设备能力并预览：

```bash
./open3d-reconstruct list --camera realsense
./open3d-reconstruct preview --camera d435
```

D435i 使用同一后端：

```bash
./open3d-reconstruct preview --camera d435i
```

预览窗口按 `ESC` 退出。没有图形桌面时可读取 30 帧测试：

```bash
./open3d-reconstruct preview --camera realsense --no-window --frames 30
```

## 最简单的 RealSense 完整扫描

下面一条命令会录制 30 秒 `.bag`、提取对齐后的 RGB-D 帧，再执行完整四阶段重建：

```bash
./open3d-reconstruct scan --camera d435 --name first-scan --seconds 30
```

D435i 只需替换相机别名：

```bash
./open3d-reconstruct scan --camera d435i --name first-scan --seconds 30
```

无显示器时增加 `--no-preview`。最终结果位于：

```text
data/recordings/first-scan.bag
data/datasets/first-scan/color/
data/datasets/first-scan/depth/
data/datasets/first-scan/intrinsic.json
data/datasets/first-scan/fragments/
data/datasets/first-scan/scene/integrated.ply
data/datasets/first-scan/scene/trajectory.log
data/datasets/first-scan/run-report.json
data/datasets/first-scan/open3d-project.json
```

`integrated.ply` 是最终带顶点颜色的三角网格。

## 分步工作流

录制 RealSense BAG：

```bash
./open3d-reconstruct record --camera d435 --name room --seconds 60
```

不指定 `--seconds` 时，窗口内按空格暂停/继续，按 `ESC` 保存退出。无窗口录制可用
`--no-preview`，按 `Ctrl+C` 会正常停止并封装 BAG。

提取帧与重建会根据扩展名自动识别 `.bag` 或 `.mkv`，无需再指定相机：

```bash
./open3d-reconstruct extract data/recordings/room.bag
./open3d-reconstruct reconstruct data/recordings/room.bag
```

也可直接重建已提取的数据集：

```bash
./open3d-reconstruct reconstruct data/datasets/room
```

降低帧密度时可以指定步长；来源文件、步长或文件指纹变化后不会错误复用旧数据：

```bash
./open3d-reconstruct extract data/recordings/room.bag --stride 2
```

只重跑部分阶段时，按原生阶段顺序指定：

```bash
./open3d-reconstruct reconstruct data/datasets/room --stages refine,integrate
```

可用阶段为 `make,register,refine,integrate,slac,slac-integrate`。后续阶段需要前序
阶段已经生成对应文件。

### GPU 计算后端

重建默认使用 `auto`：Linux/WSL2 检测 NVIDIA CUDA，Apple Silicon macOS 检测
Metal/MPS；没有可用 GPU、PyTorch 无法加载，或 GPU 里程计在运行中报错时，会自动
改用原有的 Open3D CPU 里程计。Windows 原生环境和 Intel Mac 直接走 CPU；Windows
如需 NVIDIA 加速，建议使用支持 CUDA 转接的 WSL2 环境。可通过 `doctor` 查看最终选择：

```bash
./open3d-reconstruct doctor
```

也可以显式选择或禁用 GPU：

```bash
./open3d-reconstruct reconstruct data/datasets/room --compute-backend auto
./open3d-reconstruct reconstruct data/datasets/room --compute-backend cuda
./open3d-reconstruct reconstruct data/datasets/room --compute-backend mps
./open3d-reconstruct reconstruct data/datasets/room --compute-backend cpu
```

GPU 路径使用同一套 Torch 张量实现，在 CUDA 与 MPS 上执行 Open3D 兼容的混合
光度/深度 RGB-D 里程计；闭环候选的 OpenCV 初始化、全局配准、精细 ICP 和经典 TSDF
融合仍由 CPU/Open3D 完成。因此 GPU 会降低连续帧里程计的 CPU 占用，但端到端收益会
随分辨率、深度有效像素、片段数量、闭环候选数量和具体 M 系列/NVIDIA GPU 而变化。
所有 M 系列使用相同的 MPS 代码路径，不需要按 M1、M2、M3、M4 分别编译。

运行结束后，实际选择的设备会记录在数据集的 `effective-config.json` 和
`run-report.json` 中。`--compute-device` 是上游 SLAC 流程的独立参数，不控制本节的
连续帧里程计后端。

颜色映射优化：

```bash
./open3d-reconstruct color-map data/datasets/room
```

输出为 `scene/color_map_after_optimization.ply`；增加 `--visualize` 可显示优化前后窗口。

## Azure Kinect

Linux/WSL2/Windows 上为兼容已有用法，采集命令不指定 `--camera` 时仍默认 Azure
Kinect：

```bash
./open3d-reconstruct doctor --camera azure-kinect --require-device
./open3d-reconstruct preview
./open3d-reconstruct scan --name azure-room --seconds 30
```

其录制文件为 `.mkv`。权限不足时运行：

```bash
./open3d-reconstruct udev-install --camera azure-kinect
```

`udev-install` 不指定相机时会安装 Azure Kinect 和 RealSense 两套规则；它只适用于
Linux x86_64（包括 WSL2）。Windows x64 的实时采集使用项目内 K4A DLL 和 Windows
原生 USB 后端，不使用 udev；若接入设备后诊断仍无法枚举，请检查官方设备驱动和设备
管理器状态。

Azure Kinect Sensor SDK 官方没有 macOS 运行时，Open3D 的 macOS wheel 也以
`BUILD_AZURE_KINECT=OFF` 构建。因此 macOS 不伪装实时支持：`list`、`preview`、
`record` 和 `scan` 会给出明确提示。已有 MKV 则可以直接处理：

```bash
./open3d-reconstruct extract data/recordings/room.mkv
./open3d-reconstruct reconstruct data/recordings/room.mkv
```

macOS 后端通过 FFmpeg 同步解码 COLOR/DEPTH 轨道，读取 MKV 内嵌的工厂标定，应用 K4A
模式对应的裁剪、缩放、Brown-Conrady 畸变和深度到彩色外参，再把彩色与深度输出到同一
个无畸变深度针孔坐标系。原始标定会保存在数据集的 `azure-calibration.json`，实际内参
与后端信息分别写入 `intrinsic.json` 和 `.open3d-reconstruct.json`。

## 设备选择与配置

`--camera` 支持以下规范值与简写：

| 设备 | 规范值 | 简写 |
| --- | --- | --- |
| Azure Kinect | `azure-kinect` | `azure`、`k4a`、`kinect` |
| D435/D435i | `realsense` | `rs`、`d435`、`d435i` |

有多台 RealSense 时使用 `--sensor 0`、`--sensor 1` 选择。程序会先解析对应设备的
序列号，再交给 Open3D，避免多设备枚举顺序在初始化期间变化。也可在复制后的
RealSense 配置中填写 `serial` 来固定设备。

默认相机配置：

- `config/azure-kinect.json`：720p MJPG、30 FPS、WFOV 2x2 binned depth。
- `config/realsense-d435.json`：RGB8/Z16、640×480、30 FPS、D400 High Accuracy。

自定义配置通过 `--sensor-config` 使用。RealSense 的彩色与深度帧率必须相同；
`list --camera realsense` 会打印当前设备实际支持的分辨率、格式、帧率和预设。

重建默认值在 `config/reconstruction.json`。常用参数可直接覆盖：

```bash
./open3d-reconstruct reconstruct data/datasets/room \
  --set depth_max=2.0 \
  --set voxel_size=0.03 \
  --set tsdf_cubic_size=2.048 \
  --set sdf_trunc=0.02 \
  --set n_frames_per_fragment=80
```

RealSense BAG 中的实际 `depth_scale` 会在提取时写入数据集，并自动传给重建配置；
命令行 `--set` 仍具有最高优先级。`voxel_size` 用于降采样、特征匹配和配准，最终
TSDF 表面分辨率则是 `tsdf_cubic_size / 512`；两者不是同一个“体素”参数。Web
重建弹窗只列出当前经典四阶段流程确实读取、且会直接影响质量的字段；自动标定的
`depth_scale`、只影响速度的 `python_multi_threading` 和仅供 SLAC 流程使用的参数不在
该弹窗中。

## D435i 的 IMU 边界

D435i 比 D435 多一个六轴 IMU。Open3D 0.19 的经典 Reconstruction System 只使用
同步彩色与深度帧，`RealSenseSensor` 录制器也只启用 color/depth 流。因此：

- D435i 的预览、BAG、帧提取和三维重建功能与 D435 完整一致。
- 本程序生成的 BAG 不采集 IMU，重建也不融合 IMU。
- Web 页面会明确显示 D435i 的 IMU 在当前 Open3D 后端不可用，而不会生成伪数据。
- 带额外 IMU 流的既有 RealSense BAG 仍可输入；读取时只选择第一组同步
  color/depth 流。

Azure Kinect 的 Web 录制会把真实 IMU 样本写入 MKV 并实时显示，但经典 Open3D
Reconstruction System 仍只使用 RGB-D；当前版本不把 IMU 融入相机轨迹求解。

这与上游 Open3D 原生 RealSense 重建路径一致，不是用软件里程计伪装 IMU 融合。

## 诊断与离线验证

检查两个后端但不要求设备：

```bash
./open3d-reconstruct doctor
```

运行项目单元测试：

```bash
./.venv/bin/python -B -I -m unittest discover -s tests -v
```

Windows 对应命令为：

```powershell
.\.venv-windows\Scripts\python.exe -B -I -X utf8 -m unittest discover -s tests -v
```

离线自检会创建 6 帧合成 RGB-D 数据并实际跑完四阶段重建：

```bash
./open3d-reconstruct self-test
```

Windows 使用 `.\open3d-reconstruct.ps1 self-test`。

若真机采集不稳定，请检查：

1. 是否直连 5 Gb/s 或更快的 USB 3.x 端口。
2. 是否经过扩展坞、低速 Hub 或共享带宽较重的控制器。
3. `doctor --camera realsense --require-device` 中 USB 权限与链路是否通过。
4. 自定义的 color/depth 帧率是否一致且确实受设备支持。
5. 移动相机时是否平缓，并保持连续画面有足够纹理与重叠。

扫描时避免快速转动、反光或透明表面以及大面积无纹理区域。默认重建深度上限为
3 米。

## 上游与许可

- [Open3D 0.19.0](https://github.com/isl-org/Open3D/tree/v0.19.0) 及其
  Reconstruction System 使用 MIT License；官方 0.19 wheel 提供 Windows/Linux x86_64
  与 macOS universal2 / Apple Silicon 支持。
- Open3D 0.19.0 wheel 以 `BUILD_LIBREALSENSE=ON` 集成
  [librealsense v2.44.0](https://github.com/IntelRealSense/librealsense/tree/v2.44.0)，
  使用 Apache License 2.0。macOS 实时 USB 的上游限制见
  [librealsense macOS 安装说明](https://github.com/realsenseai/librealsense/blob/master/doc/installation_osx.md)。
- [Azure Kinect Sensor SDK 1.4.1](https://github.com/microsoft/Azure-Kinect-Sensor-SDK/tree/v1.4.1)
  的开源部分使用 MIT License；随包二进制适用微软提供的许可条款。
- Python 依赖版本与文件哈希固定在 `uv.lock`。

详见 `THIRD_PARTY_NOTICES.md`。

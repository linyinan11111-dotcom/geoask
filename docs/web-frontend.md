# HTML 桌面界面

运行 `python geoask_web.py` 或 `geoask-gui`，程序打开独立桌面窗口。Windows 源码用户也可双击 `启动界面.cmd`。`python geoask_gui.py` 同样启动新界面。

桌面壳使用 pywebview，Windows 使用系统 WebView2 Runtime 渲染 HTML，不会打开外部浏览器；关闭窗口会停止本次本地 HTTP 服务并释放查询引擎。CSV 导出使用系统保存文件对话框。Windows 缺少 WebView2 Runtime 时需先安装该组件；macOS 使用系统 WKWebView，Linux 需要 pywebview 支持的 GTK/WebKit 或 Qt 运行环境。

前端使用原生 HTML、JavaScript、CSS，无 npm 构建步骤，也不依赖外部脚本或 CSS CDN。地图通过 XYZ 瓦片显示，支持拖动、滚轮与按钮缩放、键盘移动、右键设置起点；显示坐标、地点和道路路线时按配置处理 WGS84 与 GCJ-02 转换。

桌面界面以全屏地图为背景，查询、行程和路线分别放在独立的悬浮玻璃面板中，首页只保留操作标签与必要提示。玻璃主体模糊背后的实时地图，边缘使用本地生成的法线图和 SVG 背景滤镜产生轻微折射，叠加边缘高光、跟随指针的反光与弹簧按压反馈。文字与按钮内容不参与折射。手机上改为纵向面板和地图，避免控件遮挡。

输入框使用 [Lens](https://github.com/ruiqichenbiec/design-systems) 的原版 WebGL2 光学核心，折射单独的渐变画布。地图始终保持实时 DOM，未读取、复制地图像素；Lens 不捕获地图 DOM。源码和 MIT 许可放在 `web/vendor/lens/`，具体版本见该目录 README。查看全程会为悬浮面板和地点标签预留空间，缩放窗口时同步调整。

未启用持续背景动画或音效。无 WebGL2 时保留 CSS 玻璃；系统开启“减少动态效果”时关闭动画，开启“减少透明度”时使用实色面板。修改后重新启动桌面应用即可加载；网页模式可刷新。

地图使用蓝色圆形起点、橙红色编号目的地图钉、紫色字母候选标记和棕黄色停车标记，并显示对应图例。查询完成后自动弹出选点窗口，可按站点逐一确认；更换某站后，后续站点重新查询，再继续选择。可通过“行程地点 → 选择行程地点”或各站的“更换地点”重新打开弹窗；点击地图候选也会打开对应选择窗口供确认。

自然语言查询、最多 6 站的候选选择、换点后重查、步行/骑行/驾车路线、停车场查询、设备定位、查询预览与 CSV 导出使用现有 Python 服务。新界面可以通过 `--config 地区.yaml` 使用已有地区配置；地区文件导入工具仍保留在 `geoask_data.py`。原 Tkinter 类 `geoask_gui.GeoaskApp` 保留供旧界面回归测试使用。

## 启动选项

```powershell
python geoask_web.py --port 8765 --no-browser
python geoask_web.py --config 地区.yaml
python geoask_web.py --browser
```

默认自动分配空闲端口，服务只绑定 `127.0.0.1`。默认桌面方式关闭窗口即退出，也可在“接口设置”点击“退出应用”。`--no-browser` 仅启动服务；`--browser` 使用外部浏览器，这两种方式可在终端按 Ctrl+C 停止，或在界面点击“结束本地服务”。浏览器方式关闭标签页不会停止服务。

## 本地接口

`geoask_web.py` 使用 Python 标准库 HTTP 服务与一个工作线程。查询引擎及数据库连接始终由工作线程持有；网络查询在后台执行，页面轮询任务进度。接口会拒绝并发修改，行程标识阻止旧页面操作新行程。

- `GET /api/config`：公开配置与密钥是否已配置，不返回密钥。
- `GET /api/state`、`GET /api/jobs/{id}`：读取行程与任务状态。
- `POST /api/query`、`/api/select`：自然语言查询、选点及后续站更新。
- `POST /api/route`、`/api/parking`：道路规划与目的地附近停车场。
- `POST /api/locate`、`/api/reset`：设备定位与清空行程。
- `POST /api/settings`、`/api/clear-settings`：更新或删除本机接口设置。
- `GET /api/export`：下载 CSV。
- `POST /api/shutdown`：停止本地服务。

所有 API 请求需要页面内的随机会话令牌（`X-Geoask-Token`），并检查 Host/Origin。静态文件采用固定白名单，不暴露项目目录。密钥保留在 Python 会话或原本的系统用户凭据文件中，不写入浏览器存储；更换语言接口时，不自动沿用原接口的隐藏密钥。

界面中只有真实路线请求成功后才绘制道路折线。换点、修改起点、切换出行方式、改变停车场等操作会使当前显示的旧路线失效；接口失败时显示错误并保留可重试的地点。HTML 通过 `textContent` 显示查询与地点名称，CSV 对公式字符做转义。

## 验证前端

```powershell
python -m pytest -q
npm.cmd install --prefix .cache/browser-tools --no-audit --no-fund @playwright/test
python scripts/serve_web_fixture.py --port 8766
```

保持测试服务运行，在另一个终端执行 `node scripts/verify_web.mjs`。默认使用本机 Microsoft Edge；可通过 `GEOASK_BROWSER=chrome` 选择已安装的 Chrome。浏览器验证工具仅用于开发，不是应用依赖。

测试服务使用明确标注的模拟语言解析、地点和路线，隔离真实凭据，不调用付费 API；请使用 `geoask_web.py` 启动正常应用。验证覆盖多站选点、重算、停车、CSV、名称候选确认、预览、错误恢复、接口切换、地图坐标与标记可见性，以及桌面与手机布局。运行记录与截图位于 `.cache/web-verification/`，README 首页截图为真实底图上的未查询状态。

Windows 上运行 `python scripts/verify_native.py` 可验证实际 WebView2 桌面窗口，包括 HTML 加载、起点标记、设置弹窗及关闭窗口后停止服务。此检查使用隐藏窗口及隔离凭据，不调用付费 API，报告写入 `.cache/native-verification.json`。

保持同一测试服务运行，执行 `node scripts/verify_glass.mjs` 可检查实际 GPU 画布输出、弹簧反馈、闲置停止重绘、减少动画/透明度与恢复，以及无 WebGL2 时的查询选点。报告为 `.cache/web-verification/glass-result.json`。

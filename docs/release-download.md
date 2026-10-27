# 下载与使用 geoask

下载地址：[GitHub Releases 最新版本](https://github.com/linyinan11111-dotcom/geoask/releases/latest)

## Windows 用户（推荐）

下载 `geoask-0.2.2-windows-x64.zip`，解压整个压缩包，再打开 `geoask` 文件夹，双击 `geoask.exe`。

- 支持 Windows 10/11 的 x64 电脑，不需要安装 Python。
- 使用独立 WebView2 桌面窗口显示液态玻璃界面，不会打开外部浏览器。需要系统安装 Microsoft Edge WebView2 Runtime；关闭窗口会停止本次服务。
- 保留 `geoask.exe` 旁的 `_internal` 目录，不要只复制 exe。
- 已包含北京地点数据、Python 运行环境及对应的 DuckDB spatial 扩展。
- 打开“接口设置”，填写自己的 DeepSeek 或兼容接口密钥，以及高德 **Web 服务 Key**（不是 Web 端 JS API Key），点击“保存设置”；可保存到本机，下次自动读取。发布包不包含作者的密钥。
- 自然语言理解、地点查询与路线规划需要网络和有效密钥。快捷示例填入查询文本，同样需要语言模型与高德 Key。
- 默认地点、停车场与步行/骑行/驾车路线使用高德在线服务，不回退本地 OSM，也不需要下载本地嵌入模型。
- 模型、地图缓存和本地连接设置保存在系统用户目录，不在解压目录。
- 设置查询起点后，可输入“先喝咖啡，再去健身”等需求，一次最多查询 6 站；确认地点后，按指定顺序规划路线。默认在线查询范围由起点及各站位置决定。
- 程序没有代码签名证书。是否允许运行请以你核对的下载来源和校验值为依据。

发布前在 Windows x64 本机验证中文路径完整解压、隔离用户目录、独立 exe 运行、捆绑数据与空间扩展、HTML / Lens 资源加载、真实 WebView2 窗口及设置弹窗。此安装检查不调用付费 LLM，也不验证真实高德地点查询或路线请求。

## Python 用户 / Linux / macOS

下载 `geoask-0.2.2-py3-none-any.whl`，在 Python 3.11–3.13 虚拟环境中安装：

```bash
python -m pip install geoask-0.2.2-py3-none-any.whl
geoask-gui
```

wheel 不包含依赖，pip 安装时需要网络。Windows 使用 WebView2、macOS 使用系统 WKWebView；Linux 需配置 pywebview 支持的 GTK/WebKit 或 Qt 运行环境。macOS/Linux 没有本次发布的原生桌面压缩包。

需要修改源码时，下载 GitHub 自动提供的 Source code，或 `geoask-0.2.2.tar.gz`，解压后在项目目录运行：

```bash
python -m pip install -r requirements.txt -e .
python geoask_gui.py
```

## 校验下载

`SHA256SUMS.txt` 包含附件的 SHA256。Windows PowerShell 示例：

```powershell
Get-FileHash .\geoask-0.2.2-windows-x64.zip -Algorithm SHA256
```

## 数据和许可证

代码采用 MIT 许可；示例数据来自 Geofabrik / © OpenStreetMap contributors，遵循 ODbL 1.0，详见包内数据署名和仓库 README。第三方 Python 组件许可证保存在 `THIRD_PARTY_LICENSES`；Lens 原版光学核心及 MIT 许可保存在 `_internal/geoask_resources/web/vendor/lens/`。首次下载的模型遵循其模型许可证。

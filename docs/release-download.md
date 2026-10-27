# 下载与使用 geoask

下载地址：[GitHub Releases 最新版本](https://github.com/linyinan11111-dotcom/geoask/releases/latest)

## Windows 用户（推荐）

下载 `geoask-0.2.1-windows-x64.zip`，解压整个压缩包，再打开 `geoask` 文件夹，双击 `geoask.exe`。

- 支持 Windows 10/11 的 x64 电脑，不需要安装 Python。
- 保留 `geoask.exe` 旁的 `_internal` 目录，不要只复制 exe。
- 已包含北京地点数据、Python 运行环境及对应的 DuckDB spatial 扩展。
- 打开“接口设置”，填写自己的 DeepSeek 或兼容接口密钥，以及高德 **Web 服务 Key**（不是 Web 端 JS API Key），点击“应用所有设置”；可保存到本机，下次自动读取。发布包不包含作者的密钥。
- 自然语言理解、地点查询与路线规划需要网络和有效密钥。示例按钮使用固定的语言解析结果，地点与路线仍请求高德，需要高德 Key。
- 默认地点、停车场与步行/骑行/驾车路线使用高德在线服务，不回退本地 OSM，也不需要下载本地嵌入模型。
- 模型、地图缓存和本地连接设置保存在系统用户目录，不在解压目录。
- 设置查询起点后，可输入“先喝咖啡，再去健身”等需求，一次最多查询 6 站；确认地点后，按指定顺序规划路线。默认在线查询范围由起点及各站位置决定。
- 程序没有代码签名证书。是否允许运行请以你核对的下载来源和校验值为依据。

发布前在 Windows x64 本机验证压缩包解压、独立运行、捆绑数据与空间扩展加载、GUI 初始化及设置窗口。此安装检查不调用付费 LLM，也不验证真实高德地点查询或路线请求。

## Python 用户 / Linux / macOS

下载 `geoask-0.2.1-py3-none-any.whl`，在 Python 3.11–3.13 虚拟环境中安装：

```bash
python -m pip install geoask-0.2.1-py3-none-any.whl
geoask-gui
```

Linux 可能需要通过系统包管理器安装 `python3-tk`。wheel 不包含依赖，pip 安装时需要网络；macOS/Linux 没有本次发布的原生桌面压缩包。

需要修改源码时，下载 GitHub 自动提供的 Source code，或 `geoask-0.2.1.tar.gz`，解压后在项目目录运行：

```bash
python -m pip install -r requirements.txt -e .
python geoask_gui.py
```

## 校验下载

`SHA256SUMS.txt` 包含附件的 SHA256。Windows PowerShell 示例：

```powershell
Get-FileHash .\geoask-0.2.1-windows-x64.zip -Algorithm SHA256
```

## 数据和许可证

代码采用 MIT 许可；示例数据来自 Geofabrik / © OpenStreetMap contributors，遵循 ODbL 1.0，详见包内数据署名和仓库 README。第三方 Python 组件许可证保存在 `THIRD_PARTY_LICENSES`。首次下载的模型遵循其模型许可证。

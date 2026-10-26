# 下载与使用 geoask

下载地址：https://github.com/linyinan11111-dotcom/geoask/releases/latest

## Windows 用户（推荐）

下载 `geoask-0.2.0-windows-x64.zip`，解压整个压缩包，再双击 `geoask.exe`。

- 支持 Windows 10/11 的 x64 电脑，不需要安装 Python。
- 保留 `geoask.exe` 旁的 `_internal` 目录，不要只复制 exe。
- 已包含北京地点数据、Python 运行环境及对应的 DuckDB spatial 扩展。
- 打开“接口设置”，填写自己的 DeepSeek 或兼容接口密钥并保存；发布包不包含作者的密钥。
- 不填写密钥也能点击示例按钮。首次类别查询需要联网下载约 0.22 GB 的多语言模型；地图、道路路线、正常 LLM 解析需要网络。
- 模型、地图缓存和本地连接设置保存在系统用户目录，不在解压目录。
- 默认附近类别数据覆盖北京国贸周边；具体名称目录覆盖北京部分地点。其他地区需导入相应地点数据。
- 程序没有代码签名证书。是否允许运行请以你核对的下载来源和校验值为依据。

在不同 Windows 设备上未做完整兼容性测试。本次验证为 Windows x64 本机解压运行、真实数据查询、模型加载及 GUI 初始化。

## Python 用户 / Linux / macOS

下载 `geoask-0.2.0-py3-none-any.whl`，在 Python 3.11–3.13 虚拟环境中安装：

```bash
python -m pip install geoask-0.2.0-py3-none-any.whl
geoask-gui
```

Linux 可能需要通过系统包管理器安装 `python3-tk`。wheel 不包含依赖，pip 安装时需要网络；macOS/Linux 没有本次发布的原生桌面压缩包。

需要修改源码时，下载 GitHub 自动提供的 Source code，或 `geoask-0.2.0.tar.gz`，解压后在项目目录运行：

```bash
python -m pip install -r requirements.txt -e .
python geoask_gui.py
```

## 校验下载

`SHA256SUMS.txt` 包含附件的 SHA256。Windows PowerShell 示例：

```powershell
Get-FileHash .\geoask-0.2.0-windows-x64.zip -Algorithm SHA256
```

## 数据和许可证

代码采用 MIT 许可；示例数据来自 Geofabrik / © OpenStreetMap contributors，遵循 ODbL 1.0，详见包内数据署名和仓库 README。第三方 Python 组件许可证保存在 `THIRD_PARTY_LICENSES`。首次下载的模型遵循其模型许可证。

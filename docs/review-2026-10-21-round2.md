# geoask 第二轮复核报告（修复之后）

- 日期：2026-10-21
- 背景：第一轮报告见 [review-2026-10-21.md](review-2026-10-21.md)；修复记录见 [review-fixes.md](review-fixes.md)；Git 基线 `f67a39a`，修复当前仍在工作区
- 方式：4 个独立复核方向（核心/打包、GUI/地图/路线、测试/CI/产物、全新视角数据/脚本），只读审查 + 本地复现；未修改项目源码、配置、数据或 Git 状态
- 基线：`pytest -q` → **123 passed, 1 skipped**；`GEOASK_MODEL_TESTS=1` → **124 passed**
- 覆盖率：总 **52%**（geoask.py 90%、routing 95%、location 95%、service 93%、data 97%、runtime 93%；**GUI 14%**、**map 42%**）

## 总体结论

第一轮提出的 **6 个高优先级问题全部真实修复**，并且修复质量总体可信：打包资源、wheel 安装、路线中文指引、3.10 定位时间戳、类别缓存重建、字段大小写、别名清洗、原子写入、日志、无头测试、CI、README 与验证产物等都已独立复核通过；测试数从 80 增加到 124，GUI/地图覆盖率从 0%/15% 提升到 14%/42%。

但第二轮发现 **2 个新的高优先级问题**（其中一个会直接杀死进程），以及一批修复引入或仍未处理的回归与遗留项。因此当前工作区还不能视为“已完成”状态。

---

## 一、已确认修复（逐项复核结果）

| 第一轮问题 | 复核结论 | 证据 |
| --- | --- | --- |
| 模糊名称候选在屏幕外、状态误报“已完成” | **已修复** | 真实数据“去星巴克”：177 个名称匹配、10 个候选、`selected_index=None`；拟合点包含起点+候选，zoom 14，全部候选在视口内；状态为“请选择具体地点”；`docs/gui-ambiguous.png` 与 JSON 均一致。单点上限 15 级已生效。 |
| GUI `_poll` 一次异常永久冻结 | **已修复** | `drain_events` 逐事件隔离 + `finally` 重排 + `_event_error` 恢复 busy + `report_callback_exception` 写日志；注入异常后后续事件继续处理。 |
| 普通安装缺资源、入口不可用 | **已修复** | 构建的 wheel 含 `geoask_resources/{config.yaml,data/*.gpkg,manifest.json,scripts/get_location.ps1}`；在仓库外普通安装后 CLI、GPKG、本地 HTTP API、GUI 初始化均通过（`scripts/verify_wheel.py` 两次运行通过）；sdist→wheel 重建内容一致；dist 哈希与 reliability JSON 一致。 |
| Valhalla 语言/单位层级错误、导航英文 | **已修复** | 请求改为 `directions_options={language:zh-CN,units:kilometers}`；真实公共实例仍返回 en-US，程序用转向类型/道路/距离生成中文模板，无英文泄漏；真实抓包渲染为“从左侧出发（呼家楼西里南街），行进约 108 米…”等。 |
| Python 3.10 七位小数秒定位失败 | **已修复** | 解析前截断到 6 位；覆盖 7 位/6 位/3 位/Z/±offset；版本范围改为 `>=3.11,<3.14`，与 lock/CI 一致。 |
| “停车场”被映射为 `park` | **基本修复（但见新问题 2）** | 停车词不再进入嵌入映射，不再返回公园；但新守卫会误杀合法查询。 |
| 类别缓存损坏/维度不符 | **已修复** | 空/zip/错误行数/NaN/零向量/维度不符均重建；写缓存失败时内存向量仍可用。 |
| 字段映射大小写敏感 | **已修复** | `Name/Category/GEOM`、catalog 映射、大小写不同的别名均可解析；导入大写字段成功。 |
| 空别名绕过导入校验 | **已修复** | 标量/数组/GDAL JSON 文本/null/空白别名均清洗；无名称数据拒绝导入。 |
| “不明确/unknown/未知”哨兵 | **已修复** | 三类哨兵在进入类别模型前被拒绝；CLI/GUI 均不再返回无关 POI。 |
| CLI 失败退出码/NaN 半径 | **已修复（有细节）** | 有效 `--dry-run`=0；语义失败/空结果/名称未找到/NaN 半径=1；但“名称候选待确认”仍返回 0，且会先打印“结果 (0 条)”再报错。 |
| 配置/CSV 原子写、导入不再依赖硬链接 | **已修复** | `atomic_output` 的 overwrite/异常清理/BOM/换行行为验证通过；`os.link` 被 patch 掉后导入仍成功；已有配置不被覆盖（O_EXCL）。 |
| 设置对话框两页互相丢弃编辑 | **已修复** | 两个按钮统一调用一个 apply，同时校验两页并保留会话密钥；真实 GUI 验证通过。 |
| 失败后旧站点控件仍可交互 | **已修复** | 新查询同步清空站点控件；无结果时选择器/路线/导出禁用；真实 GUI 错误阶段断言 `not app.stop_combos`。 |
| 请求代际 ID | **已修复** | 所有 3 元组 job 事件带 generation 并统一过滤；location 事件为 2 元组但由原始坐标/ busy 自保护。 |
| 瓦片缓存写失败丢图 / 坏图停更 / 缓存无限增长 | **已修复（有 P3 残留）** | 写缓存失败仍返回有效图；坏缓存重取；坏图不再停止轮询；启动/切图时按 7 天+128 MiB 清理。残留：单文件锁会让清理整体中止；解码失败无退避（见三）。 |
| 名称快照硬编码 | **部分修复** | 3 处改为读 manifest/选择有效候选；`tests/test_geoask.py` 仍有 200–500 的范围断言。 |
| 日志/测试/CI/文档/截图 | **已修复/可信** | 滚动日志、`tests/test_reliability.py`、wheel 冒烟、CI 文件、README 措辞、GUI 报告时间戳与代码哈希（10/10 匹配）、初始/路线/模糊候选截图互异，均已核实；CI 尚未实际执行。 |

---

## 二、新发现的高优先级问题

### 1. P1：错误的图层名会让整个进程原生崩溃（不是 Python 异常）

- 位置：`geoask.py:186-190`（`source_select` 里 `DESCRIBE SELECT * FROM ST_Read(?, layer=?)`），入口包括 `load_data`（`geoask.py:231`）、地名目录（`geoask_places.py:28-30`）、地区导入（`geoask_data.py:28`）和 GUI 导入的“图层”输入框。
- 复现：
  ```powershell
  .\.venv\Scripts\python.exe -c "import geoask as g; con=g.connect_spatial(); con.execute('DESCRIBE SELECT * FROM ST_Read(?, layer = ?)', [r'data\beijing_poi.gpkg','POI'])"
  # 退出码 -1073741819 (0xC0000005，access violation)，无 traceback
  ```
  正确大小写 `poi` 正常。GeoJSON 加上 `layer: poi`、错误的 `place_catalog_layer` 同样崩溃。
- 影响：这是“换自己的数据”的主路径。一个大小写/拼写错误就会让 pythonw 直接消失，没有错误提示、没有日志；GUI 的 `except Exception` 也接不住原生崩溃。
- 建议：在 `DESCRIBE` 前用 `ST_Read_Meta(?)` 取图层名，大小写不敏感解析；不存在则抛 `GeoaskError("图层不存在：…；可用图层：…")`；对不可打开文件同样转成中文错误。回归测试必须用子进程（进程内无法断言崩溃）。
- 参考：DuckDB 的 `ST_Read_Meta` 对两个内置 GPKG 与 GeoJSON 都能返回图层列表。

### 2. P1/P2（回归）：停车词守卫扫描整句查询，会拒绝合法的非停车查询

- 位置：`geoask.py:433-440`（`validate_resolved_intent` 对 `intent.intent + " " + query` 做停车正则），调用点 `geoask.py:532`、`geoask_service.py:149/184`。
- 复现（LLM 意图本身正确）：
  - “找一家有停车位的咖啡店”（intent=咖啡）→ 抛出“要查找附近停车场，请先选择目的地…”；
  - “停车方便的餐厅”“停车场旁边的咖啡店”“商场哪里有停车位”同样被拒；
  - 同样的词放进 **两站** 行程却能通过（`_run_plan` 对多站传空 query），单站/多站行为不一致；
  - CLI 报错还让用户去勾选一个 GUI 才有的复选框。
- 影响：普通“带停车位的餐厅/咖啡店”类查询在搜索前就被拒绝，属于修复批次引入的功能回归。
- 建议：只对解析后的 intent/modifiers 判断停车（或仅在 intent 本身是停车词、且没有 place_name 时拦截）；单站/多站行为统一；CLI 给独立文案；补“有停车位的餐厅/咖啡店”回归测试。

### 3. P2：相对路径 `--config config.yaml` 会把缓存静默迁到用户目录

- 位置：`geoask.py:161`：`if path.resolve() == DEFAULT_CONFIG.resolve() and path.parent != ROOT:`（`path.parent` 未 resolve）。
- 复现（仓库根目录）：
  - `read_settings(g.ROOT/"config.yaml").cache_dir` → `D:\...\geoask\.cache`
  - `read_settings(Path("config.yaml")).cache_dir` → `C:\Users\...\AppData\Local\geoask`
- 影响：同一个配置文件因传参写法不同而使用不同缓存；相对 `--config`（README 文档写法）会触发一次约 0.22 GB 的嵌入模型重新下载，tiles/logs 也换位置。
- 建议：比较 resolve 后的 parent，或判断 `DEFAULT_CONFIG` 是否位于 `ROOT` 之下。

### 4. P2：混合“数组/标量”别名列不再按分隔符拆分，精确别名降级为待确认

- 位置：`geoask.py:204-216`：`if cols[column].endswith('[]')` 分支只做 `list_transform(CAST(...))`，不做 `;；|` 拆分。
- 复现：同一 GeoJSON 中某要素 `alt_name: ["甲别名1"]`、另一要素 `alt_name: "丁别名1;丁别名2"`。GDAL 把整列推断为 `VARCHAR[]`，丁馆别名保存为 `['丁别名1;丁别名2']`；查询“丁别名1”得到 `name_rank=2`（名称包含，需要用户确认），而不是 rank 0 的精确别名校验。
- 影响：README 承诺“别名列支持数组，或 `;`、`；`、`|` 分隔字符串”；混合导出（OSM 中很常见）会静默失去精确别名匹配能力。
- 建议：两个分支统一对每个元素再做 `regexp_split_to_array` 并 flatten，再清理空值；加混合数组/标量测试并断言 rank 0。

### 5. P2：空 `place_aliases` 值会把命名查询匹配到未命名地点并自动选中

- 位置：`geoask_places.py:53-56`：`canonical = aliases.get(name_key(place_name), place_name)`；若别名配置值为空，`name_key("")` 为空，SQL 的 `name_key = ''` 会匹配未命名记录。
- 复现：`place_aliases: {鸟巢: ""}` + 一条未命名（停车）记录，查询“去鸟巢看演唱会” → 返回该未命名记录 `name_rank=0, name_match_count=1`，`_choose_stops` 自动 `selected_index=0`，随后按这个停车场规划路线，并在说明里声称“按名称/已核实别名查找‘鸟巢’…已选定”。
- 影响：违反“找不到就明确提示、绝不替换成其他地点”的核心承诺；手写 YAML 中空值是常见笔误。
- 建议：`Settings` 校验 `place_aliases` 的 key/value 去空白后非空；`build_place_query` 对空 key 抛错或回退原名称；同时保留第一轮建议的“别名目标不存在时回退原名”。

### 6. P2：自定义数据/导入路径直接显示原始 DuckDB 英文错误

- 位置：`geoask.py:186-247`（`source_select`/`load_data` 不包装 `duckdb.Error`）；GUI 导入错误对话框/CLI 直接显示 `str(exc)`。
- 复现：
  - `source_crs: EPSG:999999` → `InvalidInputException: Could not create projection: EPSG:999999 -> EPSG:4326`；
  - 几何字段映射到文本列 → `Binder Error: No function matches … ST_IsEmpty(VARCHAR)` + SQL 片段；
  - 非空间/损坏文件 → `IO Error: Could not open GDAL dataset …`；截断 GPKG → `database disk image is malformed`。
- 影响：这是“换自己的数据”的主路径；用户看到的是多行英文内部错误，而不是“请核对文件格式/图层/字段映射/源坐标系”。
- 建议：在 `DESCRIBE`、`ST_Read`、`ST_Transform` 和最终查询处捕获 `duckdb.Error`，按阶段转成中文 `GeoaskError`；保留原始异常链到日志。与问题 1 一并处理。

### 7. P2：规则里的“自定义 parking 类别数据集”语义查询不可达

- 位置：`validate_resolved_intent` 在查看数据类别之前就拒绝停车词；`find_parking` 又要求已有选定目的地。
- 影响：如果用户的地区数据本身有 `parking` 类别、想直接“附近停车场”，现在既不能语义查询（被拦截），也不能用停车模式（还没选目的地），只剩名称搜索。
- 建议：把拒绝延后到类别词表已知之后，若数据含 `settings.parking.categories` 则允许按类别搜索；或增加“附近停车场类别”专用路径。

### 8. P2：`scripts/verify_gui.py` 在干净检出上仍会失败（第一轮 #37 未修）

- 位置：`scripts/verify_gui.py:29`：`tempfile.TemporaryDirectory(dir=g.ROOT / '.cache')`；`.cache/` 被 `.gitignore` 忽略，且 `GeoaskApp()` 在此时不会创建它（地图使用用户缓存目录，语义模型尚未加载）。
- 影响：README 推荐运行的真实 GUI 验证脚本在全新 clone 上第一行就 `FileNotFoundError`；而 GUI 报告/截图的唯一生成路径正是它。
- 建议：脚本开头 `mkdir(parents=True, exist_ok=True)`，或改用系统临时目录；同时把 `finish()`/`app.close()` 放进 `finally`。

### 9. P2：CI 从未执行，且 Linux 任务很可能因 Tk 依赖失败

- 位置：`.github/workflows/tests.yml:7-22`；`tests/test_reliability.py` / `tests/test_travel.py` 通过 `geoask_gui`/`geoask_map` 导入 `tkinter`；`scripts/verify_wheel.py:48` 也导入 `geoask_gui`。
- 风险：ubuntu 任务没有安装 `python3-tk`/Xvfb，若 runner 的 Python 不含 `_tkinter`，3 个 Linux job 会在收集阶段直接失败；Windows 的 wheel 冒烟在无桌面会话时也可能失败。
- 建议：ubuntu 增加 `sudo apt-get install -y python3-tk`（不需要显示，只要模块可导入），或在测试/冒烟前用 `pytest.importorskip("tkinter")` 保护；给 job 加 `timeout-minutes`；CI 真正跑一次再宣布矩阵可用。

### 10. P2：GUI 接线覆盖率仍只有 14%，关键路径只靠人工脚本

- 数据：pytest 覆盖率 `geoask_gui.py` 14%（842/975 行未覆盖）、`geoask_map.py` 42%；`_show_result`（模糊候选拟合/状态接线）、`_queue_job`/`_work` 代际传递、设置对话框构造与 apply、停车/错误展示等均未被 pytest 覆盖。
- 影响：新测试覆盖的是抽出的纯函数（`result_map_points`、`validate_settings_form` 等），但“`_show_result` 是否真的调用它们”“设置 apply 是否真的改到两个 Tab”这类接线回归仍会让 CI 全绿；唯一端到端证据是需显示+网络的 `verify_gui.py`，且它在干净检出上失败（问题 8）。
- 建议：增加 withdrawn-root 的 GUI 集成测试（`_show_result`、设置对话框、代际事件），或把事件处理进一步抽成无 Tk 的控制器；在 reliability JSON 中明确“GUI 端到端为人工验证”。

---

## 三、其余新发现（P3，按影响排序）

1. **路线距离单位未校验**：`geoask_routing.py:72-74/118` 假设 `trip.units == kilometers`；自部署 Valhalla 若返回 miles，距离会静默变成 1.6 倍误差。建议读取 `trip.units`，非 km 则转换或拒绝。
2. **中文起步指引有误导**：`geoask_routing.py:61-65` 使用 `begin_cardinal_direction`，但真实 Valhalla 响应只有 `bearing_after`；该分支是死代码，实际输出“从右侧出发（呼家楼西里南街）”，而服务原文是“Walk west on 呼家楼西里南街”，丢失了行进方向。建议用 `bearing_after` 生成 8 方位，并把测试 fixture 换成真实响应字段。
3. **瓦片清理遇到第一个被锁文件就整体中止**：`geoask_map.py:65-85` 一个 `unlink` 失败会放弃整轮清理，Windows 杀软/第二实例可让 7 天/128 MiB 上限失效。建议逐文件 `try/except OSError` 继续。
4. **瓦片解码失败没有失败退避（可形成重试循环）**：`geoask_map.py:274-288` 中 `fetch_tile` 的 `verify()` 对截断 JPEG 不报错（已实测：`validate_tile` 通过、`convert("RGB")` 抛 OSError），`_handle_tile` 抛异常后未写 `failed[key]`，下一次 redraw 会立即重新入队，可能形成约 100 ms 的下载/解码/重绘循环并刷日志。建议在 `_handle_tile` 异常时写 `failed[key] = monotonic()+45`。
5. **设置对话框仍显示 Pydantic 英文原始错误**：`geoask_gui.py:546-553` 只 catch `ValueError` 并 `str(exc)`，非法 tile_url 会弹出 `1 validation error for MapSettings … For further information visit https://errors.pydantic.dev/...`。建议取 `exc.errors()[0]["msg"]` 并中文化。
6. **`close()` 不幂等**：`geoask_gui.py:1134-1139` 第二次 `close()` 抛 `TclError: application has been destroyed`；建议 `if self.closed: return`。
7. **换点失败被报成整次查询失败**：`geoask_gui.py:702-704/798-805` 把所有非 route/parking/import 异常映射为 `error`，但旧 `self.result` 和站点控件仍保留，界面出现“这次查询没有完成”+仍可操作的状态。建议单独 `reselect_error`，保留结果只更新提示。
8. **地图 fit 对经度 +180 处理错误**：`geoask_map.py:144-145` 的 `% 360` 会把 +180 归一成 -180，含精确 +180 的数据集会得到错误视野（中国数据不涉及，但属于静默错误）。建议用 `math.remainder` 并特殊处理 ±180。
9. **设置 apply 的副作用与代际**：`geoask_gui.py:546-563` 每次 apply（哪怕只改模型）都会重配地图、清空 photos、启动清理线程、清空路线；且没有 `self.generation += 1`。当前 busy 时按钮禁用所以不可达，但属于防御缺口。
10. **`preview`/`auto_route` 复选框未在 busy 时禁用**（第一轮 #31）：查询进行中可切换“仅预览/自动规划”，UI 与实际请求不一致。
11. **`atomic_output` 语义边角**：`geoask_runtime.py:32-59` 不创建父目录；`overwrite=False` 先创建空目标再 replace，进程被杀会留下 0 字节配置；`finally` 的 unlink 可能抛错掩盖原异常；覆盖已有文件会改变 POSIX 权限。建议可选的 parents、独占创建、best-effort 清理。
12. **日志/事件辅助的竞态**：`geoask_runtime.py:18-28` 的 check-then-add 在并发首次调用时可能重复挂 RotatingFileHandler；`drain_events` 第 73 行 `event[0]` 对非下标事件会在恢复路径再次抛错。
13. **zip 导入不安全**：`geoask_resources` 用 `as_file` 只解出 config/ps1，`data/` 仍在 zip 内，`read_settings` 相对路径会指向解压临时目录。wheel 总是解压，故仅 zipapp/pex 场景；可文档说明或统一走 resources。
14. **`_prepare` 失效判断仍弱**（第一轮 #19）：`geoask_service.py:104-107` 仅 mtime+size，catalog 两次 stat（TOCTOU），且签名包含 map/parking/cache 设置导致无关重载。
15. **`parse_intent` 仍泄漏 OpenAI client**（第一轮 #22）：`geoask.py:273-278` 未用上下文管理器。
16. **`read_settings` 仍不捕获 `UnicodeDecodeError`**（第一轮 #24）：GBK/UTF-16 配置在 GUI `choose_config` 中会变成通用“界面处理出错”；同时未 `expanduser`、false-y YAML 根被当成 `{}`、路径解析在 try 外。本轮另发现 `source_crs` 等原始英文错误（问题 6）也走这条路径。
17. **名称归一化 Python/DuckDB 不一致**（第一轮 #18，仍开放）：`geoask_places.py:9-10` 的 Python `\s` 含全角空格/NBSP，而 `:38-41` 的 DuckDB/RE2 `\s` 只匹配 ASCII。实测数据名 `"广州\u3000塔"` 用“广州塔”与“广州　塔”都查不到（0 结果）；`lower()` 对 U+0130 等也不同。建议统一一套归一化实现；这是 East-Asian 数据集的 P2 风险。
18. **空别名/别名 fallback** 见问题 5；此外 `place_aliases` 目标不存在时仍未回退原名（第一轮 #26）。
19. **停车场候选截断/可读性**（第一轮 #20）：仍使用通用 `limit`，大量“未命名停车场”只能靠距离区分，5000 m 时无“还有更多”提示。
20. **数据准备脚本健壮性**（第一轮 #39/#40）：`prepare_data.py` 用 `next(...)`/`z.read('README')`，上游布局变化会抛 StopIteration/KeyError；中断留下的截断 GPKG 会被后续静默复用；`prepare_places.py` 先替换输出再做国家体育场唯一性校验/manifest；manifest 非原子写。建议先校验、临时文件+原子替换、哈希固定。
21. **启动器/文档/元数据**：`启动界面.cmd` 仍不检查 pythonw/版本、`start` 失败静默、总返回 0；`pyproject.toml` 仍缺 readme/license/urls/classifiers，版本 0.2.0 在 UA 中手写重复；`README` 仍称 `requirements-dev.txt` 提供“演示渲染依赖”（实际只加 pytest 和重复的 Pillow）；`docs/trip-verification.json` 仍是 12:31 的旧产物且无时间戳/哈希；作者遗留的 `build/lib` 里有旧模块副本（已忽略，但建议清理）。
22. **环境提示（非代码缺陷）**：当前 `.venv` 的 editable 安装元数据仍是新增 `geoask_resources` 之前的版本，因此 `.venv\Scripts\geoask.exe --help` 从任意目录（包括仓库根）都会 `ModuleNotFoundError: geoask_resources`；从源码目录运行 `python geoask.py` / `启动界面.cmd` 不受影响。重新执行一次 `python -m pip install -e .`（或干净安装 wheel）即可；这也说明升级说明里应写“新增了顶层包，需重装 editable 安装”。

---

## 四、第一轮遗留项状态

- **已修复**：GUI 模糊候选/轮询冻结/旧控件/设置对话框/代际 ID；瓦片写失败/坏图/清理；路线中文；定位时间戳；缓存损坏；字段大小写；别名清洗；哨兵；CLI 退出码/NaN；原子写；打包；日志；CI 文件；截图与报告来源；Tkinter 文档、离线措辞、Python 范围。
- **仍开放**：名称归一化（#18，P2）；`_prepare` 失效与过宽签名（#19）；停车场候选截断（#20）；`parse_intent` client 泄漏（#22）；`finish_reason` 严格/`travel_mode` 必填（#23）；`read_settings` 编码与 `~`/false-y 根（#24）；`load_data` NULL 坐标检查（#25，实际影响低）；别名 fallback（#26）；`modifiers` 无上限（#27）；e5 两侧同前缀（#28）；`preview`/`auto_route` busy 态（#31）；`close` 幂等（#32）；启动器（#36）；`verify_gui` 干净检出（#37，升级为 P2）；数据准备脚本（#39/#40）；`inspect_named_places` 导入即执行（#41）；`render_demo` 字体（#42）；`trip-verification` 旧产物（#43）；`requirements-dev` 措辞（#44）；`pyproject` 元数据（#46）。

## 五、建议修复顺序

1. **图层名原生崩溃（问题 1，P1）**：`ST_Read_Meta` 预校验 + 大小写解析 + 中文错误 + 子进程回归测试。这是当前最严重的可用性问题。
2. **停车守卫误杀（问题 2，P1/P2 回归）**：只检查解析后的 intent/modifiers，统一单站/多站行为，补“有停车位的餐厅/咖啡店”测试。
3. **相对 config 缓存迁移（问题 3）** + **verify_gui 干净检出（问题 8）** + **CI Tk 依赖（问题 9）**：三个都是“验证/使用路径”上的基础设施问题，修复成本很低。
4. **别名与错误处理（问题 4/5/6）**：混合别名拆分、空别名校验与回退、DuckDB 错误中文化。
5. **名称归一化（#18，P2）**：统一 Python/DuckDB 的空白与大小写规则，补 U+3000/NBSP 测试。
6. **GUI/路线/地图 P3 批次**：单位校验、起步方向、prune 逐文件、tile 失败退避、设置错误中文化、close 幂等、reselect 错误、+180、apply 副作用。
7. **测试/CI/文档收尾**：GUI 接线无头测试、CI 首次实际运行、wheel 依赖检查、reliability 来源哈希、trip 产物、build/ 清理、pyproject 元数据；最后统一 `pip install -e .` 刷新环境并提交工作区。

## 六、复核方法与证据

- 4 个独立复核方向并行：核心/打包、GUI/地图/路线、测试/CI/产物、全新视角（places/parking/data/scripts）。
- 真实数据/模型/路线：本地缓存 FastEmbed、`data/beijing_poi.gpkg`、`data/beijing_places.gpkg`、`.downloads/route-probe.json`；未调用付费 API，未下载瓦片。
- 打包：从源码构建 wheel 到 `%TEMP%`，检查包内容；运行 `scripts/verify_wheel.py`（通过）；确认 dist wheel/sdist 哈希与 reliability JSON 一致；sdist→wheel 重建一致。
- 测试：默认 123+1 skip、`GEOASK_MODEL_TESTS=1` 124 passed；覆盖率 52%；用“重新引入旧行为”的方式验证新增测试确实会失败（非空洞断言）。
- GUI：无头 withdrawn-root + stub 地图探针；真实 GUI 证据来自 `docs/gui-verification.json`/截图（代码哈希 10/10 匹配当前源码）。
- 所有临时脚本/构建产物在 `%TEMP%`；项目源码、配置、数据、Git 状态未修改（仅 `build/`、`egg-info` 等被忽略的构建缓存）。

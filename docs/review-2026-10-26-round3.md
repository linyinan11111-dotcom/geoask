# geoask 第三轮复核报告（2026-10-26，HEAD `b102580`）

- 复核范围：当前 HEAD `b102580`（工作区干净）；新增本地密钥保存、低置信度名称回退、Windows 便携版打包、数据管线重写
- 基线：`pytest -q` → **214 passed, 1 skipped**；`GEOASK_MODEL_TESTS=1 GEOASK_REQUIRE_GUI=1` → **215 passed**
- 方式：4 个独立方向（密钥安全、名称回退、发布/打包/CI、数据管线与全新视角）+ Lead 独立复核；只读，未改项目源码/配置/数据/Git
- 公开信息核对：GitHub API（latest release、tags、CI run）、真实 wheel 安装冒烟、desktop ZIP 内容、临时凭据存储、Tk 组合框探针

## 总体结论

这一轮代码质量明显提升：上一轮的图层原生崩溃、停车守卫、别名归一化、缓存、瓦片、发布资源、GUI 冻结等问题都已修复并被独立复现；**当前 HEAD 的 CI 6/6 通过**（run 40327635877），214/215 项测试通过，wheel/sdist/desktop ZIP 内部资源一致，冻结版能加载捆绑的 DuckDB spatial 扩展。

但出现了 **3 个 P1 级问题**（发布文档指向不存在的 0.2.1 资产、保存的 API Key 在改接口后被带到新服务商、Unicode/IDNA 主机名混淆导致跨主机复用密钥），以及 **0.2.1 桌面包未验证、验证脚本版本盲** 的发布完整性问题。其余是 P2/P3 级健壮性与文档问题。

---

## 一、P1：必须优先修

### 1. README/下载说明宣传的 0.2.1 资产并不存在，最新发布仍是 v0.2.0
- 位置：`README.md:11`、`docs/release-download.md:7,22,31,43`
- 证据：两处都让用户从 `releases/latest` 下载 `geoask-0.2.1-windows-x64.zip` / `.whl` / `.tar.gz`。GitHub API：latest release 为 **v0.2.0**，只有 `geoask-0.2.0-*` 与 `SHA256SUMS.txt`；`/releases/tags/v0.2.1` 返回 404，tags 只有 `v0.2.0`。本地 `dist/` 有 0.2.1 构建物，但未发布；`dist/SHA256SUMS.txt` 仍只有 3 个 0.2.0 哈希。
- 影响：按当前 README 操作的 Windows/Python 用户会找不到 0.2.1 附件；v0.2.0 发布页与当前代码/文档不一致。
- 建议：**发布 v0.2.1**（重新构建并上传 ZIP/wheel/sdist/SHA256SUMS，打 tag），或**先把所有文档回退到 0.2.0**。增加发布前检查：文档里的每个资产名必须存在于 release assets 和 SHA256SUMS 中。

### 2. GUI 中已保存的 API Key 会在修改接口地址后被带到新服务商
- 位置：`geoask_gui.py:576`（设置页 API Key 输入框用 `self.api_key.get()` 初始化，包含启动时恢复的保存密钥）、`geoask_gui.py:591-608`（apply 直接读取该字段并保存/设置会话密钥）、`geoask_gui.py:460-474`（恢复逻辑）；apply 没有比较旧/新 `LLM` 的服务身份。
- 复现（真实 Tk + 临时凭据存储）：为服务商 A 保存密钥 `KEY-A`；启动 GUI（自动恢复）；打开设置，只把 `base_url` 改成服务商 B、不改动被掩码的 API Key 字段；点击应用。结果保存文件变成 B + `KEY-A`，且 `api_key_is_saved=False`，下一次查询会把 A 的密钥发送给 B。
- 影响：密钥被写入/发送到用户并未为其输入密钥的主机；与 README/架构文档“换服务商不会自动发送旧服务商密钥”的承诺直接矛盾。
- 建议：记录“恢复密钥所属的服务身份”和输入框初始值；apply 时若 `credentials.same_connection(old_llm, new_llm)` 为 false 且用户没有改动密钥字段，清空密钥并要求重新输入/确认；显式改动过的密钥才允许保存；补 GUI 回归测试。

### 3. Unicode/IDNA 主机名在凭据身份判断中相等，但实际解析到不同主机
- 位置：`geoask_credentials.py:59-64`（`parsed.netloc.casefold()`）、`:139-142`（`saved_api_key`）、`geoask.py:326-333`（`resolve_api_key`）、`geoask_gui.py:433/473`
- 证据（本机复现）：保存 `https://strasse.de/v1` 的 canary 密钥后，当前 `LLMSettings(base_url="https://straße.de/v1")` 的 `same_connection` 返回 True，`saved_api_key`/`resolve_api_key` 都返回 canary；而 `httpx.URL` 的 raw host 分别是 `strasse.de` 与 `xn--strae-oqa.de`，是两个不同主机。`ß → ss`、连字等 casefold 展开都有类似风险。
- 影响：一个 Unicode/confusable 端点可收到为 ASCII 主机保存的密钥；CLI 与 GUI 自动读取保存密钥的路径都受影响。
- 建议：使用与请求层一致的 IDNA/URL 规范化（例如 `httpx.URL` 或 `idna.encode(..., uts46=True)`），比较 ASCII 主机 + 规范化端口 + path/query + `api_key_env`；规范化失败时拒绝复用；增加 `straße.de`/`strasse.de`、大小写、默认端口、query 顺序测试。

### 4. 0.2.1 桌面包未被验证，且 `verify_desktop.py` 是“版本盲”的
- 位置：`docs/desktop-verification.json`（version 0.2.0、archive `geoask-0.2.0-windows-x64.zip`、旧 `geoask.py` 哈希）、`docs/release-verification.json`（version 0.2.0、commit 4530312、212 tests）、`scripts/verify_desktop.py:43-53`、`scripts/desktop_entry.py:17`
- 证据：当前源码/版本是 0.2.1 / `b102580` / 214 tests；`desktop-verification.json` 的 `code_sha256.geoask.py` 与当前源码不一致。`verify_desktop.py` 只检查子进程返回码和 `report['passed']`，**不比较 report version、archive 文件名/哈希、`frozen` 标志、解压出的 exe 哈希**，所以用旧的 0.2.0 ZIP 也能写出“passed”报告（现有 JSON 就是这种情况）。
- 影响：没有证据证明带新回退行为的 0.2.1 桌面 ZIP 实际运行过；发布完整性不可信。
- 建议：`verify_desktop.py` 必须断言期望版本（来自 `geoask_resources.__version__`）、archive 版本/`SHA256SUMS` 哈希、`frozen is True`，最好记录构建 commit/source/resource/extension 哈希；为 0.2.1 重新生成 desktop/release 报告，或删除旧报告并明确标注历史。

---

## 二、P2：高风险 / 回归

### 5. 地区导入把带 token 的在线服务 URL 写入明文 YAML（密钥泄漏）
- 位置：`geoask_gui.py:543`（`base.llm, base.map, base.routing = self.llm, self.map_settings, self.routing_settings`）、`geoask_data.py:36-40`（`model_dump_json()` 后写 YAML）
- 证据：若 `map.tile_url`、`routing.endpoint` 或 `llm.base_url` 含 `?access_token=...`/`?key=...`，导入地区配置会把它们原样写进地区 YAML；瓦片服务把 API Key 放在 XYZ URL 中很常见。
- 影响：用户分享/提交地区配置时会泄漏付费 token；与 README:166“真实密钥不写入地区导出”不一致（`api_key` 字段确实没写，但 URL 内嵌 token 会写）。
- 建议：导入时不要把在线服务端点复制进地区 YAML，或先剥离 userinfo/query 中的敏感参数并提示；增加 canary URL 回归测试；`MapSettings.valid_tile_url` 也应拒绝 userinfo/可疑凭据。

### 6. 损坏 profile 的深层 JSON 触发 `RecursionError`，GUI 启动可能直接崩溃
- 位置：`geoask_credentials.py:96-108`（只捕获 `OSError/ValueError/TypeError/KeyError`）、`geoask_gui.py:463-476`（只捕获 `LocalSettingsError/ValueError`）
- 复现：`connection.dat` 内容为 `GEOASK-JSON-1\n` + 6 万个 `[` + `]`，`load_connection()` 抛 `RecursionError`；`_restore_connection` 未捕获，启动路径崩溃。文档承诺“损坏文件提示清除/重新保存”。
- 建议：加载处捕获 `RecursionError`（或兜底 `Exception`）转 `LocalSettingsError`；`_restore_connection` 兜底并保持应用可用；补深层 JSON 测试。

### 7. POSIX 凭据文件加载不校验权限/属主/普通文件/符号链接
- 位置：`geoask_credentials.py:88-108`（直接 open，跟随符号链接，无 lstat/mode/owner 检查）、`:117-125`（保存时预/后 `chmod` 会跟随符号链接）
- 影响：POSIX 上密钥是明文，`0600` 是主要保护；从备份/复制得到 0644、外来属主或符号链接指向的文件会被静默读取；符号链接还可能让保存的 chmod 作用到其他文件。测试在 Windows 上跳过了 mode 断言，未覆盖这些情况。
- 建议：`lstat` 要求普通文件、属主为当前用户、无 group/other 权限；拒绝符号链接（`O_NOFOLLOW`/lstat）；`load` 发现权限过宽可 chmod 或报错；POSIX CI 补测试。

### 8. 非法 `base_url` 以原始 `ValueError` 冒泡，配置切换可能留下半更新 GUI 状态
- 位置：`geoask_credentials.py:59-64/83-85/139-142`、`geoask_gui.py:433`、`geoask.py:326-333`；`LLMSettings` 没有 `base_url` 校验器
- 复现：`saved_api_key({"base_url":"not-a-url",...})` 与 `same_connection(valid, invalid)` 抛 `ValueError: invalid endpoint`；`_load_config` 在 `session_key` 恢复处未捕获，可能在 settings/config_path/llm 已修改后中断，留下不一致 UI；CLI/GUI 显示英文错误。
- 建议：`same_connection` 对非法 URL 返回 False（校验留在 save/load）；`resolve_api_key` 捕获 ValueError 转中文 `GeoaskError`；`_load_config` 局部兜底；给 `LLMSettings.base_url` 增加与 Map/Routing 一致的校验。

### 9. GUI 站点下拉框的鼠标滚轮会绕过“请选择具体地点”的确认并自动规划路线
- 位置：`geoask_gui.py:900-907`（readonly Combobox + `<<ComboboxSelected>> -> reselect_stop`）、`:114`（`auto_route` 默认 True）、`:818-824`（全选后自动规划）
- 证据（Tk 8.6 本机探针）：对只读 Combobox 调用其类绑定 `ttk::combobox::Scroll` 一次，显示从“请选择具体地点”变为“1. 海底捞火锅 · 900 米”，`current()` 从 -1 变 0，触发 `reselect_stop(0,0)`；真实处理器随后设 selected_index 并在 auto_route 下自动规划到未确认候选。
- 影响：b102580 新增的大量“名称候选需确认”回退结果可被一次滚轮绕过，直接规划路线，违背确认承诺；停车/出行方式下拉框也有类似风险。
- 建议：为站点下拉框绑定 `<MouseWheel>`/`<Button-4/5>` 返回 `"break"`，或改为显式“确认所选地点”按钮；补 GUI 测试。

### 10. CLI fallback 把“类别词巧合命中名称”当作结果输出并返回 0
- 位置：`geoask.py:683-695`、README:141/150
- 复现（真实数据、stub 解析）：`酒店` → “结果 (1 条): 酒店 [nightclub] 14865m”，无“待确认”提示，exit 0；`电影院` → cinema 41.7km；`观光` → 观光车/观光园等 exit 0。GUI 仍会要求确认（安全），但 CLI 没有选择流程。
- 影响：与 README:141“类别不匹配返回非零”和 README:150“映射被拒绝时列出可用类别，不生成查询”不一致；用户可能把偶然同名 POI 当成目标。
- 建议：fallback 结果始终输出“名称候选（需确认）”标记，并明确退出码语义（无候选非零、有候选是否 0 要在 README 固化）；`--dry-run` 的 fallback SQL/可用类别输出也要与文档一致；补 CLI 测试。

### 11. 自定义数据中 parking 类别大小写不一致时，目的地停车模式查不到停车场
- 位置：`geoask.py:529-534`（`parking_match` 用 `casefold` 匹配）vs `geoask_parking.py:35-43`（SQL `category IN (SELECT unnest(?::VARCHAR[]))` 精确匹配原始配置字符串）
- 复现：合成数据类别 `parking`/`Parking`/`地下车库`；配置 `parking.categories=["PARKING"]` 时，直接“附近停车场”能匹配，但目的地停车模式 `total_count=0`。GIS 导出常用 `Parking`/`PARKING`，配置复制默认小写时不匹配。
- 建议：`find_parking` 先按 `casefold` 解析实际数据类别（复用/扩展 `parking_match`），把真实类别字符串绑定进 SQL；加大小写不匹配测试。

### 12. 地图瓦片失败后不会在退避到期时自动重试，只有用户交互才恢复
- 位置：`geoask_map.py:307-317`（`_poll` 只有收到事件才 `redraw`）、`:227`（45 秒退避在 redraw 内检查）、`:288-302`（失败记录）
- 证据：失败 backoff 已过期但队列为空时，`_poll` 不调用 `redraw`；可见瓦片全失败（临时网络/429）时地图会一直空白 + 错误条，直到用户拖动/缩放。`failed` 字典也从不清理。
- 建议：`_poll` 中检查是否存在到期失败项，主动 `redraw`（并清理过期键），或安排一次性 `after` 重试。

### 13. `prune_tile_cache` 会删除瓦片缓存目录下任意无关文件
- 位置：`geoask_map.py:68-94`
- 证据：对包含 `documents/important.txt`（8 天前）和 `documents/old-screenshot.png` 的目录运行 `prune_tile_cache`，两者都会被删除；`max_bytes` 预算也会删除无关 `.png`。缓存目录是用户可配置的，GUI 用 `cache_dir/tiles`；若该 `tiles` 目录混放用户文件，会不可逆丢数据。
- 建议：只删除匹配 `<provider16hex>/<z>/<x>/<y>.png` 布局的文件，其他文件一概不动；可选校验 cache_dir 不是项目/数据根目录。

### 14. 0.2.1 验证证据、校验和与发布自动化不完整
- 位置：`docs/gui-verification.json`、`docs/reliability-verification.json`、`docs/trip-verification.json`、`docs/desktop-verification.json`、`docs/release-verification.json`、`dist/SHA256SUMS.txt`、`.github/workflows/tests.yml`
- 证据：
  - GUI/reliability 报告的 `code_sha256` 中 `geoask.py`/`geoask_gui.py`/`geoask_service.py` 与当前 HEAD 不一致（当前 6755b46d/76e43bdf/e49cc191，报告是 80ac7b07/b82a7f0a/12d7b31b）；README:329 仍称这些是“生产代码 SHA256”的当前证据。
  - desktop/release JSON 仍是 0.2.0 / commit 4530312 / 212 tests，而当前是 0.2.1 / b102580 / 214 tests；0.2.1 桌面 ZIP 未被验证。
  - `dist/SHA256SUMS.txt` 只有 0.2.0；没有生成 0.2.1 校验和的脚本或 release job；CI 当前 HEAD 6/6 通过，但不构建/验证 desktop ZIP、不生成/核对 SHA256SUMS、不发布资产。
  - `build-desktop` 的 `build-environment.json` 同时包含 geoask 0.2.0 与 0.2.1，0.2.1 ZIP 里还带 `THIRD_PARTY_LICENSES/geoask/geoask-0.2.0` 旧许可；没有记录构建 commit/clean tree/resource/extension 哈希。
- 建议：在发布前重新生成 GUI/trip/reliability/desktop/release 报告（记录 version/commit/test count/hash）；增加 tag 触发的 Windows release job：构建 wheel/sdist/desktop → verify_wheel/verify_desktop → 生成 SHA256SUMS → 上传；清理构建环境的旧版本成分并记录来源。

---

## 三、P3：体验、契约与工程细节

1. **Fallback UI 文案/范围不准**：`geoask_gui.py:910-922` 对 `resolution=="name-fallback"` 仍打印“搜索半径 1000 米”，`:956-958` 的 notice 却显示“附近类别查询使用当前 POI 文件”，实际查的是全局名称目录且不受半径限制；`geoask_service.py:184-197` 建议“增大范围”对名称搜索无效。应按 resolution 分支显示“名称目录（主数据 + 配置地名目录，不受半径限制）”。
2. **Fallback note 状态不随选择变化**：`geoask_service.py:71-72` 无条件追加“名称候选仍需确认”，无候选时也提示确认，已选后仍显示；无类别数据时仍说“未达到类别匹配阈值”。应区分无候选/待确认/已确认/无类别。
3. **Fallback 丢失总匹配数**：`name_match_count`（如火锅 117）未在 `_name_fallback_stop`/GUI/CLI 提示，只显示最近 10 条；应像停车总数一样显示“共 N 个，仅显示最近 M 个”。
4. **`run(planning=False)` 与 planning 路径在无类别数据上不一致**：`geoask_service.py:161-168` 在到达 fallback 前抛“没有类别信息”，planning 路径却会名称回退；统一行为或文档化。
5. **CLI/GUI 对无类别数据的停车意图不一致**：CLI 在 categories 为空时跳过 `match_intent` 走名称回退；GUI/service 调用 `match_intent` 的 `parking_match` 抛“没有汽车停车场类别”。统一规则。
6. **Fallback 忽略 `place_candidates`**：`geoask_service.py:184-197`、`geoask.py:674-683` 只用 `intent.intent`；若 LLM 把俗称只放进 candidates 而没有 place_name，会被丢弃。可传入候选或校验该组合。
7. **Fallback GUI 分支测试/验证缺口**：GUI `name-fallback` 说明分支、`_render_candidates` scope 未被 pytest 覆盖；desktop/GUI 验证脚本没有回退场景；CLI 只有多候选测试，无“酒店”这类 exact-single、dry-run、退出码契约测试。
8. **CSV 导出错误暴露原始 WinError/临时路径**：`geoask_service.py:299-311`、`geoask_gui.py:1205-1216`；应转中文提示（文件被 Excel 占用/目录不可写），旧文件保留行为本身正确。
9. **`import_region` 细节**：`geoask_data.py:20` 传入字符串 alias 会按字符拆成 `['别','名']`；`:35` 把绝对 `base.cache_dir` 写入地区 YAML 导致不可移植；`:14-17` 目录与后缀错误共用提示。
10. **配置读取错误过于笼统**：`geoask.py:168-171` 把文件缺失、YAML 错误、字段 typo、非法值都合并成一句；可保留字段级中文提示（不含 pydantic URL）。
11. **数据管线维护性**：`prepare_places.py:41-69` 只校验国家体育场唯一性和行数，没有目录规模/parking 数量范围；`prepare_data.py:59-62` 在 publish 之后写 README，进程中断会留下数据/README 不一致；`verify_data.py:27-28` 首次下载可能超时；失败后旧 `data-verification.json` 仍保留 `"passed": true`；`render_demo.py` 写 committed 资产非原子；`publish_dataset` 不防并发写者。
12. **死代码/契约**：`validate_resolved_intent(intent, query="")` 的 query 参数已无用途但仍传入；`SemanticMapper.match` 的停车分支在应用路径不可达且用默认 `ParkingSettings()`；`_work` 无 unknown kind 的 else，未来新 job 会让 busy 永久 True；`find_parking(radius=True)` 会把 bool 绑定到 SQL 比较并抛 BinderException。
13. **凭据 P3**：save 接受 >128 KiB 而 load 拒绝（前后不对称）；`_validate` 只检查 map/routing 是 dict、version `True==1` 也能过；`_restore_connection` 两次加载 profile、第二个失败可能留下半更新状态且 `api_key_is_saved=True` 但 key 为空；`remember_connection` 每次打开对话框重置 True；env 变量检查未 `.strip()`；`_crypt` 每次调用改写共享 ctypes argtypes，存在线程竞态；`connection_file` 对相对 XDG/LOCALAPPDATA 路径不做规范化；身份判断对默认端口/尾点/query 顺序有 false negative。补测试与文档。
14. **发布文档细节**：`MANIFEST.in` 未包含 `requirements.lock.txt`（sdist 里缺失）；README:317“其他矩阵需由 CI 执行”已过时（当前 HEAD CI 已 6/6 通过）；release-verification 引用旧 run/commit；`启动界面.cmd` 无法捕获 GUI 崩溃；`desktop ZIP` 内 exe 在 `geoask/` 子目录，说明文字未明确。
15. **测试计数文档**：`docs/api-local-storage.md` 仍写 211/212，当前 214+1 skipped。

---

## 四、已确认修复 / 验证通过

- 上一轮 P1：模糊名称地图不可见、`_poll` 冻结、非 editable 安装缺资源、英文导航、Python 3.10 定位、停车场→park；以及图层原生崩溃、字段大小写、别名清洗、停车守卫、缓存重建、原子写、导入硬链接、设置对话框、旧控件、代际 ID，均通过代码复核/复现/回归测试。
- 当前 HEAD CI：run 40327635877（6/6 成功）；v0.2.0 发布资产与 SHA256SUMS 一致；0.2.1 wheel/sdist/desktop ZIP 内部资源一致；`verify_wheel.py` 在 0.2.1 wheel 上通过；冻结入口能加载捆绑 spatial 扩展。
- 密钥正常流程：DPAPI 结构/标志/LocalFree/密文无明文、跨进程同用户恢复、普通 provider 差异不重用、显式输入 > 环境变量 > 保存密钥、CLI 不覆盖 YAML 端点、错误信息不泄漏密钥、测试/脚本隔离真实凭据；除上述 P1/P2 边界外，正常路径可靠。
- Fallback 触发逻辑安全：只在 `match.category is None` 时触发；`_name_fallback_stop` 总是 `selected_index=None`，GUI 不会自动选中/自动规划；place_name、停车、unknown 路径不被回退影响；dry-run 不执行名称搜索；多站后续站被阻塞，选择后重算；SQL 参数化无注入。
- 名称归一化、混合别名拆分、字段大小写、无类别数据名称搜索、数据管线可复现（415/29,131/4,776 与 bundled 一致）、manifest 回滚、地图投影/缓存基础、`atomic_output` 无部分文件、parking 核心语义、CSV 公式转义、配置/导入基础行为均验证通过。

---

## 五、建议修复顺序

1. **发布一致性与凭据安全（P1）**：
   - 发布 v0.2.1（ZIP/wheel/sdist/SHA256SUMS + tag）或回退所有 0.2.1 下载说明；
   - GUI apply 的“恢复密钥跨 provider”保护；
   - IDNA/Unicode 主机身份规范化；
   - 重新验证 0.2.1 desktop ZIP，并让 `verify_desktop.py` 断言 version/archive hash/frozen。
2. **P2 安全/数据完整性**：地区导入 token 剥离、损坏 profile 兜底、POSIX 权限/符号链接、非法 base_url、停车类别大小写、瓦片清理范围/重试。
3. **Fallback UX 与 CLI 契约**：下拉框滚轮 `break`、CLI 候选提示/退出码、README:141/150、UI scope/半径/总匹配数/note 状态。
4. **发布/验证自动化**：tag 触发的 release workflow、SHA256SUMS 生成、报告 hash/version/test count 自动核对、清掉构建环境旧 0.2.0 成分。
5. **P3 清理**：CSV/导入/配置错误文案、数据管线健壮性、死代码、凭据边界测试、文档计数与 lock 文件。

## 六、复核方法与证据

- 4 个独立方向并行：凭据安全（临时存储，真实 Tk）、名称回退（真实缓存模型/数据、无头 Tk、CLI 探针）、发布/打包/CI（GitHub API、ZIP/wheel 内容、冻结 exe/扩展）、数据管线/全新视角（隔离重放、各模块探针）。
- 关键复现：`straße.de` vs `strasse.de` 的 `saved_api_key`/`resolve_api_key` canary；Tk readonly Combobox 滚轮触发 `<<ComboboxSelected>>`；CLI `酒店` fallback 输出；GitHub latest release v0.2.0 / tags / CI run；报告 code hashes 与当前源码对比。
- 所有测试/探针未调用付费 LLM、未下载瓦片/路线（除公开只读 API），未改动项目文件；临时脚本在 `%TEMP%`。

# AI-3.0 数据管理

AI-3.0 是独立的原始文件、工况、样本与后续标签/数据集管理项目。当前实现第一阶段；不包含模型训练、模型推理、标签业务和数据集业务，也不在运行时读取或修改 AI-2.0。

## 数据位置

新建数据库时只填写名称，此时仅建立主页记录，不生成 SQLite。随后在基础信息中选择数据库路径，原始文件放在所选根目录中，SQLite 固定放在该根目录的 `databases` 子目录内：

```text
<所选数据库路径>/
├── databases/<database_name>.sqlite3
└── <原始数据目录与文件>
```

打开数据库时，可选择数据库根目录或 `databases` 目录；旧的 `_ai3/databases` 布局仍可读取。打开后会自动把该数据库根目录绑定为 `wuxi_raw`。空数据库可以在详情页修改根目录；已有文件记录的数据库在当前快速版中会拒绝迁移。

数据库只保存 `storage_id + relative_path`。原始数据扫描会排除 `databases`、旧 `_ai3`、临时文件及 SQLite 辅助文件。

## 当前功能

- 仅输入名称即可建立主页记录；选择数据库路径后才生成 `<路径>/databases/<名称>.sqlite3`。
- 也可选择数据库文件夹打开；打开时同时绑定该数据库自己的根目录。
- 数据库卡片主页与详情页；显示名称可编辑，SQLite 物理路径保持只读；主页“移除”只删除列表记录，不删除 SQLite 或原始文件。
- 根目录内登记或从根目录外导入 `.tdms`、`.tdms.zst`、`.wav`。
- 原始数据导入在当前详情页弹窗完成；标签导入暂为空白弹窗。
- 数据面板以页面内 Tab 打开，不新建浏览器窗口。
- 界面明确区分“根目录内登记”和“根目录外导入”，选择与实际路径不一致时拒绝写入。
- 导入先做只读文件/样本预览，确认信息未变化后才执行发布与登记。
- 裸 TDMS 先流式压缩、解压校验、TDMS 打开校验和样本发现，再原子发布并登记。
- 批次工况完全来自导入请求；固定字段包括产线、采集、项目、型号、负载单值、转速比单值和采集时间，并可增加自定义字段。
- 工况卡片由同一套字段定义生成，导入、更新和数据面板按各自能力复用；负载和转速比均为单值。
- 工况支持按所选文件、指定文件夹、指定文件或整个数据库批量修改；自定义字段保存在 `metadata_json.condition_extras`。
- TDMS 依据 `config/sample_profiles` 自动生成通道样本；当前界面不再显示手工通道映射。
- 单通道 WAV 生成 `main`；多通道 WAV 登记为待通道处理，不静默选择首通道。
- “更新”窗口可独立选择“工况更新”或“路径检查和更新”，也可同时选择；两项业务共用范围但分别提交，路径更新保持先预览再应用。
- 数据面板将文件状态、工况筛选和标签筛选分开；工况使用数据库现有值下拉多选，同字段为 OR、跨字段为 AND，负载和转速比按所选单值精确匹配。标签业务尚未启用时，标签多选控件保持禁用。
- 文件面板支持文件名模糊搜索、工况 AND、同字段多值 OR 及数值范围筛选。

## 启动

### Windows 64 位 EXE

Windows 构建配置在 `.github/workflows/windows-audio-exe.yml`。将 AI-3.0 目录作为 GitHub 仓库根目录后，在 Actions 中运行 `Windows audio labeling EXE`，下载 `AI3-Audio-Labeling-win-x64` 构建产物并解压。双击其中的 `AI3-Audio-Labeling.exe`；它会启动仅监听本机的服务并打开浏览器，关闭命令窗口即可退出。需保留 EXE 与 `_internal` 文件夹在同一目录。

程序不携带原始音频、已有标注或本机数据库。音频与按时间命名的标注 JSON（如 `label_20261001_153045.json`）由使用者在界面中选择；程序自身的主页记录及标签类别保存在 `%LOCALAPPDATA%\AI-3.0`。Windows 文件和文件夹选择使用系统对话框。

也可在 Windows 64 位电脑的 Python 3.11 环境中直接构建：

```powershell
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean packaging/ai3_audio_windows.spec
```

产物位于 `dist/AI3-Audio-Labeling/`。工作流会启动打包后的 EXE 并检查页面和接口是否可访问。macOS/Linux 不能直接生成可验证的 Windows PyInstaller EXE。

### macOS 开发启动

当前机器的 `fault` 环境已包含第一阶段依赖：

```bash
cd AI-3.0
./start_ai3.sh
```

浏览器打开 `http://127.0.0.1:8030`。

新建数据库时先选父文件夹，再输入数据库名称。程序在父文件夹下创建同名目录，其中 `databases/<名称>.sqlite3` 保存数据库，`databases/lines.json` 保存产线清单，产线原始文件仍放在该目录下。打开已有数据库时直接选择这个同名目录；旧位置的 `lines.json` 在打开时迁入 `databases` 目录。

也可以安装独立依赖后启动：

```bash
python -m pip install -r requirements.txt
python -m uvicorn web.api.main:app --host 127.0.0.1 --port 8030
```

可通过 `AI3_DATABASE_ROOT=/database/catalog` 增加启动时自动发现的数据库父目录，通过 `AI3_CATALOG_STATE=/path/to/catalog_state.json` 修改主页记录文件位置。受控测试启动仍可通过 `AI3_STORAGE_CONFIG=/path/to/storage_roots.json` 或 `AI3_STORAGE_ROOT=/temporary/root` 显式注入根目录；普通交互启动不会绑定固定数据盘。

## 快速局部验证

```bash
env PYTHONDONTWRITEBYTECODE=1 conda run -n fault \
  python -m pytest -p no:cacheprovider tests -q
```

新建主页记录后需要先选择数据库路径，之后才可导入和更新。通过首页“打开数据库”可重新加入已从列表移除的数据库；`AI3_DATABASE_ROOT` 下的数据库仍会自动发现。

## 实现边界

- 导入按单文件事务提交，使批次可以部分成功并保留逐文件报告。
- 目标同名同内容按重复处理；同名不同内容拒绝覆盖。
- 内部裸 TDMS 只有在文件发布、验证和数据库事务提交全部成功后才尝试删除；清理失败记录为 `cleanup_pending`。
- 文件已发布但数据库提交失败时保留恢复状态，重试不会覆盖已发布文件。
- 正式目标使用原子 no-replace 发布；发布锁由内核管理，进程中断后不会被残留锁文件永久阻塞。
- 内部裸 TDMS 清理与 AI-3.0 发布共用同一目标锁；清理前先移入同盘隔离目录，再次核对源、压缩目标和数据库 payload；不一致时保留文件并记录 `cleanup_pending`。
- 正式数据目录应由 AI-3.0 管理；内核锁可以序列化 AI-3.0 进程，不能强制未遵循锁协议的外部程序停止直接覆盖或删除正式文件。
- `core/algorithms` 仅为后续信号算法保留，不能直接访问数据库或文件路径。

## 当前验证状态

- 2026-09-23 通过 76 项自动化检查，覆盖延迟创建数据库路径、主页记录移除保护、文件夹递归导入、范围更新、文件名搜索、工况选项隔离、精确多选筛选和自定义工况。
- 普通启动不预设外部数据盘；新建数据库只建立名称记录，用户选择路径后才生成 SQLite。
- 选择真实数据盘后，仍需用真实归档 TDMS 完成一次压缩、登记、重启回读和更新验收。
- 当前界面显示批次级压缩/登记进度，尚未实现字节级百分比、后台任务暂停或断点续传。
- 文件面板单次最多返回 5000 条，尚未增加分页。

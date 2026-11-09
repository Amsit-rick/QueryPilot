# Query Pilot 测试交付说明

## 1. 测试范围

本交付包用于 Windows 10/11 隔离测试机上的本地功能验收。包内包含当前工作区源码、迁移、安装脚本、测试和文档，不包含 `.env`、真实密钥、本机数据库、审计日志、导出文件、Python 虚拟环境或前端依赖目录。

当前本地自动化验证已覆盖后端、前端构建、任务恢复、审批、图表、分析、质量反馈和工作台多轮对话。Oracle、SQL Server、ClickHouse 连接器仍需在真实隔离实例上单独验收，不能仅凭本地测试视为生产可用。

## 2. 前置环境

- Windows 10/11 64 位。
- Python 3.12，安装时启用 Python Launcher，使 `py -3.12` 可用。
- Node.js 22，随附 npm。
- Docker Desktop，使用 Linux Containers，并等待 Docker Desktop 完成启动。
- 首次安装依赖需要可访问 Python 和 npm 软件源的网络。

在新的 **CMD** 窗口执行：

```cmd
py -3.12 --version
node --version
npm --version
where docker.exe
docker info
docker compose version
```

`docker info` 必须正常返回。只安装 Docker Desktop 而没有启动它，仍然无法运行项目。

## 3. 首次安装和启动

将压缩包完整解压到纯英文路径，例如 `D:\QueryPilot\QueryPilot-test-日期时间`。不要在 ZIP 压缩包内直接运行脚本。

在新的 **CMD** 窗口执行：

```cmd
cd /d D:\QueryPilot\QueryPilot-test-日期时间
scripts\setup_local.cmd
```

脚本会创建 `.venv`、安装 Python/npm 依赖、由 `.env.example` 生成本机 `.env`，并为 MySQL root、控制面和只读查询账号生成三套不同的随机密码；随后启动 MySQL、执行迁移、创建本地管理员并启动前后端。数据库密码不会写在 Compose 或交付文档中。下载依赖时保留窗口运行。

本地隔离测试管理员为 `admin`，密码是首次安装生成在 `.env` 的 `LOCAL_ADMIN_PASSWORD`（与数据库密码无关，查看：`findstr /B "LOCAL_ADMIN_PASSWORD=" .env`）。首次安装会自动启用认证，在空 MySQL 库写入演示数据并创建这个管理员账号；本地开发默认选择 `mysql-main`，SQLite 演示库仍可在下拉框切换。登录后可看到数据源、审批中心、用户管理、语义层、数据网关和模型运营等完整管理入口。该密码只用于本项目的本地测试环境，不能用于生产或可从互联网访问的环境。

以后启动只需：

```cmd
cd /d D:\QueryPilot\QueryPilot-test-日期时间
scripts\start_local.cmd
```

检查整套服务：

```cmd
scripts\check_stack.cmd
```

`start_local.cmd` 每次启动都会在 MySQL ready 后幂等执行最新迁移。升级已有项目或旧 Docker 数据卷时，也必须重新运行该脚本；不要只手动启动旧的 Uvicorn 窗口。

访问地址：

- 前端：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`

不要同时运行 `docker-compose.app.yml` 和本地启动脚本，也不要绕过 `start_local.cmd` 单独启动 Uvicorn。

如果页面出现 `查询失败（500）`，先关闭旧的 API/前端窗口，在项目根目录重新运行 `scripts\start_local.cmd`，再执行 `scripts\check_stack.cmd`。项目曾出现旧 MySQL 数据卷停留在 `V014`、而应用查询已依赖 `V015` 的 `model_providers` 表，根因是迁移未在旧启动路径中执行；当前启动脚本已在启动 API 前强制补齐迁移，迁移失败会直接阻止 API 启动并显示原因。

## 4. 多轮对话验收

进入工作台后，在同一个输入框依次发送：

1. `查询各区域销售额`
2. `只看华东`
3. `再按销售额排序`
4. 确认同一消息流保留三轮问题和结果，而不是覆盖上一轮。
5. 刷新浏览器，确认三轮对话仍然存在。
6. 从“最近任务”重新进入该任务，确认三轮对话能够恢复。

继续检查：

- 追问沿用首轮数据源，不应在对话中切换数据源。
- 等待审批时输入区应禁用，审批完成后再继续。
- 失败的追问应显示错误，同时保留之前成功的结果。
- 在窄屏浏览器中输入框和结果区不应产生页面级横向溢出。

自然语言生成需要接收方在自己的 `.env` 配置有效模型密钥。没有模型密钥时，可以使用项目内置的确定性问题或显式只读 SQL 检查基础链路。不要通过聊天、截图或缺陷单发送 `.env` 内容。

## 5. Docker 故障排查

遇到 `Docker not found`：确认 Docker Desktop 已安装，然后关闭旧 CMD，重新打开 CMD 再运行 `where docker.exe`。安装程序对 PATH 的修改不会自动进入已经打开的终端。

遇到 `Docker Desktop is not running` 或 `docker info` 失败：先启动 Docker Desktop，等待界面显示引擎已经运行。仍失败时，在 **CMD** 中执行：

```cmd
wsl --shutdown
wsl --update
```

随后重启 Docker Desktop，再打开一个新的 CMD，重新执行：

```cmd
docker info
docker compose version
scripts\setup_local.cmd
```

不要因为普通启动错误执行 Docker Desktop Factory Reset，也不要注销或删除 Docker 的 WSL 数据发行版；这些操作可能清除本机其他项目的容器数据。

## 6. 反馈信息

发现问题时记录操作步骤、预期结果、实际结果、发生时间、页面地址、任务 ID 和 `scripts\check_stack.cmd` 输出。截图前检查画面中没有 `.env`、模型密钥、数据库密码或其他真实凭据。

交付包根目录的 `DELIVERY-MANIFEST.json` 包含分支、源码提交、文件数量及每个交付文件的 SHA-256，可用于确认文件是否被修改。压缩包旁的 `.sha256` 文件用于核对整个 ZIP。

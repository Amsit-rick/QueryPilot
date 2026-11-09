# 企业演示数据

项目提供 `scripts/generate_enterprise_data.py` 生成可重复的合成企业数据，默认写入 `data/demo.db`。数据库文件被 `.gitignore` 忽略，不直接提交二进制数据；其他成员在本地运行脚本即可得到同样结构的数据。

```cmd
cd /d D:\QueryPilot
.venv\Scripts\python.exe scripts\generate_enterprise_data.py --rows 500000
```

默认数据量约为：客户 50,000、产品 5,000、订单 250,000、订单明细 500,000、支付 166,666，另外生成库存、供应商和员工数据。`--rows` 是基准规模参数，`--seed` 可固定随机种子，重复执行会清空并重建这些 `enterprise_*`、`products`、`order_items`、`inventory`、`suppliers`、`employees`、`payments` 表，不会删除原有 `sales` 和 `customers` 表。

新增表适合演示跨表聚合、趋势分析、库存预警、客户分层、回款统计和区域经营分析。数据全部为合成数据，不代表真实企业记录。

## 导入 MySQL

先启动 MySQL 并确认 `query_agent` 数据库可连接，再执行：

```cmd
set MYSQL_HOST=127.0.0.1
set MYSQL_PORT=3307
set MYSQL_DATABASE=query_agent
set MYSQL_USER=query_app
set MYSQL_PASSWORD=你的query_app密码
.venv\Scripts\python.exe scripts\load_enterprise_mysql.py
```

脚本从 `data/demo.db` 读取数据，批量写入 MySQL。它只删除并重建上述企业演示表，不会修改 `sales`、`customers`、用户、审批或任务表。重复执行是安全的；`MYSQL_PASSWORD` 未设置时脚本会直接拒绝连接。正式环境请改用专用导入账号和密钥管理服务，不要把密码写入脚本或提交到 Git。

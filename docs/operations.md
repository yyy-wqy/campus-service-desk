# 备份、恢复和日志

## 日常备份

以下命令供部署负责人使用。先确认当前进程指向预期的部署数据库及附件目录，再使用运行应用的操作系统账号执行。开发阶段请按文末步骤在隔离验收库中演练。

```powershell
.\.venv\Scripts\python.exe manage.py backup_site
```

默认输出在 `.runtime/backups/YYYY-MM-DD/`，含 SQLite 数据库、`media/`、`private_documents/` 及记录文件大小和 SHA256 的清单。请使用专用备份和日志目录。Windows 会将这些目录及已有内容的访问权限限制为运行账号的 SID 和 SYSTEM，并读回权限验证。Linux 的专用目录使用 0700。备份和日志目录不得与附件目录重叠或通过 Web 服务器公开。

备份先取得 SQLite 写入保留锁，再通过另一只读连接调用 SQLite backup API，并复制附件。上传和处置服务先取得数据库写锁，因而不能在该窗口替换材料。备份和恢复均校验哈希、数据库完整性及外键，并按快照数据库的记录检查所有尚未处置的私有附件。附件缺失或内容不符时，新快照不会替换已有快照。请使用包含维修资质表的项目数据库。

每天保留一个成功快照，同一天额外执行替换当天快照，既有日期仍计入保留期。第 8 个成功备份日清理最早一天。运行结果追加到 `executions.jsonl`。失败时命令返回非零，日志记录失败类型。

`.backup.lock` 防止两个备份并发发布；进程异常退出后可能留下锁。处理前核对锁中 PID、当前进程及执行记录，确认没有备份运行，再手动移除该单个锁文件，禁止在运行中移除。备份期间短暂持有写锁，应安排在低峰时段。

## 每日计划

在部署环境中先查看计划：

```powershell
.\tools\install_backup_task.ps1 -DescribeOnly
```

在管理员 PowerShell 中注册（默认每天 Windows 本地时间03:00，可用 `-At '02:30'` 调整）：

```powershell
.\tools\install_backup_task.ps1
Get-ScheduledTask -TaskName CampusServiceDeskDailyBackup
Start-ScheduledTask -TaskName CampusServiceDeskDailyBackup
Get-ScheduledTaskInfo -TaskName CampusServiceDeskDailyBackup
```

脚本不覆盖同名任务，不保存密码；使用当前账号 S4U、有限权限，补跑错过的执行，并在失败后间隔10分钟最多重试3次。系统需允许该账号作为批处理作业登录；注册被拒绝时由机器管理员配置权限。检查 LastTaskResult 为0，并核对备份清单和 `executions.jsonl` 中的成功事件，不能只检查任务是否存在。

注册前确认任务的工作目录和数据路径对应计划备份的部署实例。运行环境若使用自定义数据位置，需为任务配置同样的环境变量。若项目移位或虚拟环境重建，检查任务中的程序路径及工作目录。

## 隔离恢复

```powershell
.\.venv\Scripts\python.exe manage.py restore_site --source '.runtime\backups\2026-11-27' --destination 'D:\campus-recovery\run-restored'
```

目标必须不存在；不覆盖当前库或已有恢复目录。工具先完整校验，再复制，并再次校验。恢复后在独立进程中配置 `DJANGO_DATABASE_PATH`、`DJANGO_MEDIA_ROOT`、`DJANGO_PRIVATE_DOCUMENT_ROOT` 指向恢复目录，使用新的运行目录，检查账号、审核材料、历史关联及材料访问权限后再决定切换应用。

## 日志

默认 `.runtime/logs/application-YYYY-MM-DD.jsonl` 和 `security-YYYY-MM-DD.jsonl`。记录时间、事件、级别、路由名称、响应状态、处理耗时及可用的用户ID；登录失败账号只记录 SHA256 标识。应用错误保留异常类型；不序列化异常消息、原始请求路径和查询参数、POST、密码、邮件链接或附件内容。写入失败在进程标准错误输出安全的 `log_write_failed` 事件，业务请求继续执行。

按日期写文件，仅清理超过90天的指定日志文件，90天边界仍保留；业务历史模型和附件不受日志清理影响。清理在新日期首次写日志时执行，不保证精确删除到期当天。监控需检查日志文件最近更新时间、进程标准错误及备份退出码。不得将控制台邮件后端用于对外部署，以免在服务终端输出重置链接。

## 重新执行验收

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-validation.txt
$env:VALIDATION_ROOT=Join-Path (Get-Location).Path '.runtime\validation\run-new'
.\.venv\Scripts\python.exe -m tools.validation.prepare
$env:DJANGO_SETTINGS_MODULE='tools.validation.settings'
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8765 --noreload --insecure
```

另一个终端执行（以上服务器仅供本机验收）：

```powershell
.\.venv\Scripts\python.exe -m tools.validation.load --root .runtime/validation/run-new
.\.venv\Scripts\python.exe -m tools.validation.browsers --root .runtime/validation/run-new --executables browser-executables.json
```

`browser-executables.json` 是浏览器标签到实际浏览器 exe 路径的映射。使用测试当天官方当前稳定主版本和前一主版本的 Chrome、Edge，各一个独立副本，不修改系统浏览器版本。测试脚本会记录真实版本，不将 Chromium 冒充 Chrome 或 Edge。浏览器用合成账号完成网页提交，支持重复执行时应新建验收目录并重新准备数据。

在停止对隔离库的请求后，设置同一 `VALIDATION_ROOT`，执行 `python -m tools.validation.restore_drill`。恢复演练验证哈希、材料、审核和工单历史、页面及权限，输出恢复记录。验收数据及随机测试密码均在忽略的私有运行目录；提交报告仅保留脱敏结果。

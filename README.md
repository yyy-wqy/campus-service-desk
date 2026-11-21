# 校园智办

校园智办是一个面向校园设施报修的服务工单平台。学生和教师提交问题，管理员受理并分派，维修人员记录处理过程，最后由报修人确认结果。项目希望让报修从“发出消息等回复”变成有责任人、有进度、可追溯的服务流程。

第一版聚焦**宿舍楼与教学楼设施报修**，采用 Django 开发。项目仍在迭代，功能交付情况请查看 [Issues](https://github.com/yyy-wqy/campus-service-desk/issues) 和 [Pull Requests](https://github.com/yyy-wqy/campus-service-desk/pulls)。

## 功能概览

以下是项目第一版的功能范围，具体交付进度见 GitHub Issues。

| 模块 | 功能 |
| --- | --- |
| 账号与身份 | 学生、教师、维修人员注册；账号密码登录；个人资料、密码找回；管理员审核教师及维修人员身份与资质。登录时无需选择角色。 |
| 地点与分类 | 维护宿舍楼、教学楼、公共区域和故障类别；根据角色、住宿信息及地点限制可提交的报修范围。 |
| 报修工单 | 填写地点、故障描述等信息；生成工单编号；查看本人或负责的工单、进度和历史。 |
| 受理与维修 | 管理员受理、设置优先级和分派；维修人员更新进度并提交结果；报修人确认解决或退回继续处理。 |
| 协作与反馈 | 取消申请、工单转派、现场照片、工单内文字沟通、服务评价与站内通知。 |
| 管理与统计 | 用户审核、基础资料管理、工单管理，以及按状态、地点和类别查看统计概览。 |

核心流程：

```text
提交报修 → 管理员受理与分派 → 维修人员处理 → 报修人确认 → 工单完成
                                  ↑             │
                                  └── 未解决 ────┘
```

工单的关键变化会记录在历史中。维修人员提交结果后，只有报修人确认解决，工单才算完成。

## 用户角色

| 角色 | 主要操作 | 工单可见范围 |
| --- | --- | --- |
| 学生 | 提交本人宿舍及教学楼的普通设施报修，查看进度并确认结果 | 本人提交的工单 |
| 教师 | 提交教学楼普通设施和教师专用设备报修，查看进度并确认结果 | 本人提交的工单 |
| 维修人员 | 查看分派任务、更新进度、提交维修结果 | 当前分派给自己的工单 |
| 管理员 | 审核身份、维护基础资料、受理和分派工单、管理取消申请 | 全部工单 |

学生、教师和维修人员在注册时选择身份；管理员账号由项目维护者创建。业务权限由服务端依据真实账号角色及工单归属判断。

## 技术栈

- 后端：Python 3.12、Django 5.2
- 页面：Django Templates、CSS、原生 JavaScript
- 数据库：SQLite（本地开发）
- 测试：Django 测试框架、Node.js 内置测试运行器

## 快速开始

需要 Python 3.12。以下命令以 Windows PowerShell 为例：

```powershell
git clone https://github.com/yyy-wqy/campus-service-desk.git
cd campus-service-desk
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

打开 <http://127.0.0.1:8000/>。如果 PowerShell 不允许激活虚拟环境，可将后续命令中的 `python` 换成 `.\.venv\Scripts\python.exe`。

macOS / Linux 使用 `source .venv/bin/activate` 激活虚拟环境，其余命令相同。

项目从环境变量读取配置；[.env.example](.env.example) 列出了可配置项，但不会自动加载 `.env`。本地可使用默认开发配置；对外部署前必须设置独立密钥、关闭调试模式，并配置数据库、邮件及附件存储。

## 项目结构

```text
accounts/    账号、认证与身份审核
locations/   楼栋、地点与故障类别
tickets/     报修工单、权限与处理流程
chat/        工单内沟通
core/        首页与公共页面
config/      Django 项目配置
templates/   页面模板
static/      CSS 与 JavaScript
docs/        需求与设计文档
```

## 测试

```powershell
python manage.py check
python manage.py test --noinput
node --test tests/js/register.test.cjs
```

数据库结构更新后运行 `python manage.py migrate`。提交代码前可执行 `python manage.py makemigrations --check --dry-run` 检查是否遗漏迁移文件。


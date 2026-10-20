# 校园智办

校园事务报修与服务工单管理平台，支持报修提交、工单分派、维修进度跟踪、双方确认完成和服务评价。

当前仓库处于第一版基础项目阶段，使用 Python 3.12、Django 5.2 和 SQLite。项目由小组独立开发，第一版以宿舍和教学楼设施报修为核心。

## 开发环境

- Python 3.12
- Django 5.2.18
- SQLite（Python 自带，无需单独安装）

## 首次运行

以下命令在 PowerShell 中执行。

```powershell
git clone https://github.com/yyy-wqy/campus-service-desk.git
Set-Location -LiteralPath '.\campus-service-desk'
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py check
python manage.py runserver
```

浏览器打开 <http://127.0.0.1:8000/>。终端显示服务器正在运行即表示基础环境安装成功；按 `Ctrl+C` 停止服务。

如果 PowerShell 禁止激活脚本，可以不激活虚拟环境，直接执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

## 本地配置

`.env.example` 列出了可配置项，但项目不会自动读取 `.env` 文件。开发时可在启动服务前通过 PowerShell 设置环境变量：

```powershell
$env:DJANGO_SECRET_KEY = "仅用于你本机的随机密钥"
$env:DJANGO_DEBUG = "True"
$env:DJANGO_ALLOWED_HOSTS = "127.0.0.1,localhost"
```

未设置时，项目使用仅限本地开发的默认值。部署时必须设置真实的 `DJANGO_SECRET_KEY`，并将 `DJANGO_DEBUG` 设为 `False`。不要提交 `.env`、真实密码或密钥。

## 常用命令

```powershell
# 检查项目配置
python manage.py check

# 模型变化后生成迁移文件
python manage.py makemigrations

# 创建或更新本地数据表
python manage.py migrate

# 启动开发服务器
python manage.py runserver
```

迁移文件需要提交到 GitHub，`db.sqlite3` 和 `.venv` 只保留在个人电脑中。

## 当前项目结构

```text
campus-service-desk/
├── accounts/           # 用户模型及后续账号功能
├── chat/               # 工单内实时文字沟通
├── config/             # Django 项目配置
├── core/               # 首页及公共页面
├── docs/               # 需求与设计文档
├── locations/          # 校园地点、楼栋及设施分类
├── static/             # 公共 CSS、JavaScript 和图片资源
├── templates/          # 公共页面模板
├── tickets/            # 报修工单、分派、状态及评价
├── .env.example        # 本地配置示例
├── .gitignore          # 不上传的本地文件规则
├── manage.py           # Django 管理命令入口
├── requirements.txt    # 固定的 Python 依赖版本
└── README.md            # 安装、运行与协作说明
```

## 已完成的基础内容

- Django 项目骨架和 SQLite 数据库配置
- 自定义用户模型：学生、教师、维修人员和管理员
- 教师、维修人员的审核状态字段
- 中文界面语言与中国时区
- 用户模型初始迁移文件
- 账号、地点、工单、沟通和公共页面模块骨架
- 各业务模块的独立路由入口
- 公共模板和静态资源目录

已接入账号注册、登录、退出及角色与审核状态校验；个人信息、报修工单以及管理员分派等业务功能仍待实现。

## 账号功能（Issue #4）

- 注册入口：`/accounts/register/`，仅提供学生、教师、维修人员三种身份，填写账号、姓名、联系方式和两次密码。
- 学生注册后为已通过；教师和维修人员注册后为待审核。审核状态及管理员权限由服务端控制，不能通过提交表单修改。
- 登录入口：`/accounts/login/`，仅输入账号和密码。验证成功后系统从数据库读取账号角色，登录请求中的角色参数不会改变身份或权限。待审核账号不能登录，审核未通过时显示审核说明。原 `/ui/login/` 地址跳转到正式登录页。
- 登录成功后进入首页。顶部“退出登录”使用带 CSRF 校验的 POST 请求结束会话。
- 业务访问由 `accounts.middleware.BusinessAccessMiddleware` 统一校验，包括已登录账号审核状态发生变化的情况。账号模块除注册、登录、退出外的页面也受此校验；后续业务视图仍需按各自要求校验角色和数据归属。
- 本次无需新增数据库迁移。自动测试使用独立测试数据库，不会创建本地演示账号。

管理员由维护者预置，不能通过注册页面创建。若使用 Django 的 `createsuperuser` 命令，需再在 `python manage.py shell` 中显式设置该账号的平台角色和审核状态（将示例账号名替换为实际账号）：

```python
from accounts.models import User
User.objects.filter(username="实际管理员账号").update(
    role=User.Role.ADMIN, approval_status=User.ApprovalStatus.APPROVED
)
```

审核管理页面由后续任务实现；本地验收可在 Django shell 中将教师或维修人员的 `approval_status` 更新为 `approved` 或 `rejected`，并按需填写 `approval_reason`，再验证登录提示。

## 手机号与邮箱去重

注册时手机号不可被其他账号使用；非空找回邮箱忽略大小写并去除前后空白后判重。重复错误显示在对应字段附近，不创建账号，并保留非密码输入。邮箱仍为选填，多账号不填写邮箱不受限制。已有用户更换找回邮箱时也不能占用其他账号的邮箱，保留自己的邮箱则允许。

数据库增加非空手机号与非空邮箱的唯一索引，阻止并发提交绕过表单检查；保存账号时将手机号去除首尾空白、邮箱去除首尾空白并转小写。普通注册及邮箱绑定遇到竞争冲突会重新检查并显示字段错误。

更新代码后需运行 `python manage.py migrate`。迁移先检查并规范存量联系人；如果规范后出现重复，会列出冲突账号 ID 并停止，不会自动删除账号或清空联系人。维护者核实并修正冲突后再运行迁移即可。邮箱大小写和首尾空白的规范化在回退迁移时不会恢复原样。

本功能测试：`python manage.py test accounts.test_contact_uniqueness`，包含数据库约束、注册与绑定竞争冲突，以及存量迁移的验证。

## 忘记密码与本地控制台邮件（Issue #14）

注册时可选填找回邮箱；已有用户登录后点击账号导航中的“找回邮箱”，输入当前密码确认绑定或更换。使用用户模型已有 `email` 字段，无需数据库迁移。未绑定邮箱且已经忘记密码的用户，需要联系维护者核验身份后处理。

登录页“忘记密码”入口为 `/accounts/password-reset/`，填写账号和绑定邮箱。匹配且账号启用、有可用密码时生成重置邮件，链接有效期 1 小时，使用成功即失效；重置后需重新登录，原会话随密码变更失效。账号角色和审核状态不改变，未审核通过的账号重置后仍不能登录业务页面。

本地 `DEBUG=True` 默认使用控制台邮件后端，**不向真实邮箱发信，也不产生邮件服务费用**。显式配置与运行方式：

```powershell
$env:DJANGO_EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
.\.venv\Scripts\python.exe manage.py runserver
```

本地验收步骤：

1. 注册账号并填写找回邮箱（可使用 `student@example.com` 这样的测试地址），或用现有账号登录后绑定。
2. 退出登录，在“忘记密码”页填写相同账号和邮箱。
3. 从运行 `runserver` 的终端中复制 `http://127.0.0.1:8000/accounts/reset/...` 链接，在浏览器打开。
4. 输入两次符合强度要求的新密码，返回登录页验证新密码可用、旧密码不可用。
5. 再次打开已用链接应显示失效。连续申请同一账号需间隔至少 60 秒。

若由后台进程启动本地服务，邮件内容会进入该进程的输出日志；本地助手启动的日志位于 `.venv/server.stdout.log`。控制台邮件包含有效重置链接，仅供开发者查看，不应暴露为公开日志；测试完可关闭服务。

自动测试：`python manage.py test accounts.test_password_recovery`。使用测试邮箱后端和控制台捕获验证，不向外发信，也不创建本地演示账号。

后续真实发信才需要 SMTP 配置，环境变量示例见 `.env.example`。配置 `DJANGO_EMAIL_BACKEND`、`DJANGO_DEFAULT_FROM_EMAIL`、`DJANGO_EMAIL_HOST`、`DJANGO_EMAIL_PORT`、`DJANGO_EMAIL_HOST_USER`、`DJANGO_EMAIL_HOST_PASSWORD` 和对应加密方式；SSL 与 STARTTLS 二选一。当前 `.env` 不会自动读取，配置需要设置到进程环境中。生产部署还需设置真实 `SECRET_KEY`、明确的 `ALLOWED_HOSTS` 和 HTTPS 可访问地址，不能使用控制台日志作为用户收信方式。

重置申请对未知账号、邮箱不匹配、停用账号等均返回相同受理提示，不在页面返回链接；限频使用 Django cache，每账号 60 秒一次。本地默认缓存仅适用于当前服务进程，生产多进程部署需配置共享缓存。

## 协作约定

每项工作从最新的 `main` 创建任务分支，通过 Pull Request 审核后合并。不要直接向 `main` 推送未检查的功能代码。

提交前至少运行：

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

项目第一版的完整业务范围和验收要求见 `docs/校园智办_第一版需求与产品设计_V1.0.docx`。

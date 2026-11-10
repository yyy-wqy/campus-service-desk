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
- 注册格式校验（Issue #10）：所有角色手机号必须为 11 位半角数字，学生账号作为学号必须为 12 位半角数字，教师和维修人员账号沿用原有规则。前后端均校验格式，不接受空格或全角数字；以字符串存储以保留前导零。失败后保留非密码信息。
- 学生注册后为已通过；教师和维修人员注册后为待审核。审核状态及管理员权限由服务端控制，不能通过提交表单修改。
- 登录入口：`/accounts/login/`，仅输入账号和密码。验证成功后系统从数据库读取账号角色，登录请求中的角色参数不会改变身份或权限。待审核账号不能登录，审核未通过时显示审核说明。原 `/ui/login/` 地址跳转到正式登录页。
- 登录失败提示（Issue #11）：不存在的账号显示“账号不存在，请检查账号或先注册”；账号存在但密码错误时显示“密码错误，请重新输入”。账号密码正确后才判断账号停用、角色及审核状态。失败后保留账号输入、清空密码，不建立登录会话。
- 登录成功后进入首页。顶部“退出登录”使用带 CSRF 校验的 POST 请求结束会话。
- 业务访问由 `accounts.middleware.BusinessAccessMiddleware` 统一校验，包括已登录账号审核状态发生变化的情况。账号模块除注册、登录、退出外的页面也受此校验；后续业务视图仍需按各自要求校验角色和数据归属。
- 更新后需运行 `python manage.py migrate`，应用手机号和邮箱唯一性约束；存量冲突处理见下文。自动测试使用独立测试数据库，不会创建本地演示账号。

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

注册时可选填找回邮箱；已有用户登录后点击账号导航中的“找回邮箱”，输入当前密码确认绑定或更换。邮箱找回复用用户模型已有 `email` 字段；本 PR 同时包含联系人唯一性迁移，更新后必须执行 `python manage.py migrate`。未绑定邮箱且已经忘记密码的用户，需要联系维护者核验身份后处理。

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

注册页角色切换与校验提示的 JavaScript 测试使用 Node.js 内置测试运行器，无需额外 npm 依赖：

```powershell
node --test tests/js/register.test.cjs
```

项目第一版的完整业务范围和验收要求见 `docs/校园智办_第一版需求与产品设计_V1.0.docx`。


## 账号资料、教师审核与登录保护（Issue #17）

已确认实施规则：本人可修改姓名、手机号、院系/部门；维修人员另可修改所属单位编号。资料入口 `/accounts/profile/` 只操作当前登录账号，登录账号、角色、审核状态不在可修改字段中；找回邮箱仍通过独立入口验证当前密码后修改。手机号格式及唯一性约束继续有效。

教师注册必须填写姓名、工号、院系/部门及手机号，无需证明附件。维修登录账号使用平台保留的 `maint_` 前缀及随机编号，注册成功后在提示中告知；单位编号独立、可选且不作为凭证。数据库全局唯一约束及冲突重试保障并发分配；已有账号不改名。

管理员通过 `/accounts/reviews/` 查看教师并通过或拒绝，拒绝原因必填。每次决定追加记录审核人、时间、结果、说明；不覆盖旧记录。资料不足可线下核验，旧教师的空部门字段在编辑时需要补齐。

登录采用用户确认的固定窗口：首次凭证登录失败开始计时，15 分钟内第 5 次失败立即锁定 15 分钟；窗口起点或锁定结束恰满 15 分钟时重置计数。锁定期间正确密码仍不能登录，重复请求不延长锁定。成功登录清零，不同账号分别记录。完整账号和密码提交才计为登录尝试；待审核、停用等拒绝也计入失败。应用登录及 Django 管理登录均受保护，直接认证后端也检查锁定状态。

状态存入数据库，更新先取得数据库写锁，防止并发丢失计数；SQLite 锁竞争作有限重试，不能以失败计数竞争绕过。原有会话在账号停用或审核状态变化后仍由业务中间件重新检查。

仅已存在的账号写入登录保护表。未知账号的完整凭证提交按服务端看到的来源地址使用缓存计数，15 分钟内第 31 次起提示稍后重试。多进程部署需配置共享缓存以统一限频计数。停用已有会话的账号后，下一次访问受保护页面会返回 403 并清除会话。

更新后执行 `python manage.py migrate`，新增部门和单位编号字段、教师审核历史及登录保护表，保留已有账号、联系方式和唯一约束。专项测试：`python manage.py test accounts.test_account_management`，包含资料白名单、审核权限与留痕、锁定边界、账号隔离及并发注册和计数验证。

## 维修资质、私有附件和补件审核（Issue #18）

本功能基于 #17 的维修账号、个人资料、管理员角色和登录保护。更新后安装 `requirements.txt` 中的 Pillow、pypdf 并执行 `python manage.py migrate`。旧维修账号保留账号和资料，初始化资质申请：未通过账号保持未通过，其余从待提交开始补齐材料，不自动获得资质审核通过状态。

维修人员注册后使用自动生成的账号登录，进入 `/accounts/qualifications/`。工作关系证明、专业资质证明每类当前材料各需1至3份；可先保存、移出旧材料、补传，齐全后统一提交。只接受实际可解析、扩展名匹配的 PDF/JPG/JPEG/PNG，单文件不超过5MiB；拒绝空文件、损坏文件和加密PDF。图片解析后重新编码，专业资质的名称、方向和附件必填。证书编号、发证机构、日期和有效期可选，支持长期有效和自定义证书类别（默认“其他”），真实性由管理员人工核验。

状态流转：待提交 → 待审核 → 已通过 / 未通过 / 待补充；待补充补齐后可重新提交到待审核。待审核及已完成审核的材料不能修改；补件时移出的附件仍保留。管理员通过 `/accounts/maintenance-reviews/` 阅读申请及附件，补充或拒绝必须填写原因；每轮保存审核人、时间、结果、说明、轮次与当轮材料关联，旧记录不覆盖。只有账号启用、账号审核通过且资质审核通过者可进入维修业务。草稿、待补充可实际登录本人材料页，受限会话只允许本人资料、找回邮箱及材料入口，不能访问任务、管理或其他申请人的材料；提交后已有会话仍受限制。

材料保存在 `PRIVATE_DOCUMENT_ROOT`（默认仓库下 `private_documents/`），与公开 `MEDIA_ROOT` 分离且被 Git 忽略。页面只提供需要登录和权限校验的 `/accounts/materials/<id>/` 下载地址，申请人本人和启用且审核通过的管理员可读取，其他用户无法通过猜测地址获取文件。下载按附件返回，禁止缓存与内容嗅探。部署时不要给私有目录配置静态文件或媒体公开映射；备份数据库时应同步备份私有附件。

管理员在资质详情页填写原因正式停用账号，记录操作人、时间和原因，同时禁用登录及维修业务。审核拒绝、临时登录锁定、移出本轮材料均不等同于正式停用，也不会启动删除。账号存续期间保留材料，正式停用后至少保留180天；没有正式停用记录或保留期未满时，禁止处置。满180天也不自动删除，由校方授权维护人员按照实际政策执行受控命令：

```powershell
python manage.py dispose_maintenance_materials --worker-id <维修用户ID> --actor-id <管理员用户ID> --reason "处置原因" --policy-reference "校方政策或审批依据" --confirm
```

命令同时检查管理员资格、账号停用状态、最新停用时间、处置原因和政策依据；先保存审计意图与附件关联，再删除文件并逐项记录处置时间。成功、失败或中断均留有审计记录；维护人员应核查失败/未完成记录后再处理。材料元数据和历史审核关联保留，处置情况在管理员资质详情页可查，不提供自动清理任务。

专项测试：`python manage.py test accounts.test_maintenance_qualifications`，覆盖文件真实内容、数量上限与并发上传、越权、完整补件闭环、受限登录、审核历史、失败清理、正式停用与180天保留边界。测试使用临时数据库和临时私有目录，不写入本地真实账号或附件。

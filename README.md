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

业务功能尚未完成。下一阶段将依次实现注册登录、个人信息、报修工单以及管理员分派流程。

## 协作约定

每项工作从最新的 `main` 创建任务分支，通过 Pull Request 审核后合并。不要直接向 `main` 推送未检查的功能代码。

提交前至少运行：

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

项目第一版的完整业务范围和验收要求见 `docs/校园智办_第一版需求与产品设计_V1.0.docx`。

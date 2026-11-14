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

## 开发与文档

项目按照 P0 核心报修闭环、P1 协作与体验增强推进。每项任务通过 Issue 描述范围和验收标准，在独立分支开发并通过 Pull Request 审核。

- [查看任务与开发进度](https://github.com/yyy-wqy/campus-service-desk/issues)
- [查看迭代里程碑](https://github.com/yyy-wqy/campus-service-desk/milestones)
- [第一版需求与产品设计](docs/校园智办_第一版需求与产品设计_V1.0.docx)

## 学生住宿、校园基础资料与报修范围（Issue #19）

已确认实施规则：

- 学生住宿信息一生一档，只记录当前楼栋。首次填写不计次、不启动冷却；同一楼栋重复保存不计次；楼栋实际变更后满 30 天（含第 30 天整）才允许下一次自助变更。限制只作用于住宿楼栋，不影响姓名、手机号等资料。
- 学生可随时在 `/locations/housing/` 查看当前楼栋、下次可自助更换时间和本人变更记录；更换被拒时显示下次可更换时间。
- 管理员在 `/locations/manage/` 维护楼栋、故障类别和公共区域，随时可修改学生住宿楼栋。管理员变更不计入学生自助次数、不重置学生 30 天冷却，并记录操作人、时间、来源及前后楼栋名称；只影响之后创建的工单，历史工单保留提交快照。
- 管理员代改住宿统一走 `/locations/manage/housing/`，写入时必须经过 `locations.housing.set_building_by_admin()`，由服务层补记录操作人、时间和前后楼栋；Django 管理后台的住宿档案为只读，不能再无痕修改。
- Django 管理后台的 locations 模型只对业务管理员开放（`role=admin` 且审核通过）：即使教师被授予 Django 模型权限，也无法在后台查看或修改楼栋、故障类别、公共区域和他人住宿档案。
- SQLite 并发写入的 `database is locked` 做有限重试；重试后仍冲突时返回“另一次换楼请求正在处理，请稍后刷新重试”的可读提示，不会返回 500。
- 面板与列表只停用、不删除：Django 管理后台同步关闭三类基础资料的删除权限，避免历史工单失去楼栋或类别。
- 公共区域使用固定选项，默认走廊、楼梯间、卫生间、大厅、其他；管理员可新增、改名、排序和停用。`其他` 全库唯一，选择时必须填写具体位置文字；房间号与公共区域二选一。
- 位置参数必须与选项一致：选择房间时提交“其他”位置文字、选择普通公共区域时提交“其他”文字，都会返回对应字段错误；只有选择“其他”并填写具体位置才通过。是否“提交了公共区域”按原始参数判断，选项被删也不影响“二选一”冲突的判断。
- 报修范围由服务端统一校验：学生可报本人宿舍普通设施和教学楼普通设施，不能报教师专用设备；教师可报教学楼普通设施和专用设备，不报宿舍；未填写住宿楼栋的学生仍可报教学楼普通设施。停用楼栋、类别和公共区域不能用于新单。
- 校验入口按主键重新读取身份、住宿、楼栋、故障类别和公共区域，调用方缓存的旧对象不能让已迁出的宿舍或已停用项通过；管理员把学生从 A 栋迁到 B 栋后，旧宿舍立即被拒绝，新宿舍可以正常报修。
- 每次提交都重新读取当前住宿信息与启用状态，页面参数不能代替服务端判断；绕过页面伪造地点类型、停用项或同时提交房间号与公共区域都会被拒绝。
- 宿舍楼仍有学生住宿档案时不能改成教学楼：业务管理页和 Django 后台共用模型校验，提示管理员先迁移关联学生，再修改楼栋类型。
- 并发写入有第二道保护：学生换楼的 UPDATE 条件同时要求“目标楼栋仍是启用中的宿舍楼”，管理员改楼栋类型则先写入取得写锁、再在同一事务内复核住宿关联。因此“管理员先改类型”和“学生先保存住宿”两种顺序都只会有一边成功，不会出现住宿档案指向教学楼。

统一校验接口（供 Issue #20 创建报修调用）：

```python
from locations.scope import validate_repair_scope

result = validate_repair_scope(
    reporter=request.user,
    building=building,
    fault_category=category,
    location_kind="room",          # 或 "public_area"
    room_number="301",
    public_area=None,              # 选公共区域时传 PublicAreaOption 实例
    public_area_other="",          # 选“其他”时必填
)
result["location_detail"]          # "301" / "走廊" / "其他（三楼东侧）"
```

校验失败抛出 `ValidationError`，错误字典的键与表单字段同名：`reporter`、`building`、`fault_category`、`location_kind`、`room_number`、`public_area`、`public_area_other`。`locations.scope.reportable_buildings()` 和 `reportable_categories()` 可直接用作表单下拉的数据源。

更新后执行 `python manage.py migrate`，新增 `locations/0002`（住宿档案、变更记录、公共区域）与 `locations/0003`（默认公共区域数据，回退时移除）。专项测试：`python manage.py test locations`，覆盖首次填写、30 天边界、并发换楼、管理员留痕、停用项、位置二选一、“其他”文字、角色范围与越权访问。

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

欢迎通过 Issue 提交问题或改进建议。

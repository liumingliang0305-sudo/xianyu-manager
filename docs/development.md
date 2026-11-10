# 开发

本机环境怎么搭见 [README 的快速开始](../README.md#快速开始)。

## 改前端

前端在 [web/](../web/)：Vite + React + TypeScript + Tailwind + shadcn/ui。用 pnpm，版本固定在 `web/package.json` 的 `packageManager` 里；
没装过的话先运行一次 `corepack enable pnpm`（Node 自带的 corepack 会按这个版本下载 pnpm）。

开两个终端，一个跑接口，一个跑 Vite 开发服务器（改代码自动刷新，`/api` 会转给 8000 端口），打开 http://localhost:5173 ：

```bash
uv run xym serve
```

```bash
pnpm -C web dev
```

页面的样式和结构按 [design.md](design.md)（颜色、字号、列表页结构、状态标签、空和出错状态），
用 Skills 做页面的流程见 [frontend-skills-workflow.md](frontend-skills-workflow.md)。

## 测试和检查

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest                  # 用到数据库的连 docker 里的 xianyu_test 库，没有会自动建
pnpm -C web lint
pnpm -C web build
```

测试用 `httpx.MockTransport` 模拟平台，不连网。每个 PR 和推到 main 的提交，GitHub Actions 会跑一遍同样的检查
（[.github/workflows/ci.yml](../.github/workflows/ci.yml)），后端连一个 PostgreSQL 16 服务容器。

## 改表结构

表结构写在 [app/schema.py](../app/schema.py)，迁移在 [migrations/versions/](../migrations/versions/)：

```bash
# 1. 改 app/schema.py
uv run alembic revision --autogenerate -m "说明"   # 2. 生成迁移，打开生成的文件检查一遍（改列名、数据要搬的得手写）
uv run alembic upgrade head                        # 3. 升级本机的库
uv run pytest tests/test_db.py                      # 4. 确认迁移和 app/schema.py 对得上、能降回去
```

## 在代码里用底座

`xianyu_core` 只负责和闲鱼通信，业务层（`app/`）只通过它访问平台：

```python
from xianyu_core import Credential, MtopClient, QRLogin

async with QRLogin() as login:
    qr = await login.start()  # qr.to_ascii() / qr.to_svg() 拿二维码
    credential = await login.wait()  # 扫码确认后返回凭证

async with MtopClient(credential) as mtop:
    user = await mtop.call("mtop.taobao.idlemessage.pc.loginuser.get")
    credential = mtop.credential  # 响应里续期过的 cookie（含免密续期），记得存回去
```

接口和协议的细节见[协议笔记](protocol-notes.md)。

## 目录结构

```
xianyu_core/     底座：只负责和闲鱼通信，不放业务逻辑
  sign.py          mtop 签名
  mtop.py          mtop 请求客户端，按错误码抛 AuthRequired / RiskControl 等异常，会话过期时先试免密续期
  guard.py         按账号的限流和风控熔断，熔断状态存在 data/guard.json
  credential.py    账号凭证（cookie）的读写
  renew.py         登录态续期：保持登录开关、免密续期（silentHasLogin）
  auth/qr_login.py 扫码登录，登录后开启保持登录
  item.py          商品接口：列表、编辑（editDetail → edit）、下架、删除；买家看的商品详情后台不调，只解析插件拿来的
  order.py         订单接口：卖出的订单列表（卖家工作台）、我买到的、订单详情、改价（price render → adjust）
  publish.py       发布商品：传图、推荐分类和属性、发货地、发布限制和违禁词检查、发布
  search.py        搜索接口：网页端搜索页同一个接口，排序和筛选，解析成商品卡片
  profile.py       自己账号的个人资料：昵称、头像、IP 属地、卖家信用、粉丝（个人主页头部接口）
  im.py            即时消息：WebSocket 连接（注册、心跳、ACK、对齐同步位点）、推送解码、会话列表、历史消息、断线重连
app/             业务层：只通过 xianyu_core 和闲鱼通信
  cli.py           命令行 xym：启动管理后台（serve）、管理后台的用户（users）
  api.py           管理后台的 HTTP 接口（FastAPI），xym serve 启动，顺带托管 web/dist
  db.py            本地库（PostgreSQL）：连接、启动时确认表结构是最新的
  schema.py        表结构；Alembic 从这里生成迁移
  users.py         管理后台的用户：密码（argon2id）、刷新令牌、插件令牌、闲鱼账号归属
  auth.py          登录鉴权：访问令牌（JWT）、签名密钥、输错密码限次
  accounts.py      账号（data/accounts/<unb>.json），用完把续期的 cookie 写回，顺手记下登录态
  login.py         登录闲鱼：扫码（后台任务跑 QRLogin，前端轮询进度，每个用户同一时间只有一张码）和导入浏览器的 Cookie
  keepalive.py     定时确认登录态：每 30 分钟查一次，会话过期就免密续期，失效的只在续期有希望时再试
  matrix.py        账号矩阵：每个账号的昵称头像（一天拉一次）、备注，在售 / 待发货 / 未读的概况
  items.py         商品管理：同步、查询、详情（editDetail）、存插件拿来的浏览收藏、改价（带保护线）、下架、删除、改动记录
  drafts.py        发布草稿、编辑商品、重新上架
  wordcheck.py     本地违规词检查：按 forbidden_words.toml 的词表查描述，只提醒，不请求闲鱼
  forbidden_words.toml  违规词表（类别、档位、依据、替换建议），前端不另存一份
  places.py        发货地按名字搜：用自己的高德 key 调高德的关键字搜索
  images.py        草稿和拼多多货源的图片（data/images，按内容哈希存一份），两边都不用了才删
  orders.py        订单管理（卖出的、买到的）：按订单号添加、刷新、查询、实时详情、待付款订单改价、改动记录；从交易消息记订单
  autosync.py      自动同步商品和订单：交易消息触发、打开页面时数据旧了、定时兜底，结果记进 sync_jobs
  messages.py      消息：每个账号一条即时消息连接，新消息存库、通知页面（SSE），连上先补拉，停下了等条件变了再连；回复
  search.py        搜索闲鱼上的商品（别人的），一次一页，不存库
  stash.py         搜索暂存：搜索结果里挑出来的商品存一份（搜到时的样子 + 搜索词 + 插件拿来的详情），按用户存
  pdd.py           拼多多货源：插件推来的商品整理、存库、下载图片，照它建发布草稿的描述
  agent.py         AI 助手（PydanticAI）：带着一个拼多多货源、一件暂存和模型对话，流式回复，对话按用户存
web/             管理后台前端：Vite + React + TypeScript + Tailwind + shadcn/ui
  src/lib/api.ts       后端接口和类型；请求都带访问令牌，过期了自动换新的
  src/lib/auth.ts      登录状态：访问令牌放在内存里，打开页面时拿 cookie 里的刷新令牌换
  src/components/ui/   shadcn/ui 组件（在 web/ 里 pnpm dlx shadcn@latest add 生成，可以直接改）
  src/features/        按页面分：auth、items、publish、orders、autosync、messages、search、accounts、pdd、agent
extension/       Chrome 插件，见 docs/extension.md
migrations/      Alembic 迁移（alembic.ini 在项目根目录）
deploy/          部署脚本和服务配置，见 docs/deployment.md
tests/           单元测试
third_party/     拷进来的开源代码（规矩见 third_party/README.md）
docs/            文档
```

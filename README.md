# xianyu-manager · 闲鱼管理系统

一个自托管的闲鱼店铺管理后台：多个闲鱼账号的商品、订单、消息放在一个网页里管，另外带商品搜索、发布、拼多多货源导入和 AI 文案助手。

和闲鱼通信的底座 `xianyu_core`（扫码登录、mtop 签名、即时消息、限流熔断）用 Python 自己实现，思路参考开源项目（调研见 [docs/open-source-research.md](docs/open-source-research.md)）；上层业务全部自研。

> 自用项目，还在开发中。部分功能（发布、自动续期、订单自动入库）代码和测试都有了，但还没在线上完整跑通，详见[功能说明](docs/features.md)。

## 功能

- **多账号**：扫码登录，一个后台用户名下可以有多个闲鱼账号，"账号矩阵"一张表看全部账号的登录态、待发货、未读
- **商品**：同步、改价、编辑、下架、删除、重新上架，每次改动有记录
- **发布**：草稿 → 检查 → 发到闲鱼，分类和属性用平台推荐，发布前查发布限制和违禁词
- **订单**：卖出的和买到的分开看，从交易消息里自动记订单、跟着刷新状态
- **消息**：即时消息长连接实时收消息，在网页上直接回复
- **自动同步**：订单有变化、页面数据旧了、定时兜底，三条路自动同步商品和订单
- **搜索和暂存**：搜闲鱼全站在售商品，挑出来的暂存下来，看完整描述和图片
- **拼多多货源**：浏览器插件在拼多多商品页一键导入，照它建发布草稿
- **AI 助手**：带上货源和同行商品和大模型聊文案、标题、定价
- **账号安全**：每个账号限流，碰到风控自动熔断 30 分钟，交给人工处理

## 技术栈

| 部分 | 用的什么 |
|---|---|
| 后端 | Python 3.12、FastAPI、SQLAlchemy、Alembic、PostgreSQL 16、PydanticAI |
| 前端 | Vite、React、TypeScript、Tailwind CSS、shadcn/ui |
| 浏览器插件 | Chrome 扩展（manifest v3），同步登录、导入拼多多商品 |
| 部署 | systemd、Caddy（HTTPS） |

```
xianyu_core/   底座：只负责和闲鱼通信（签名、登录、即时消息、限流熔断），不放业务逻辑
app/           业务层和管理后台的 HTTP 接口，命令行 xym
web/           管理后台前端
extension/     Chrome 插件
migrations/    数据库迁移
deploy/        部署脚本和服务配置
docs/          文档
```

## 快速开始

需要：

- [uv](https://docs.astral.sh/uv/)（Python 3.12 以上）
- Docker（跑本机的 PostgreSQL；Mac 上用 OrbStack 或 Docker Desktop）
- Node.js 20 以上（打包前端；pnpm 用 Node 自带的 corepack 装）

```bash
uv sync                                   # 装后端依赖
docker compose up -d                      # 启动本机的 PostgreSQL
uv run alembic upgrade head               # 建表；以后拉了新代码也跑一次
uv run xym users add <用户名>              # 建管理后台的用户，提示输入密码
cp .env.example .env                      # 可选：AI 助手、高德 key 这些配置填在这里

corepack enable pnpm
pnpm -C web install && pnpm -C web build  # 打包前端；前端代码更新后再跑一次

uv run xym serve                          # 接口和页面都在 8000 端口
```

浏览器打开 http://127.0.0.1:8000 ，用刚建的用户登录，再点「登录闲鱼」用闲鱼 App 扫码。

想让闲鱼登录自动续期、导入拼多多商品，再装上[浏览器插件](docs/extension.md)。

## 部署

线上的结构是：

```
浏览器 ──https──▶ Caddy ──▶ xym serve（127.0.0.1:8000，systemd）──▶ PostgreSQL
```

后台本身只听本机、明文 http，对公网由 Caddy 提供 HTTPS。大致步骤：

1. 服务器上装 uv，建运行用的 `xym` 用户，在 `/etc/xym.env` 里写数据库连接串，装好 [deploy/xym.service](deploy/xym.service)；
2. 装 Caddy，用 [deploy/Caddyfile](deploy/Caddyfile) 提供 HTTPS；
3. 在自己电脑上发版，打包前端、传代码、装依赖、跑迁移、重启服务一步完成：

   ```bash
   deploy/deploy.sh <ssh 主机>
   ```

4. 建管理后台的用户：

   ```bash
   deploy/xym.sh users add <用户名>
   ```

完整步骤（数据库放在另一台服务器时的 SSH 隧道、证书、日志、连线上库）见[部署文档](docs/deployment.md)。

## 配置

本机写在 `.env`（从 [.env.example](.env.example) 复制），服务器上写在 `/etc/xym.env`。

| 变量 | 用途 |
|---|---|
| `DATABASE_URL` | 数据库连接串，不设时连本机 docker 里的库 |
| `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`XYM_AGENT_MODEL` | AI 助手用的模型，默认配的是魔搭社区 |
| `AMAP_KEY` | 高德 key，发布页按名字搜发货地 |

详见[配置文档](docs/configuration.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [功能说明](docs/features.md) | 每一页能做什么、怎么做的、有什么限制 |
| [浏览器插件](docs/extension.md) | 安装、同步闲鱼登录、导入拼多多商品、拿闲鱼商品的详情 |
| [配置](docs/configuration.md) | 环境变量、数据库、高德 key、AI 模型、本地数据目录 |
| [用户和登录](docs/auth.md) | 后台用户和闲鱼账号的归属、用户管理命令、鉴权设计 |
| [部署](docs/deployment.md) | 服务器安装、发版、HTTPS、日常维护 |
| [开发](docs/development.md) | 改前端、测试和 CI、改表结构、目录结构 |
| [限流和风控熔断](docs/guard.md) | 限流额度和熔断规则，为什么这么定 |
| [协议笔记](docs/protocol-notes.md) | 闲鱼网页端接口和即时消息协议 |
| [前端设计规范](docs/design.md) | 管理后台的颜色、字号、页面结构 |
| [开源项目调研](docs/open-source-research.md) | 闲鱼相关开源项目的对比和许可证 |
| [以后要做](docs/todo.md) | 还没定下来做的事 |

## 原则

- **许可证干净**：只复用 MIT 或 Apache-2.0 的代码，GPL / AGPL 只参考思路（见 [third_party/README.md](third_party/README.md)）。
- **底座和业务分开**：平台协议一变，只需要改 `xianyu_core`。
- **账号安全优先**：内置限流和熔断；碰到滑块或身份验证时交给人工处理，不做自动破解。
  账号凭证（cookie）等同于密码，只存在本机的 `data/` 里（文件权限 0600），不要外传。

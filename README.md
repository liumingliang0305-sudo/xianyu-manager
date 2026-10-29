# xianyu-manager · 闲鱼管理系统

自用的闲鱼店铺管理系统。底层协议（登录、扫码、签名、即时消息）参考开源实现，上层业务功能自己定制。

## 现在的进度

- [x] 开源项目调研 → [docs/open-source-research.md](docs/open-source-research.md)
- [x] 协议笔记 → [docs/protocol-notes.md](docs/protocol-notes.md)
- [x] 底座方案：Python 自建 `xianyu_core`，结构参考 goofish-cli
- [x] mtop 签名和请求客户端（[xianyu_core/mtop.py](xianyu_core/mtop.py)）
- [x] 纯 HTTP 扫码登录（[xianyu_core/auth/qr_login.py](xianyu_core/auth/qr_login.py)）
- [ ] 登录后开启"保持登录"、cookie 定时续期
- [ ] 即时消息（WebSocket + MessagePack）
- [ ] 限流和风控熔断
- [x] 商品接口：列表、详情、编辑、下架、删除（[xianyu_core/item.py](xianyu_core/item.py)）
- [x] 业务层第一块：商品管理，同步到本地 SQLite、改价、改描述、下架、删除、改动记录（[app/items.py](app/items.py)）
- [x] 管理后台第一页：商品管理（FastAPI 接口 [app/api.py](app/api.py) + Vite / React / shadcn/ui 前端 [web/](web/)）
- [ ] 业务功能：订单、消息、自动回复、自动发货……

## 快速上手

需要 [uv](https://docs.astral.sh/uv/) 和 Python 3.12 以上；管理后台的前端还要 Node.js 20 以上。

```bash
uv sync                                      # 安装依赖
uv run xianyu login                          # 终端里显示二维码，用闲鱼 App 扫
uv run xianyu whoami data/accounts/<unb>.json  # 检查登录态是否还有效
uv run pytest                                # 跑测试
```

商品管理用 `xym`（业务层命令）。只有一个账号时不用指定，多个账号加 `--account <unb>`：

```bash
uv run xym items sync --stats      # 把主页上的商品同步到 data/xianyu.db，--stats 顺带拉浏览、收藏数
uv run xym items list              # 看本地库里的商品，--status online/sold 过滤，--all 含已下架
uv run xym items show <商品ID>      # 实时详情
uv run xym items price <商品ID> 299 # 改价，会先确认；降价超过一半或涨价超过一倍要加 --force
uv run xym items desc <商品ID> 描述.txt
uv run xym items down <商品ID>      # 下架
uv run xym items delete <商品ID>    # 删除，平台上不可恢复
uv run xym items log               # 通过本系统做过的改动，成功失败都有记录
```

### 管理后台

后台接口没有登录鉴权，只监听本机（127.0.0.1）。

前端用 pnpm，版本固定在 `web/package.json` 的 `packageManager` 里。没装过的话先运行一次 `corepack enable pnpm`（Node 自带的 corepack 会按这个版本下载 pnpm）。

平时用：先打包一次前端，之后只要起后台，浏览器打开 http://127.0.0.1:8000 。

```bash
pnpm -C web install && pnpm -C web build   # 第一次，或前端代码更新后
uv run xym serve                            # 接口和打包好的页面都在 8000 端口
```

改前端时：开两个终端，一个跑接口，一个跑 Vite 开发服务器（改代码自动刷新，`/api` 会转给 8000 端口），打开 http://localhost:5173 。

```bash
uv run xym serve
```

```bash
pnpm -C web dev
```

凭证存在 `data/accounts/<unb>.json`，文件权限是 0600，`data/` 不会进 git。里面是账号 cookie，不要外传。

在代码里用：

```python
from xianyu_core import Credential, MtopClient, QRLogin

async with QRLogin() as login:
    qr = await login.start()  # qr.to_ascii() / qr.to_svg() 拿二维码
    credential = await login.wait()  # 扫码确认后返回凭证

async with MtopClient(credential) as mtop:
    user = await mtop.call("mtop.taobao.idlemessage.pc.loginuser.get")
    credential = mtop.credential  # 响应里续期过的 cookie，记得存回去
```

## 目录结构

```
xianyu_core/     底座：只负责和闲鱼通信，不放业务逻辑
  sign.py          mtop 签名
  mtop.py          mtop 请求客户端，按错误码抛 AuthRequired / RiskControl 等异常
  credential.py    账号凭证（cookie）的读写
  auth/qr_login.py 扫码登录
  item.py          商品接口：列表、详情、编辑（editDetail → edit）、下架、删除
  cli.py           调试命令 xianyu login / whoami
app/             业务层：只通过 xianyu_core 和闲鱼通信
  accounts.py      账号（data/accounts/<unb>.json），用完把续期的 cookie 写回
  db.py            本地 SQLite（data/xianyu.db）：items、item_ops 两张表
  items.py         商品管理：同步、查询、改价（带保护线）、改描述、下架、删除、改动记录
  api.py           管理后台的 HTTP 接口（FastAPI），xym serve 启动，顺带托管 web/dist
  cli.py           业务命令 xym
  （以后）订单、消息、自动化……
tests/           单元测试（用 httpx.MockTransport 模拟平台，不连网）
web/             管理后台前端：Vite + React + TypeScript + Tailwind + shadcn/ui
  src/lib/api.ts       后端接口和类型
  src/components/ui/   shadcn/ui 组件（在 web/ 里 pnpm dlx shadcn@latest add 生成，可以直接改）
  src/features/items/  商品管理页：列表、详情、改价、改描述、下架、删除、改动记录
third_party/     拷进来的开源代码（规矩见 third_party/README.md）
docs/            调研和设计文档
```

## 几条原则

- **许可证干净**：只复用 MIT 或 Apache-2.0 的代码，GPL / AGPL 只参考思路。
- **底座和业务分开**：平台协议一变，只需要改 `xianyu_core`。
- **账号安全优先**：内置限流和熔断；碰到滑块或身份验证时通知人工处理，不做自动破解；cookie 加密保存。

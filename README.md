# xianyu-manager · 闲鱼管理系统

自用的闲鱼店铺管理系统。底层协议（登录、扫码、签名、即时消息）参考开源实现，上层业务功能自己定制。

## 现在的进度

- [x] 开源项目调研 → [docs/open-source-research.md](docs/open-source-research.md)
- [x] 协议笔记 → [docs/protocol-notes.md](docs/protocol-notes.md)
- [x] 底座方案：Python 自建 `xianyu_core`，结构参考 goofish-cli
- [x] mtop 签名和请求客户端（[xianyu_core/mtop.py](xianyu_core/mtop.py)）
- [x] 纯 HTTP 扫码登录（[xianyu_core/auth/qr_login.py](xianyu_core/auth/qr_login.py)）
- [x] 登录态续期：扫码后开启"保持登录"，会话过期时自动免密续期（[xianyu_core/renew.py](xianyu_core/renew.py)），`xym accounts check` 和后台账号菜单看登录态。线上还没走通一次真正的续期，见[协议笔记](docs/protocol-notes.md#登录态续期)
- [ ] 定时续期：网页端每打开一个页面就续一次，我们现在只在会话过期时续
- [ ] 即时消息（WebSocket + MessagePack）
- [x] 限流和风控熔断：每个账号改动 3 次/分钟、读 30 次/分钟，碰到风控停 30 分钟（[docs/guard.md](docs/guard.md)）
- [x] 商品接口：列表、详情、编辑、下架、删除（[xianyu_core/item.py](xianyu_core/item.py)）
- [x] 业务层第一块：商品管理，同步到本地 SQLite、改价、改描述、下架、删除、改动记录（[app/items.py](app/items.py)）
- [x] 管理后台第一页：商品管理（FastAPI 接口 [app/api.py](app/api.py) + Vite / React / shadcn/ui 前端 [web/](web/)）
- [x] 订单接口：订单详情（已实测）、待付款订单改价、专业卖家的卖出订单列表（[xianyu_core/order.py](xianyu_core/order.py)）
- [x] 订单管理：按订单号添加、刷新进行中的订单、按状态查看、实时详情、改价、改动记录（[app/orders.py](app/orders.py)，后台"订单"页）
- [ ] 订单自动入库：个人卖家在网页端拿不到卖出订单列表（见[协议笔记](docs/protocol-notes.md#订单)），等即时消息做好后从交易消息里记
- [ ] 业务功能：发货、消息、自动回复、自动发货……

## 快速上手

需要 [uv](https://docs.astral.sh/uv/) 和 Python 3.12 以上；管理后台的前端还要 Node.js 20 以上。

```bash
uv sync                                      # 安装依赖
uv run xianyu login                          # 终端里显示二维码，用闲鱼 App 扫
uv run xianyu whoami data/accounts/<unb>.json  # 检查登录态是否还有效，会话过期时先试免密续期
uv run pytest                                # 跑测试
```

商品管理用 `xym`（业务层命令）。只有一个账号时不用指定，多个账号加 `--account <unb>`：

```bash
uv run xym accounts check          # 各账号登录态；会话过期时先试免密续期，不行就提示重新扫码
uv run xym items sync --stats      # 把主页上的商品同步到 data/xianyu.db，--stats 顺带拉浏览、收藏数
uv run xym items list              # 看本地库里的商品，--status online/sold 过滤，--all 含已下架
uv run xym items show <商品ID>      # 实时详情
uv run xym items price <商品ID> 299 # 改价，会先确认；降价超过一半或涨价超过一倍要加 --force
uv run xym items desc <商品ID> 描述.txt
uv run xym items down <商品ID>      # 下架
uv run xym items delete <商品ID>    # 删除，平台上不可恢复
uv run xym items log               # 通过本系统做过的改动，成功失败都有记录
```

订单管理（卖出的订单）。个人卖家在网页端拿不到卖出订单列表，要在闲鱼 App「我卖出的」里点进订单、复制订单编号，一笔笔添加：

```bash
uv run xym orders add <订单号或订单链接>       # 添加，会拉一次详情确认是你卖出的订单
uv run xym orders sync                       # 刷新本地还没结束的订单；专业卖家会从卖家工作台整页同步
uv run xym orders list --status to_ship      # 看本地库里的订单；状态有 unpaid / to_ship / shipped / done / closed / refund / other
uv run xym orders show <订单ID>               # 实时详情：进度、金额明细（含软件服务费、预估到手）、订单信息
uv run xym orders price <订单ID>              # 待付款订单：先列出能改的价格项（商品价格、运费）
uv run xym orders price <订单ID> modifyFee=99 # 改价，没写的项保持原值；总价降一半以上或涨一倍以上要加 --force
uv run xym orders log                        # 本系统对订单做过的改动
uv run xym orders remove <订单ID>             # 从本地列表移除，不动平台上的订单
```

发货还没做：网页版闲鱼也不支持发货，要在闲鱼 App 里操作。

所有请求都有限流（每个账号改动 3 次/分钟、读 30 次/分钟）。碰到风控（滑块、"被挤爆啦"）时账号自动暂停 30 分钟，
期间不发任何请求。先到闲鱼 App 或网页完成验证，再提前解除：

```bash
uv run xym accounts unblock        # 管理后台页面顶部也有"已完成验证，解除"
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
    credential = mtop.credential  # 响应里续期过的 cookie（含免密续期），记得存回去
```

## 目录结构

```
xianyu_core/     底座：只负责和闲鱼通信，不放业务逻辑
  sign.py          mtop 签名
  mtop.py          mtop 请求客户端，按错误码抛 AuthRequired / RiskControl 等异常，会话过期时先试免密续期
  guard.py         按账号的限流和风控熔断，熔断状态存在 data/guard.json
  credential.py    账号凭证（cookie）的读写
  renew.py         登录态续期：保持登录开关、免密续期（silentHasLogin）
  auth/qr_login.py 扫码登录，登录后开启保持登录
  item.py          商品接口：列表、详情、编辑（editDetail → edit）、下架、删除
  order.py         订单接口：卖出的订单列表（卖家工作台）、订单详情、改价（price render → adjust）
  cli.py           调试命令 xianyu login / whoami
app/             业务层：只通过 xianyu_core 和闲鱼通信
  accounts.py      账号（data/accounts/<unb>.json），用完把续期的 cookie 写回，顺手记下登录态
  db.py            本地 SQLite（data/xianyu.db）：items、item_ops、orders、order_ops 四张表
  items.py         商品管理：同步、查询、改价（带保护线）、改描述、下架、删除、改动记录
  orders.py        订单管理：按订单号添加、刷新、查询、实时详情、待付款订单改价（同一条保护线）、改动记录
  api.py           管理后台的 HTTP 接口（FastAPI），xym serve 启动，顺带托管 web/dist
  cli.py           业务命令 xym
  （以后）消息、自动化……
tests/           单元测试（用 httpx.MockTransport 模拟平台，不连网）
web/             管理后台前端：Vite + React + TypeScript + Tailwind + shadcn/ui
  src/lib/api.ts       后端接口和类型
  src/components/ui/   shadcn/ui 组件（在 web/ 里 pnpm dlx shadcn@latest add 生成，可以直接改）
  src/features/items/  商品管理页：列表、详情、改价、改描述、下架、删除、改动记录
  src/features/orders/ 订单管理页：添加订单、按状态分组、实时详情、待付款订单改价、移除、改动记录
third_party/     拷进来的开源代码（规矩见 third_party/README.md）
docs/            调研和设计文档
```

## 几条原则

- **许可证干净**：只复用 MIT 或 Apache-2.0 的代码，GPL / AGPL 只参考思路。
- **底座和业务分开**：平台协议一变，只需要改 `xianyu_core`。
- **账号安全优先**：内置限流和熔断；碰到滑块或身份验证时通知人工处理，不做自动破解；cookie 加密保存。

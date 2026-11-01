# xianyu-manager · 闲鱼管理系统

自用的闲鱼店铺管理系统。底层协议（登录、扫码、签名、即时消息）参考开源实现，上层业务功能自己定制。

## 现在的进度

- [x] 开源项目调研 → [docs/open-source-research.md](docs/open-source-research.md)
- [x] 协议笔记 → [docs/protocol-notes.md](docs/protocol-notes.md)
- [x] 底座方案：Python 自建 `xianyu_core`，结构参考 goofish-cli
- [x] mtop 签名和请求客户端（[xianyu_core/mtop.py](xianyu_core/mtop.py)）
- [x] 纯 HTTP 扫码登录（[xianyu_core/auth/qr_login.py](xianyu_core/auth/qr_login.py)），终端和管理后台都能扫（[app/login.py](app/login.py)）
- [x] 登录态续期：会话过期时自动免密续期（[xianyu_core/renew.py](xianyu_core/renew.py)），`xym accounts check` 和后台账号菜单看登录态。
  纯 HTTP 扫码登录平台不给开"保持登录"（同一个账号在浏览器里能开），所以用浏览器插件（[extension/](extension/)）把浏览器里的登录同步过来，见[协议笔记](docs/protocol-notes.md#登录态续期)。线上还没走通一次真正的续期
- [x] 定时确认登录态：`xym serve` 每 30 分钟确认一次，会话一过期就免密续期；保持登录 3 天内到期时页面顶部提醒（[app/keepalive.py](app/keepalive.py)）
- [x] 即时消息底座：WebSocket 长连接，新消息由服务端实时推送，不用轮询；断线自动重连，会话列表和历史消息也能拉（[xianyu_core/im.py](xianyu_core/im.py)，协议见[协议笔记](docs/protocol-notes.md#即时消息)）。能发文本消息；标已读还没做
- [x] 消息管理：`xym serve` 里每个账号挂一条即时消息连接，新消息实时存进本地库、推到后台"消息"页；连上先补拉最近的会话，
  别的会话点开时再拉，往前翻历史也走同一条连接（[app/messages.py](app/messages.py)）。登录失效、风控时停下，换了登录、解除熔断后自动再连。
  在页面上能直接回复商品会话（文本消息，每分钟最多 10 条）；格式照网页版，实测发过一条。
  只存和人的聊天：平台的活动推广（通知消息）、客服机器人（闲小蜜）、各种通知号的会话不拉进来，以前存的启动时删掉。
  对方昵称从会话列表的最后一条消息拿，拿不到的向平台查（连上时查最近 20 个，别的点开会话时查）。
  图片消息直接显示图片；在 App 里删掉的会话和网页版一样不显示、不算未读。
  点开有未读的会话会清掉平台上的红点（和网页版一样）；不给对方发已读回执。
  同一个账号同一时间只让一个 `xym serve` 连即时消息（两个进程会互相挤掉线）：后开的那个不连，页面上提示，
  先开的退出以后自动接上
- [x] 限流和风控熔断：每个账号改动 3 次/分钟、发消息 10 次/分钟、读 30 次/分钟，碰到风控停 30 分钟（[docs/guard.md](docs/guard.md)）
- [x] 商品接口：列表、详情、编辑、下架、删除（[xianyu_core/item.py](xianyu_core/item.py)）
- [x] 业务层第一块：商品管理，同步到本地库、改价、改描述、下架、删除、改动记录（[app/items.py](app/items.py)）
- [x] 管理后台第一页：商品管理（FastAPI 接口 [app/api.py](app/api.py) + Vite / React / shadcn/ui 前端 [web/](web/)）
- [x] 订单接口：订单详情（已实测）、待付款订单改价、专业卖家的卖出订单列表（[xianyu_core/order.py](xianyu_core/order.py)）
- [x] 订单管理：按订单号添加、刷新进行中的订单、按状态查看、实时详情、改价、改动记录（[app/orders.py](app/orders.py)，后台"订单"页）
- [x] 本地库换成 PostgreSQL（本机用 docker 跑），表结构用 Alembic 迁移管理；以前 SQLite 里的数据用 `xym import-sqlite` 一次导过来（见下面"数据库"）
- [x] 管理后台的用户登录：用户名密码登录，访问令牌（JWT）加刷新令牌；一个用户可以有多个闲鱼账号，每个用户只看得到、
  只能操作自己名下的账号。用户由命令行建，没有注册入口（[app/auth.py](app/auth.py)、[app/users.py](app/users.py)，见下面"用户和登录"）
- [ ] 订单自动入库：个人卖家在网页端拿不到卖出订单列表（见[协议笔记](docs/protocol-notes.md#订单)），等即时消息做好后从交易消息里记
- [ ] 业务功能：发货、消息、自动回复、自动发货……

以后要做、但还没定下来做的事记在 [docs/todo.md](docs/todo.md)：设备指纹、登录同步插件推到远程服务器。

## 快速上手

需要 [uv](https://docs.astral.sh/uv/) 和 Python 3.12 以上、Docker（跑本机的 PostgreSQL，Mac 上用 OrbStack 或 Docker Desktop）；管理后台的前端还要 Node.js 20 以上。

```bash
uv sync                                      # 安装依赖
docker compose up -d                         # 启动本机的 PostgreSQL
uv run alembic upgrade head                  # 建表；以后拉了新代码也跑一次，把表结构升到最新
uv run xym users add <用户名>                  # 建管理后台的用户，提示输入密码（第一个用户接手已有的闲鱼账号）
uv run xianyu login                          # 终端里显示二维码，用闲鱼 App 扫（也可以在管理后台里扫）
uv run xianyu whoami data/accounts/<unb>.json  # 检查登录态是否还有效，会话过期时先试免密续期
uv run pytest                                # 跑测试（用到数据库的连 docker 里的 xianyu_test 库，没有会自动建）
```

每个 PR 和推到 main 的提交，GitHub Actions 会跑一遍同样的检查（[.github/workflows/ci.yml](.github/workflows/ci.yml)）：
后端 `ruff check`、`ruff format --check`、`pytest`（连一个 PostgreSQL 16 服务容器），前端 `pnpm lint`、`pnpm build`。

商品管理用 `xym`（业务层命令）。只有一个账号时不用指定，多个账号加 `--account <unb>`：

```bash
uv run xym accounts check          # 各账号登录态；会话过期时先试免密续期，不行就提示重新扫码
uv run xym items sync --stats      # 把主页上的商品同步到本地库，--stats 顺带拉浏览、收藏数
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

消息由 `xym serve` 在后台实时收（见下面"管理后台"），命令行只看本地库里已经收到的：

```bash
uv run xym messages list              # 商品会话，最近有动静的在前；--all 含系统通知
uv run xym messages show <会话ID> -n 50 # 一个会话最近的消息
```

所有请求都有限流（每个账号改动 3 次/分钟、发消息 10 次/分钟、读 30 次/分钟）。碰到风控（滑块、"被挤爆啦"）时账号自动暂停 30 分钟，
期间不发任何请求。先到闲鱼 App 或网页完成验证，再提前解除：

```bash
uv run xym accounts unblock        # 管理后台页面顶部也有"已完成验证，解除"
```

### 管理后台

打开页面先用管理后台的用户名密码登录（见下面"用户和登录"）。后台只监听本机（127.0.0.1），是明文 http，不直接对公网开。
启动前数据库要先起来（`docker compose up -d`），表结构不是最新时 `xym serve` 会直接报错退出。

前端用 pnpm，版本固定在 `web/package.json` 的 `packageManager` 里。没装过的话先运行一次 `corepack enable pnpm`（Node 自带的 corepack 会按这个版本下载 pnpm）。

平时用：先打包一次前端，之后只要起后台，浏览器打开 http://127.0.0.1:8000 。
还没有账号时页面上直接点「登录闲鱼」；登录失效时页面顶部的提示和右上角账号菜单里都能重新登录。
扫码登录的会话平台不给开保持登录，过期后只能重新扫。想要自动续期，用浏览器插件把 Chrome 里的登录同步过来：

1. Chrome 打开 `chrome://extensions`，右上角打开"开发者模式"，点"加载已解压的扩展程序"，选这个项目的 `extension` 目录；
2. 在管理后台左下角的用户菜单里点「浏览器插件令牌」生成一个，复制到插件弹窗的「插件令牌」里。
   推过来的账号归到这个令牌的主人名下；令牌只能用来推登录，别的接口都不认；
3. 在 Chrome 里登录 goofish.com，头像菜单里打开"保存登录信息"；
4. 插件会把登录推给本机的管理后台（默认 http://127.0.0.1:8000，插件弹窗里能改，只能填本机地址）。
   之后浏览器里登录变了（重新登录、网页端自动续期）都会自动同步，另外每 10 分钟对一次。点插件图标能看同步结果。

插件只读 goofish.com 的 cookie，只发给本机的后台。后台和浏览器共用这次登录，浏览器里退出登录这里也会失效。
插件令牌忘了或者泄露了，在用户菜单里重新生成，旧的马上作废。
后台收到登录先向平台确认能用再保存；和已保存的一样就不请求平台；浏览器那份已经过期就拒绝，不替它续期，留着后台自己那份。
没装插件时，登录弹窗的「导入浏览器登录」可以手动粘贴开发者工具里 www.goofish.com 那条请求的 Cookie 请求头，效果一样。

```bash
pnpm -C web install && pnpm -C web build   # 第一次，或前端代码更新后
uv run xym serve                            # 接口和打包好的页面都在 8000 端口
```

改前端时：开两个终端，一个跑接口，一个跑 Vite 开发服务器（改代码自动刷新，`/api` 会转给 8000 端口），打开 http://localhost:5173 。
页面的样式和结构按 [docs/design.md](docs/design.md)（颜色、字号、列表页结构、状态标签、空和出错状态）。

```bash
uv run xym serve
```

```bash
pnpm -C web dev
```

凭证存在 `data/accounts/<unb>.json`，文件权限是 0600，`data/` 不会进 git。里面是账号 cookie，不要外传。
旁边的 `<unb>.im.json` 是即时消息用的设备 ID，第一次连的时候生成，之后一直用同一个，不要删；
`<unb>.im.lock` 是文件锁，保证一个账号只有一个 `xym serve` 在连，进程退出时系统自动放开。

### 用户和登录

一个用户可以有多个闲鱼账号，一个闲鱼账号只归一个用户。每个用户在后台里只看得到、只能操作自己名下的账号：
商品、订单、消息、改动记录都跟着账号走；别人的账号在接口上和不存在一样（404）。
扫码登录、导入 Cookie、插件推过来的账号归到当前用户；已经归别人的拒绝，凭证也不保存。

用户由命令行建，没有注册入口。命令行是管理员工具，不分用户：

```bash
uv run xym users add alice          # 建用户，提示输两遍密码（至少 8 位）
uv run xym users list               # 每个用户名下的闲鱼账号
uv run xym users passwd alice       # 重设密码，这个用户已经登录的地方都要重新登录
uv run xym users disable alice      # 停用：登录不了，已经登录的地方马上失效，插件令牌也不认；enable 恢复
uv run xym accounts assign <unb> bob  # 把闲鱼账号分给用户（从别人名下转过来时先确认）
```

有用户以前就登录过的闲鱼账号没有归属，谁都看不到；**建第一个用户时自动归给这个用户**，之后的用 `xym accounts assign` 分。

登录鉴权（[app/auth.py](app/auth.py)）：

- 登录后拿到**访问令牌**（JWT，HS256，15 分钟），前端只放在内存里，每个请求带在 `Authorization: Bearer` 头上；
- **刷新令牌**放在 HttpOnly cookie 里（只发给 `/api/auth`，SameSite=Strict，页面脚本读不到），访问令牌过期了拿它换新的。
  7 天没打开就过期，最长 30 天要重新输密码。退出登录时在服务端删掉；
- 改密码、停用时，这个用户之前发的访问令牌立即作废（每个请求查一次用户）；
- 同一个用户名 15 分钟里输错 5 次密码，要等这 15 分钟过去才能再试；
- 签名密钥在 `data/auth.key`，第一次启动时生成。删掉它再重启，发出去的访问令牌全部作废（刷新令牌还在，页面会自动换新的）；
- 消息推送（SSE）用 fetch 读，访问令牌放在请求头里，不放进网址（网址会被记进访问日志）。

### 数据库

本地库是 PostgreSQL 16。连接串取环境变量 `DATABASE_URL`（命令行也可以 `--db` 指定），没设时连本机 docker 里的
`postgresql://xianyu:xianyu@localhost:5432/xianyu`（[docker-compose.yml](docker-compose.yml)，端口只绑 127.0.0.1）。
数据在 docker 卷里，`docker compose down` 不会丢，`docker compose down -v` 才会删。

表结构写在 [app/schema.py](app/schema.py)，迁移在 [migrations/versions/](migrations/versions/)。改表的步骤：

```bash
# 1. 改 app/schema.py
uv run alembic revision --autogenerate -m "说明"   # 2. 生成迁移，打开生成的文件检查一遍（改列名、数据要搬的得手写）
uv run alembic upgrade head                        # 3. 升级本机的库
uv run pytest tests/test_db.py                      # 4. 确认迁移和 app/schema.py 对得上、能降回去
```

以前的 SQLite 库（`data/xianyu.db`）里的数据，建好表以后导一次就行，只能导进空库，出错整个回滚：

```bash
uv run xym import-sqlite data/xianyu.db
```

### 部署

线上分两台华为云服务器（`~/.ssh/config` 里叫 `huawei-pg` 和 `huawei`）：

- **数据库服务器 huawei-pg**：PostgreSQL 16 跑在 docker 里，端口只绑 127.0.0.1，不对外开；
- **应用服务器 huawei**：只放前后端。用一条 SSH 隧道（[deploy/pg-tunnel.service](deploy/pg-tunnel.service)）
  把本机的 127.0.0.1:5432 转到数据库服务器本机的 5432，后端照常连本机：
  `DATABASE_URL=postgresql://xianyu:密码@127.0.0.1:5432/xianyu`（密码里有 `@`、`:` 之类的字符要 URL 编码）。

两台机器内网不通，隧道走数据库服务器的公网 SSH。两边各有一个不能登录的 `pgtunnel` 用户，数据库服务器上它的公钥
限定成只能转发到 127.0.0.1:5432（`restrict,port-forwarding,permitopen="127.0.0.1:5432"`），拿到这把钥匙也开不了 shell、
连不了别的端口。隧道断了 5 秒后自动重连；后端那边断的那一下查询会报错，下一次自动重连。

重新装一遍隧道（应用服务器上，root）：

```bash
useradd --system --create-home --home-dir /var/lib/pgtunnel --shell /usr/sbin/nologin pgtunnel
install -d -m 700 -o pgtunnel -g pgtunnel /var/lib/pgtunnel/.ssh
runuser -u pgtunnel -- ssh-keygen -t ed25519 -N "" -f /var/lib/pgtunnel/.ssh/id_ed25519
# 数据库服务器的主机公钥写进 /var/lib/pgtunnel/.ssh/known_hosts（先核对指纹）
echo "PG_SSH_HOST=<数据库服务器的 IP>" > /etc/pg-tunnel.env
# 数据库服务器上同样建 pgtunnel 用户，authorized_keys 写：
#   restrict,port-forwarding,permitopen="127.0.0.1:5432" <上面生成的 id_ed25519.pub>
cp deploy/pg-tunnel.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now pg-tunnel
systemctl status pg-tunnel                   # 看隧道状态；journalctl -u pg-tunnel 看日志
```

从自己电脑上临时连线上库（跑迁移、导数据、查数据），另开一条隧道，用 15432 免得和本机 docker 的 5432 撞上：

```bash
ssh -N -L 15432:127.0.0.1:5432 huawei-pg
DATABASE_URL=postgresql://xianyu:密码@127.0.0.1:15432/xianyu uv run alembic current
```

#### 发版

在自己电脑上、项目根目录运行，把已提交的代码（HEAD）部署到应用服务器：

```bash
deploy/deploy.sh            # 默认主机 huawei
```

它会：用 `git archive` 导出已提交的代码（没提交的改动不会上去），在本机打包前端，rsync 到服务器的
`/opt/xianyu-manager`（服务器上的 `.venv` 和 `data/` 不动），然后在服务器上 `uv sync --locked --no-dev`、
`alembic upgrade head`、重启 `xym`，等接口能访问了才算完。服务器上不用装 Node。
第一次装依赖从 PyPI 下载比较慢（十几分钟），之后 uv 有缓存，只下变了的包。

后台由 systemd 的 `xym` 服务跑（[deploy/xym.service](deploy/xym.service)）：以不能登录的 `xym` 用户运行，只监听服务器本机 8000，
排在 `pg-tunnel` 后面启动，连不上数据库就 5 秒后重试；除了 `data/` 别的目录都只读。连接串在 `/etc/xym.env`（root 才能读）。
看日志：`journalctl -u xym -f`。

访问后台：接口是明文 http，不对公网开，从自己电脑开一条隧道（用 18000，免得和本机开发用的 8000 撞上）：

```bash
ssh -N -L 18000:127.0.0.1:8000 huawei
```

然后打开 http://127.0.0.1:18000 。服务器上的用户和闲鱼账号都和本机分开：

- 用户在服务器上建。在自己电脑上用 `deploy/xym.sh` 跑服务器上的 `xym` 命令（SSH 过去、带上 `/etc/xym.env`、
  以 `xym` 用户运行，密码在本机终端里输）：

  ```bash
  deploy/xym.sh users add alice
  deploy/xym.sh users list
  ```

  第一个用户会接手服务器上以前扫码登录的闲鱼账号。
- 闲鱼账号在页面上点「登录闲鱼」用 App 扫码，凭证存在服务器的 `data/accounts/`，不从本机拷。
  登录同步插件要推给服务器的话，把插件里的地址改成 http://127.0.0.1:18000 ，令牌在服务器的页面上生成。

应用服务器第一次装（root）：

```bash
# uv：装和本机一样的版本（服务器从 GitHub 下载很慢的话，在本机下载好 scp 上去，核对 sha256）
install -m 755 uv uvx /usr/local/bin/
useradd --system --no-create-home --home-dir /opt/xianyu-manager/data --shell /usr/sbin/nologin xym
install -d -m 755 /opt/xianyu-manager
install -d -m 700 -o xym -g xym /opt/xianyu-manager/data /opt/xianyu-manager/data/accounts
# 连接串：DATABASE_URL=postgresql://xianyu:密码@127.0.0.1:5432/xianyu
install -m 600 /dev/null /etc/xym.env && vi /etc/xym.env
cp deploy/xym.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable xym
# 然后在自己电脑上跑 deploy/deploy.sh
```

想先看迁移要执行的 SQL：服务器上 `cd /opt/xianyu-manager && set -a && . /etc/xym.env && .venv/bin/alembic upgrade head --sql`。

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
  im.py            即时消息：WebSocket 连接（注册、心跳、ACK、对齐同步位点）、推送解码、会话列表、历史消息、断线重连
  cli.py           调试命令 xianyu login / whoami
app/             业务层：只通过 xianyu_core 和闲鱼通信
  accounts.py      账号（data/accounts/<unb>.json），用完把续期的 cookie 写回，顺手记下登录态
  users.py         管理后台的用户：密码（argon2id）、刷新令牌、插件令牌、闲鱼账号归属
  auth.py          登录鉴权：访问令牌（JWT）、签名密钥、输错密码限次
  db.py            本地库（PostgreSQL）：连接、启动时确认表结构是最新的
  schema.py        表结构：用户、刷新令牌、账号归属，商品、订单、它们的改动记录，消息的会话和消息；Alembic 从这里生成迁移
  sqlite_import.py 一次性把以前 SQLite 库里的数据导进 PostgreSQL（xym import-sqlite）
  items.py         商品管理：同步、查询、改价（带保护线）、改描述、下架、删除、改动记录
  orders.py        订单管理：按订单号添加、刷新、查询、实时详情、待付款订单改价（同一条保护线）、改动记录
  login.py         登录闲鱼：扫码（后台任务跑 QRLogin，前端轮询进度，每个用户同一时间只有一张码）和导入浏览器的 Cookie
  keepalive.py     定时确认登录态：每 30 分钟查一次，会话过期就免密续期，失效的只在续期有希望时再试
  messages.py      消息：每个账号一条即时消息连接，新消息存库、通知页面（SSE），连上先补拉，停下了等条件变了再连；回复
  api.py           管理后台的 HTTP 接口（FastAPI），xym serve 启动，顺带托管 web/dist
  cli.py           业务命令 xym
  （以后）自动回复、自动化……
migrations/      Alembic 迁移（alembic.ini 在项目根目录）
deploy/          部署：deploy.sh 发版，xym.sh 在服务器上跑 xym 命令，xym.service 跑后台，pg-tunnel.service 连数据库的 SSH 隧道
.github/workflows/ci.yml  GitHub Actions：后端 ruff + pytest，前端 oxlint + 构建
tests/           单元测试（用 httpx.MockTransport 模拟平台，不连网；用到数据库的连 docker 里的 xianyu_test 库）
web/             管理后台前端：Vite + React + TypeScript + Tailwind + shadcn/ui
  src/lib/api.ts       后端接口和类型；请求都带访问令牌，过期了自动换新的
  src/lib/auth.ts      登录状态：访问令牌放在内存里，打开页面时拿 cookie 里的刷新令牌换
  src/features/auth/   登录页、改密码、浏览器插件令牌
  src/components/ui/   shadcn/ui 组件（在 web/ 里 pnpm dlx shadcn@latest add 生成，可以直接改）
  src/features/items/  商品管理页：列表、详情、改价、改描述、下架、删除、改动记录
  src/features/orders/ 订单管理页：添加订单、按状态分组、实时详情、待付款订单改价、移除、改动记录
  src/features/messages/ 消息页：会话列表、聊天记录、往前翻历史、回复，新消息通过 SSE 实时刷新
extension/       Chrome 插件：把浏览器里闲鱼的登录同步给管理后台（manifest v3，不用打包，直接加载目录）
  background.js    cookie 变化和每 10 分钟时推给 /api/login/import（带插件令牌），登录没变不推
  lib.js           纯函数（拼 cookie、登录指纹、只允许本机地址），可以直接用 node 测
  popup.html/js    弹窗：同步结果、立即同步、管理后台地址、插件令牌
third_party/     拷进来的开源代码（规矩见 third_party/README.md）
docs/            调研和设计文档
```

## 几条原则

- **许可证干净**：只复用 MIT 或 Apache-2.0 的代码，GPL / AGPL 只参考思路。
- **底座和业务分开**：平台协议一变，只需要改 `xianyu_core`。
- **账号安全优先**：内置限流和熔断；碰到滑块或身份验证时通知人工处理，不做自动破解；cookie 加密保存。

# xianyu-manager · 闲鱼管理系统

自用的闲鱼店铺管理系统。底层协议（登录、扫码、签名、即时消息）参考开源实现，上层业务功能自己定制。

## 现在的进度

- [x] 开源项目调研 → [docs/open-source-research.md](docs/open-source-research.md)
- [x] 协议笔记 → [docs/protocol-notes.md](docs/protocol-notes.md)
- [x] 底座方案：Python 自建 `xianyu_core`，结构参考 goofish-cli
- [x] mtop 签名和请求客户端（[xianyu_core/mtop.py](xianyu_core/mtop.py)）
- [x] 纯 HTTP 扫码登录（[xianyu_core/auth/qr_login.py](xianyu_core/auth/qr_login.py)），在管理后台里扫（[app/login.py](app/login.py)）
- [x] 登录态续期：会话过期时自动免密续期（[xianyu_core/renew.py](xianyu_core/renew.py)），后台账号菜单看登录态、重新检查。
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
- [x] 订单管理：卖出的和买到的分开看（后台"订单"页上面切换），按订单号添加、刷新进行中的订单、按状态查看、实时详情、
  卖出订单改价、改动记录（[app/orders.py](app/orders.py)）。买到的从闲鱼「我买到的」整页同步
- [x] 本地库换成 PostgreSQL（本机用 docker 跑），表结构用 Alembic 迁移管理；以前 SQLite 里的数据已经一次导过来了
- [x] 管理后台对公网开放：服务器上用 Caddy 提供 HTTPS（Let's Encrypt 给公网 IP 发的证书），后台只听本机；
  登录同步插件能推给远程的 https 后台（见下面"部署"）
- [x] 管理后台的用户登录：用户名密码登录，访问令牌（JWT）加刷新令牌；一个用户可以有多个闲鱼账号，每个用户只看得到、
  只能操作自己名下的账号。用户由命令行建，没有注册入口（[app/auth.py](app/auth.py)、[app/users.py](app/users.py)，见下面"用户和登录"）
- [x] 订单自动入库：个人卖家在网页端拿不到卖出订单列表（见[协议笔记](docs/protocol-notes.md#订单)），改从交易消息里记。
  `xym serve` 收到交易卡片（"[我已拍下，待付款]"、"[我已付款，等待你发货]"之类）时，从卡片里取订单号，
  在后台拉一次详情记进本地库，之后每来一张卡片（发货、收货、评价）刷新一次状态，后台"订单"页跟着刷新。
  卖出的、买到的都记（按订单详情里自己是不是卖家分）。以前存下的消息里的订单在订单页点
  "从消息找订单"补（[app/orders.py](app/orders.py) 的 `OrderFeed`，卡片格式见[协议笔记](docs/protocol-notes.md#交易消息里的订单号)）。
  线上还没走通一次：要等有人下单
- [x] 商品搜索：后台"搜索"页搜闲鱼上所有卖家的在售商品，排序、发布时间、价格区间、个人闲置 / 包邮 / 全新 / 验货宝
  和网页端搜索页一样，一次 30 件往后加载；用当前账号搜，结果不存本地（[xianyu_core/search.py](xianyu_core/search.py)，
  接口见[协议笔记](docs/protocol-notes.md#搜索)）。以后的捡漏监控、同行价都从这里搜
- [x] 发布商品：后台"发布"页先存草稿（图片、描述、价格、邮费、发货地），检查好了再发到闲鱼。
  图片存在服务器的 `data/images` 里，点发布时才传到闲鱼；分类和品牌、成色这些属性用闲鱼按描述推荐的，可以改；
  发货地和网页端一样能按名字搜（用自己的高德 key，见下面"高德 key"），也能从闲鱼账号的常用地址里选。
  发布前和网页端一样查发布限制和违禁词，网页端不支持的分类（要填 3C 证书编号之类）不让发
  （[xianyu_core/publish.py](xianyu_core/publish.py)、[app/drafts.py](app/drafts.py)、[app/places.py](app/places.py)，
  接口见[协议笔记](docs/protocol-notes.md#发布商品)）。线上还没真正发过一件
- [ ] 业务功能：发货、消息、自动回复、自动发货……

以后要做、但还没定下来做的事记在 [docs/todo.md](docs/todo.md)：设备指纹。

## 快速上手

需要 [uv](https://docs.astral.sh/uv/) 和 Python 3.12 以上、Docker（跑本机的 PostgreSQL，Mac 上用 OrbStack 或 Docker Desktop）；管理后台的前端还要 Node.js 20 以上。

```bash
uv sync                                      # 安装依赖
docker compose up -d                         # 启动本机的 PostgreSQL
uv run alembic upgrade head                  # 建表；以后拉了新代码也跑一次，把表结构升到最新
uv run xym users add <用户名>                  # 建管理后台的用户，提示输入密码（第一个用户接手已有的闲鱼账号）
uv run pytest                                # 跑测试（用到数据库的连 docker 里的 xianyu_test 库，没有会自动建）
```

每个 PR 和推到 main 的提交，GitHub Actions 会跑一遍同样的检查（[.github/workflows/ci.yml](.github/workflows/ci.yml)）：
后端 `ruff check`、`ruff format --check`、`pytest`（连一个 PostgreSQL 16 服务容器），前端 `pnpm lint`、`pnpm build`。

商品、发布、订单、消息、搜索都在管理后台的页面上操作（见下面"管理后台"），命令行只剩 `xym serve` 和 `xym users`。

- 订单：分卖出的和买到的。个人卖家在网页端拿不到卖出订单列表，订单从交易消息里记：`xym serve` 连着即时消息时，
  拍下、付款的卡片一到就自动记下来，状态也跟着后面的卡片刷新。以前的订单在"订单"页点"从消息找订单"从存下的消息里补；
  消息里也没有的，在闲鱼 App「我卖出的」「我买到的」里点进订单、复制订单编号，到"订单"页添加。
  买到的还可以在"买到的"那边点"从闲鱼同步"，把「我买到的」整页拉下来；
- 发货还没做：网页版闲鱼也不支持发货，要在闲鱼 App 里操作；
- 所有请求都有限流（每个账号改动 3 次/分钟、发消息 10 次/分钟、读 30 次/分钟）。碰到风控（滑块、"被挤爆啦"）时
  账号自动暂停 30 分钟，期间不发任何请求。先到闲鱼 App 或网页完成验证，再在页面顶部点"已完成验证，解除"提前解除。

### 管理后台

打开页面先用管理后台的用户名密码登录（见下面"用户和登录"）。后台只监听本机（127.0.0.1），本身是明文 http；
服务器上对公网由 Caddy 提供 HTTPS（见下面"部署"）。
启动前数据库要先起来（`docker compose up -d`），表结构不是最新时 `xym serve` 会直接报错退出。

前端用 pnpm，版本固定在 `web/package.json` 的 `packageManager` 里。没装过的话先运行一次 `corepack enable pnpm`（Node 自带的 corepack 会按这个版本下载 pnpm）。

平时用：先打包一次前端，之后只要起后台，浏览器打开 http://127.0.0.1:8000 。
还没有账号时页面上直接点「登录闲鱼」；登录失效时页面顶部的提示和右上角账号菜单里都能重新登录。
扫码登录的会话平台不给开保持登录，过期后只能重新扫。想要自动续期，用浏览器插件把 Chrome 里的登录同步过来：

1. Chrome 打开 `chrome://extensions`，右上角打开"开发者模式"，点"加载已解压的扩展程序"，选这个项目的 `extension` 目录；
2. 在管理后台左下角的用户菜单里点「浏览器插件令牌」生成一个，复制到插件弹窗的「插件令牌」里。
   推过来的账号归到这个令牌的主人名下；令牌只能用来推登录，别的接口都不认；
3. 在 Chrome 里登录 goofish.com，头像菜单里打开"保存登录信息"；
4. 插件会把登录推给管理后台（默认 http://127.0.0.1:8000，插件弹窗里能改）。本机的地址可以是 http，
   别的只能是 https；填了远程地址时 Chrome 会问一次要不要允许插件访问这个地址，点允许（只给这一个地址的权限）。
   之后浏览器里登录变了（重新登录、网页端自动续期）都会自动同步，另外每 10 分钟对一次。点插件图标能看同步结果。

插件只读 goofish.com 的 cookie，只发给填的那个后台。后台和浏览器共用这次登录，浏览器里退出登录这里也会失效。
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
发布草稿的图片存在 `data/images/<sha256>.jpg`（统一转成 JPEG，同一张图只存一份），删草稿时只有它用的图跟着删。

### 高德 key

发布页按名字搜发货地要一个高德开放平台的 key（[console.amap.com](https://console.amap.com/dev/key/app)，
个人认证开发者，建应用、添加 Key，**服务平台选"Web 服务"**）。key 只在后台用，不发给浏览器。没配的时候只能从常用地址里选。

- 本机：存进 `data/amap.key`（一行，`data/` 不进 git），重启 `xym serve`：

  ```bash
  read -rs "k?高德 key: " && printf '%s\n' "$k" > data/amap.key && chmod 600 data/amap.key && unset k
  ```

- 服务器：在 `/etc/xym.env` 里加一行 `AMAP_KEY=<key>`，`systemctl restart xym`。环境变量比文件优先。

高德那边设了 IP 白名单的话，要把应用服务器的公网 IP 和本机出口 IP 都加上。

### 用户和登录

一个用户可以有多个闲鱼账号，一个闲鱼账号只归一个用户。每个用户在后台里只看得到、只能操作自己名下的账号：
商品、订单、消息、改动记录都跟着账号走；别人的账号在接口上和不存在一样（404）。
扫码登录、导入 Cookie、插件推过来的账号归到当前用户；已经归别人的拒绝，凭证也不保存。

用户由命令行建，没有注册入口。命令行是管理员工具，不分用户，服务器上用 `deploy/xym.sh users ...` 跑：

```bash
uv run xym users add alice          # 建用户，提示输两遍密码（至少 8 位）
uv run xym users list               # 每个用户名下的闲鱼账号
uv run xym users passwd alice       # 重设密码，这个用户已经登录的地方都要重新登录
uv run xym users disable alice      # 停用：登录不了，已经登录的地方马上失效，插件令牌也不认；enable 恢复
```

有用户以前就登录过的闲鱼账号没有归属，谁都看不到；**建第一个用户时自动归给这个用户**。之后谁在后台登录（扫码、导入浏览器登录、插件同步）就归谁；
已经归了一个用户的账号没有转给别人的入口。

登录鉴权（[app/auth.py](app/auth.py)）：

- 登录后拿到**访问令牌**（JWT，HS256，15 分钟），前端只放在内存里，每个请求带在 `Authorization: Bearer` 头上；
- **刷新令牌**放在 HttpOnly cookie 里（只发给 `/api/auth`，SameSite=Strict，页面脚本读不到），访问令牌过期了拿它换新的。
  7 天没打开就过期，最长 30 天要重新输密码。退出登录时在服务端删掉；
- 改密码、停用时，这个用户之前发的访问令牌立即作废（每个请求查一次用户）；
- 15 分钟里同一个用户名输错 5 次、或者同一个 IP 输错 20 次密码，要等这 15 分钟过去才能再试
  （本机来的不按 IP 算：SSH 隧道、本机开发看起来都是 127.0.0.1）；
- 每个响应都带内容安全策略（CSP，只跑自己站点上的脚本）、禁止被别的网站嵌进 iframe 等响应头；接口文档页（/docs）不开；
- 签名密钥在 `data/auth.key`，第一次启动时生成。删掉它再重启，发出去的访问令牌全部作废（刷新令牌还在，页面会自动换新的）；
- 消息推送（SSE）用 fetch 读，访问令牌放在请求头里，不放进网址（网址会被记进访问日志）。

### 数据库

本地库是 PostgreSQL 16。连接串取环境变量 `DATABASE_URL`（`xym` 也可以 `--db` 指定），没设时连本机 docker 里的
`postgresql://xianyu:xianyu@localhost:5432/xianyu`（[docker-compose.yml](docker-compose.yml)，端口只绑 127.0.0.1）。
数据在 docker 卷里，`docker compose down` 不会丢，`docker compose down -v` 才会删。

表结构写在 [app/schema.py](app/schema.py)，迁移在 [migrations/versions/](migrations/versions/)。改表的步骤：

```bash
# 1. 改 app/schema.py
uv run alembic revision --autogenerate -m "说明"   # 2. 生成迁移，打开生成的文件检查一遍（改列名、数据要搬的得手写）
uv run alembic upgrade head                        # 3. 升级本机的库
uv run pytest tests/test_db.py                      # 4. 确认迁移和 app/schema.py 对得上、能降回去
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

访问后台：浏览器打开 `https://<应用服务器的公网 IP>`。Caddy（[deploy/Caddyfile](deploy/Caddyfile)）在 443 上提供 HTTPS，
转给本机的 8000；80 只用来跳转到 https 和申请证书。证书是 Let's Encrypt 给 IP 地址发的，只有 6 天有效，Caddy 自己续
（`journalctl -u caddy` 看续期日志）。后台本身还是只听本机，不经过 Caddy 进不来。

对公网开放的安全措施：只走 HTTPS（浏览器记住一年，不会退回 http）；登录按用户名和 IP 限次；访问令牌 15 分钟；
CSP 等安全响应头；接口文档页不开；请求体最大 1MB。华为云安全组放行 80、443，除了 SSH 的 22 别的端口不用开。
要注意的是：后台能改价、下架、回消息，**用户密码要够长、别和别处共用**；不用的用户 `xym users disable` 停掉。

不想走公网时也可以照旧从自己电脑开隧道（用 18000，免得和本机开发用的 8000 撞上），打开 http://127.0.0.1:18000 ：

```bash
ssh -N -L 18000:127.0.0.1:8000 huawei
```

服务器上的用户和闲鱼账号都和本机分开：

- 用户在服务器上建。在自己电脑上用 `deploy/xym.sh` 跑服务器上的 `xym` 命令（SSH 过去、带上 `/etc/xym.env`、
  以 `xym` 用户运行，密码在本机终端里输）：

  ```bash
  deploy/xym.sh users add alice
  deploy/xym.sh users list
  ```

  第一个用户会接手服务器上以前扫码登录的闲鱼账号。
- 闲鱼账号在页面上点「登录闲鱼」用 App 扫码，凭证存在服务器的 `data/accounts/`，不从本机拷。
  登录同步插件要推给服务器的话，把插件里的地址改成 `https://<公网 IP>`，令牌在服务器的页面上生成。

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

装 HTTPS 入口 Caddy（root；用 Caddy 官方的 apt 源，以后 apt upgrade 跟着升级。华为云安全组要放行 80、443）：

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
echo "XYM_PUBLIC_IP=<应用服务器的公网 IP>" > /etc/caddy/caddy.env
install -D -m 644 deploy/caddy-xym.conf /etc/systemd/system/caddy.service.d/xym.conf
install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile
systemctl daemon-reload && systemctl restart caddy
journalctl -u caddy -f        # 看到 certificate obtained successfully 就好了
```

改了 Caddyfile 以后：`install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy`。

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
  order.py         订单接口：卖出的订单列表（卖家工作台）、我买到的、订单详情、改价（price render → adjust）
  search.py        搜索接口：网页端搜索页同一个接口，排序和筛选，解析成商品卡片
  im.py            即时消息：WebSocket 连接（注册、心跳、ACK、对齐同步位点）、推送解码、会话列表、历史消息、断线重连
app/             业务层：只通过 xianyu_core 和闲鱼通信
  accounts.py      账号（data/accounts/<unb>.json），用完把续期的 cookie 写回，顺手记下登录态
  users.py         管理后台的用户：密码（argon2id）、刷新令牌、插件令牌、闲鱼账号归属
  auth.py          登录鉴权：访问令牌（JWT）、签名密钥、输错密码限次
  db.py            本地库（PostgreSQL）：连接、启动时确认表结构是最新的
  schema.py        表结构：用户、刷新令牌、账号归属，商品、订单、它们的改动记录，消息的会话和消息；Alembic 从这里生成迁移
  items.py         商品管理：同步、查询、改价（带保护线）、改描述、下架、删除、改动记录
  orders.py        订单管理（卖出的、买到的）：按订单号添加、刷新、查询、实时详情、待付款订单改价（同一条保护线）、改动记录
  search.py        搜索闲鱼上的商品（别人的），一次一页，不存库
  login.py         登录闲鱼：扫码（后台任务跑 QRLogin，前端轮询进度，每个用户同一时间只有一张码）和导入浏览器的 Cookie
  keepalive.py     定时确认登录态：每 30 分钟查一次，会话过期就免密续期，失效的只在续期有希望时再试
  messages.py      消息：每个账号一条即时消息连接，新消息存库、通知页面（SSE），连上先补拉，停下了等条件变了再连；回复
  api.py           管理后台的 HTTP 接口（FastAPI），xym serve 启动，顺带托管 web/dist
  cli.py           命令行 xym：启动管理后台（serve）、管理后台的用户（users）
  （以后）自动回复、自动化……
migrations/      Alembic 迁移（alembic.ini 在项目根目录）
deploy/          部署：deploy.sh 发版，xym.sh 在服务器上跑 xym 命令，xym.service 跑后台，
                 Caddyfile 对公网的 HTTPS 入口，pg-tunnel.service 连数据库的 SSH 隧道
.github/workflows/ci.yml  GitHub Actions：后端 ruff + pytest，前端 oxlint + 构建
tests/           单元测试（用 httpx.MockTransport 模拟平台，不连网；用到数据库的连 docker 里的 xianyu_test 库）
web/             管理后台前端：Vite + React + TypeScript + Tailwind + shadcn/ui
  src/lib/api.ts       后端接口和类型；请求都带访问令牌，过期了自动换新的
  src/lib/auth.ts      登录状态：访问令牌放在内存里，打开页面时拿 cookie 里的刷新令牌换
  src/features/auth/   登录页、改密码、浏览器插件令牌
  src/components/ui/   shadcn/ui 组件（在 web/ 里 pnpm dlx shadcn@latest add 生成，可以直接改）
  src/features/items/  商品管理页：列表、详情、改价、改描述、下架、删除、改动记录
  src/features/orders/ 订单管理页：卖出的 / 买到的切换、添加订单、按状态分组、实时详情、待付款订单改价、移除、改动记录
  src/features/messages/ 消息页：会话列表、聊天记录、往前翻历史、回复，新消息通过 SSE 实时刷新
  src/features/search/ 搜索页：搜闲鱼上的商品，排序、筛选，往后加载
extension/       Chrome 插件：把浏览器里闲鱼的登录同步给管理后台（manifest v3，不用打包，直接加载目录）
  background.js    cookie 变化和每 10 分钟时推给 /api/login/import（带插件令牌），登录没变不推
  lib.js           纯函数（拼 cookie、登录指纹、只允许本机地址），可以直接用 node 测
  popup.html/js    弹窗：同步结果、立即同步、管理后台地址、插件令牌
  icons/           插件图标（奶蛙回眸）；16px 的只取头部，小了也看得出眼睛
third_party/     拷进来的开源代码（规矩见 third_party/README.md）
docs/            调研和设计文档
```

## 几条原则

- **许可证干净**：只复用 MIT 或 Apache-2.0 的代码，GPL / AGPL 只参考思路。
- **底座和业务分开**：平台协议一变，只需要改 `xianyu_core`。
- **账号安全优先**：内置限流和熔断；碰到滑块或身份验证时通知人工处理，不做自动破解；cookie 加密保存。

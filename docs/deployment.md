# 部署

部署用到的文件都在 [deploy/](../deploy/)：

| 文件 | 用途 |
|---|---|
| [deploy.sh](../deploy/deploy.sh) | 发版：在自己电脑上把已提交的代码部署到应用服务器 |
| [xym.sh](../deploy/xym.sh) | 在自己电脑上跑服务器上的 `xym` 命令（比如建用户） |
| [xym.service](../deploy/xym.service) | systemd 服务：跑后台 `xym serve` |
| [pg-tunnel.service](../deploy/pg-tunnel.service) | systemd 服务：连数据库的 SSH 隧道 |
| [Caddyfile](../deploy/Caddyfile)、[caddy-xym.conf](../deploy/caddy-xym.conf) | 对公网的 HTTPS 入口 |

## 架构

```
浏览器 ──https──▶ Caddy :443 ──▶ xym serve 127.0.0.1:8000 ──SSH 隧道──▶ PostgreSQL（数据库服务器的 127.0.0.1:5432）
         └─────────── 应用服务器 ───────────┘                                └──── 数据库服务器 ────┘
```

现在线上分两台服务器，`~/.ssh/config` 里叫 `huawei`（应用）和 `huawei-pg`（数据库），`deploy/` 下的脚本默认用这两个名字：

- **数据库服务器**：PostgreSQL 16 跑在 docker 里，端口只绑 127.0.0.1，不对外开；
- **应用服务器**：只放前后端。后台 `xym serve` 只听本机 8000，本身是明文 http，对公网由 Caddy 提供 HTTPS；
  用一条 SSH 隧道把本机的 127.0.0.1:5432 转到数据库服务器本机的 5432，后端照常连本机：
  `DATABASE_URL=postgresql://xianyu:密码@127.0.0.1:5432/xianyu`（密码里有 `@`、`:` 之类的字符要 URL 编码）。

两台机器内网不通，隧道走数据库服务器的公网 SSH。

## 应用服务器第一次装

下面的命令都在应用服务器上用 root 跑，在项目目录（或者把 `deploy/` 下的文件拷上去）里执行。

### 1. 后台服务

```bash
# uv：装和本机一样的版本（服务器从 GitHub 下载很慢的话，在本机下载好 scp 上去，核对 sha256）
install -m 755 uv uvx /usr/local/bin/
useradd --system --no-create-home --home-dir /opt/xianyu-manager/data --shell /usr/sbin/nologin xym
install -d -m 755 /opt/xianyu-manager
install -d -m 700 -o xym -g xym /opt/xianyu-manager/data /opt/xianyu-manager/data/accounts
# 写连接串和别的配置：DATABASE_URL=postgresql://xianyu:密码@127.0.0.1:5432/xianyu
install -m 600 /dev/null /etc/xym.env && vi /etc/xym.env
cp deploy/xym.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable xym
```

`/etc/xym.env` 里还可以写 AI 助手的模型、高德 key，见[配置](configuration.md)。

`xym` 服务以不能登录的 `xym` 用户运行，只监听本机 8000，排在 `pg-tunnel` 后面启动，连不上数据库就 5 秒后重试；
除了 `data/` 别的目录都只读。装好以后在自己电脑上跑一次[发版](#发版)。

### 2. 数据库隧道

两边各建一个不能登录的 `pgtunnel` 用户。数据库服务器上它的公钥限定成只能转发到 127.0.0.1:5432，拿到这把钥匙也开不了 shell、连不了别的端口。

```bash
useradd --system --create-home --home-dir /var/lib/pgtunnel --shell /usr/sbin/nologin pgtunnel
install -d -m 700 -o pgtunnel -g pgtunnel /var/lib/pgtunnel/.ssh
runuser -u pgtunnel -- ssh-keygen -t ed25519 -N "" -f /var/lib/pgtunnel/.ssh/id_ed25519
# 数据库服务器的主机公钥写进 /var/lib/pgtunnel/.ssh/known_hosts（先核对指纹）
echo "PG_SSH_HOST=<数据库服务器的 IP>" > /etc/pg-tunnel.env
# 数据库服务器上同样建 pgtunnel 用户，authorized_keys 写：
#   restrict,port-forwarding,permitopen="127.0.0.1:5432" <上面生成的 id_ed25519.pub>
cp deploy/pg-tunnel.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now pg-tunnel
systemctl status pg-tunnel
```

隧道断了 5 秒后自动重连；后端那边断的那一下查询会报错，下一次自动重连。

### 3. HTTPS 入口

Caddy 用官方的 apt 源装，以后 `apt upgrade` 跟着升级。云服务器的安全组要放行 80、443。

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

Caddy 在 443 上提供 HTTPS，转给本机的 8000；80 只用来跳转到 https 和申请证书。证书是 Let's Encrypt 给 IP 地址发的，
只有 6 天有效，Caddy 自己续。改了 Caddyfile 以后：

```bash
install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy
```

## 发版

在自己电脑上、项目根目录运行，把已提交的代码（HEAD）部署到应用服务器：

```bash
deploy/deploy.sh            # 默认主机 huawei，也可以传别的 ssh 主机名
```

它会：

1. 用 `git archive` 导出已提交的代码（没提交的改动不会上去），在本机打包前端（服务器上不用装 Node）；
2. rsync 到服务器的 `/opt/xianyu-manager`（服务器上的 `.venv` 和 `data/` 不动）；
3. 在服务器上 `uv sync --locked --no-dev`、`alembic upgrade head`、重启 `xym`，等接口能访问了才算完。

第一次装依赖从 PyPI 下载比较慢（十几分钟），之后 uv 有缓存，只下变了的包。

## 访问后台

浏览器打开 `https://<应用服务器的公网 IP>`。后台本身只听本机，不经过 Caddy 进不来。

不想走公网时也可以从自己电脑开 SSH 隧道（用 18000，免得和本机开发用的 8000 撞上），打开 http://127.0.0.1:18000 ：

```bash
ssh -N -L 18000:127.0.0.1:8000 huawei
```

对公网开放的安全措施：只走 HTTPS（浏览器记住一年，不会退回 http）；登录按用户名和 IP 限次；访问令牌 15 分钟；
CSP 等安全响应头；接口文档页不开；请求体最大 1MB。安全组除了 SSH 的 22 和 80、443，别的端口不用开。
后台能改价、下架、回消息，**用户密码要够长、别和别处共用**。

## 用户和闲鱼账号

服务器上的用户和闲鱼账号都和本机分开。

- **用户**在服务器上建。在自己电脑上用 `deploy/xym.sh` 跑服务器上的 `xym` 命令（SSH 过去、带上 `/etc/xym.env`、以 `xym` 用户运行，
  密码在本机终端里输）。第一个用户会接手服务器上以前扫码登录的闲鱼账号：

  ```bash
  deploy/xym.sh users add alice
  deploy/xym.sh users list
  ```

- **闲鱼账号**在页面上点「登录闲鱼」用 App 扫码，凭证存在服务器的 `data/accounts/`，不从本机拷。
  浏览器插件要推给服务器的话，把插件里的地址改成 `https://<公网 IP>`，令牌在服务器的页面上生成。

## 日常维护

```bash
journalctl -u xym -f          # 后台日志
journalctl -u pg-tunnel       # 隧道日志
journalctl -u caddy           # HTTPS 入口、证书续期、访问日志
```

想先看迁移要执行的 SQL（服务器上）：

```bash
cd /opt/xianyu-manager && set -a && . /etc/xym.env && .venv/bin/alembic upgrade head --sql
```

从自己电脑上临时连线上库（跑迁移、导数据、查数据），另开一条隧道，用 15432 免得和本机 docker 的 5432 撞上：

```bash
ssh -N -L 15432:127.0.0.1:5432 huawei-pg
DATABASE_URL=postgresql://xianyu:密码@127.0.0.1:15432/xianyu uv run alembic current
```

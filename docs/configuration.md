# 配置

## 环境变量

| 变量 | 用途 | 不设时 |
|---|---|---|
| `DATABASE_URL` | 数据库连接串（`xym` 也可以用 `--db` 指定） | 连本机 docker 里的 `postgresql://xianyu:xianyu@localhost:5432/xianyu` |
| `OPENAI_API_KEY` | AI 助手调模型的 key | AI 助手发不了消息 |
| `OPENAI_BASE_URL` | OpenAI 兼容接口的地址 | 直连 OpenAI |
| `XYM_AGENT_MODEL` | AI 助手用的模型 | `openai:gpt-6.1-sol` |
| `AMAP_KEY` | 高德 key，发布页按名字搜发货地 | 读 `data/amap.key`，也没有就只能从常用地址里选 |
| `XYM_GITHUB_TOKEN` | 问题反馈推成 GitHub issue 用的 token | 反馈能提交、处理，推不了 |
| `XYM_GITHUB_REPO` | 推到哪个仓库，写成 `owner/name` | 同上 |
| `TEST_DATABASE_URL` | 测试用的库（只有 `pytest` 用） | `postgresql://xianyu:xianyu@localhost:5432/xianyu_test` |

写在哪：

- **本机**：`cp .env.example .env`，填好以后重启 `uv run xym serve`。`xym` 启动时读项目根目录的 `.env`，终端里 `export` 过的优先。`.env` 不进 git；
- **服务器**：写在 `/etc/xym.env`（权限 600，root 才能读），改完 `systemctl restart xym`。见[部署](deployment.md)。

## 数据库

PostgreSQL 16。本机用 [docker-compose.yml](../docker-compose.yml) 起一个，端口只绑 127.0.0.1。
数据在 docker 卷里，`docker compose down` 不会丢，`docker compose down -v` 才会删。

表结构用 Alembic 迁移管理（[migrations/versions/](../migrations/versions/)）。拉了新代码以后跑一次 `uv run alembic upgrade head`；
表结构不是最新时 `xym serve` 会直接报错退出。改表的步骤见[开发](development.md#改表结构)。

连接串里的密码有 `@`、`:` 之类的字符要 URL 编码。

## 高德 key

发布页按名字搜发货地要一个高德开放平台的 key（[console.amap.com](https://console.amap.com/dev/key/app)，
个人认证开发者，建应用、添加 Key，**服务平台选"Web 服务"**）。key 只在后台用，不发给浏览器。没配的时候只能从常用地址里选。

- 本机：在 `.env` 里写 `AMAP_KEY=<key>`；或者存进 `data/amap.key`（一行），重启 `xym serve`：

  ```bash
  read -rs "k?高德 key: " && printf '%s\n' "$k" > data/amap.key && chmod 600 data/amap.key && unset k
  ```

  （这是 zsh 的写法，bash 用 `read -rsp "高德 key: " k`。）
- 服务器：在 `/etc/xym.env` 里加一行 `AMAP_KEY=<key>`，`systemctl restart xym`。

环境变量比文件优先。高德那边设了 IP 白名单的话，要把应用服务器的公网 IP 和本机出口 IP 都加上。

## AI 助手的模型

"AI 助手"页用 [PydanticAI](https://ai.pydantic.dev) 调 OpenAI 兼容接口上的模型，现在用的是
[魔搭社区](https://modelscope.cn)的 API-Inference（key 是魔搭的访问令牌：modelscope.cn → 账号设置 → 访问令牌）：

```
OPENAI_API_KEY=<魔搭的访问令牌>
OPENAI_BASE_URL=https://api-inference.modelscope.cn/v1
XYM_AGENT_MODEL=deepseek-ai/DeepSeek-V4.1-Flash-1017
```

- 模型名照魔搭模型页上的写。不带 `provider:` 前缀的都按 Chat Completions 调 `OPENAI_BASE_URL`；
- 不设 `XYM_AGENT_MODEL` 是直连 OpenAI 的 `openai:gpt-6.1-sol`（Responses API，要 OpenAI 的 key）；
- 带前缀的是 PydanticAI 的 `provider:model` 写法（比如 `openai:gpt-6.1-terra`）。换别家的（Claude 这些）要先装对应的
  pydantic-ai-slim 扩展，key 用那一家自己的环境变量；
- 国内服务器能直接连魔搭，直连 OpenAI 不通。

## 问题反馈推到 GitHub

"问题反馈"页上管理员点「推到 GitHub」，后台用 GitHub 的接口在一个仓库里建 issue。要一个 token：

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token；
2. Repository access 选 **Only select repositories**，只勾要推的那个仓库；
3. Permissions → Repository permissions → **Issues** 选 **Read and write**，别的都不用开（截图也够传，2026-11 在服务器上试过）；
4. 有效期按自己习惯选，过期了推送会报"token 不对或者过期了"，换一个新的就行。

```
XYM_GITHUB_TOKEN=github_pat_...
XYM_GITHUB_REPO=liumingliang0305-sudo/xianyu-manager
```

- 本机写在 `.env`，服务器写在 `/etc/xym.env`，改完重启（`systemctl restart xym`）；
- token 只在后台用，不发给浏览器；
- **仓库是公开的时候 issue 谁都看得到**。默认正文不带提交人，推送前弹窗里能改标题和正文、取消勾选截图，
  有手机号、订单号、闲鱼账号这类信息先删掉；
- 服务器要能连上 `api.github.com`，带截图时还有 `uploads.github.com`（一张要几秒）。连不上时推送会报"连不上 GitHub"，
  反馈本身不受影响，可以过会儿再推。截图上传接口没写进 GitHub 的 REST 文档，是网页和 gh `--attach` 用的那个；
  GitHub 哪天改了它，推送会在传截图那步报错，这时可以不勾截图先推文字。

## 本地数据

`data/` 放运行时的数据和凭证，不进 git。服务器上只有这个目录能写。

| 路径 | 内容 |
|---|---|
| `data/accounts/<unb>.json` | 闲鱼账号的凭证（cookie），权限 0600。**等同于账号密码，不要外传** |
| `data/accounts/<unb>.im.json` | 即时消息用的设备 ID，第一次连的时候生成，之后一直用同一个，不要删 |
| `data/accounts/<unb>.im.lock` | 文件锁，保证一个账号只有一个 `xym serve` 在连，进程退出时系统自动放开 |
| `data/images/<sha256>.jpg` | 发布草稿、拼多多和第三方货源的图片、问题反馈的截图（统一转成 JPEG，同一张图只存一份），都不用了才删 |
| `data/auth.key` | 管理后台访问令牌的签名密钥，第一次启动时生成，见[用户和登录](auth.md#鉴权) |
| `data/guard.json` | 每个账号的风控熔断状态，见 [guard.md](guard.md) |
| `data/amap.key` | 高德 key（可选） |

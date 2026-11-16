#!/usr/bin/env bash
# 构建并部署一个已提交的版本，任意工作目录都能运行。
#
#   deploy/deploy.sh [ssh 主机] [版本]   默认 huawei、HEAD；版本可填 tag 或提交 SHA
#
# 服务器的 uv、xym 用户、/etc/xym.env 和 systemd 服务先按 docs/deployment.md 配好。
# 只部署 git archive 导出的代码；服务器上的 .venv、data/ 不随 rsync 删除或上传。
set -euo pipefail

if [ "$#" -gt 2 ]; then
  echo "用法：deploy/deploy.sh [ssh 主机] [tag 或提交 SHA]" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
HOST=${1:-huawei}
TARGET=${2:-HEAD}
APP=/opt/xianyu-manager
REV=$(git rev-parse --verify --end-of-options "${TARGET}^{commit}")
SSH_OPTIONS=(-o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)

if [ -n "$(git status --porcelain)" ]; then
  echo "注意：有没提交的改动，这次只部署已提交的 $REV" >&2
fi

TMP=$(mktemp -d)
REMOTE_STAGE=
cleanup() {
  rm -rf "$TMP"
  if [ -n "$REMOTE_STAGE" ]; then
    ssh "${SSH_OPTIONS[@]}" -- "$HOST" rm -rf -- "$REMOTE_STAGE" || true
  fi
}
trap cleanup EXIT
chmod 755 "$TMP" # mktemp 默认 700，不能把这个权限复制给应用目录，否则 xym 读不到代码。

echo "打包 ${TARGET}（${REV}）的前端"
git archive "$REV" | tar -x -C "$TMP"
echo "$REV" >"$TMP/REVISION"
(cd "$TMP/web" && pnpm install --frozen-lockfile --silent && pnpm build >/dev/null)

# 先完整上传到临时目录，再加锁安装，避免传到一半影响正在运行的服务。
REMOTE_STAGE=$(ssh "${SSH_OPTIONS[@]}" -- "$HOST" mktemp -d /opt/xianyu-manager-release.XXXXXXXX)
if [[ ! "$REMOTE_STAGE" =~ ^/opt/xianyu-manager-release\.[[:alnum:]]+$ ]]; then
  REMOTE_STAGE=
  echo "服务器返回的临时目录不正确，停止部署" >&2
  exit 1
fi
echo "上传到 $HOST:$REMOTE_STAGE"
rsync -az --no-owner --no-group \
  -e 'ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3' \
  "$TMP/" "$HOST:$REMOTE_STAGE/"

echo "安装代码、同步依赖、迁移数据库并启动"
# 参数都是固定路径、服务端生成且已校验的临时目录、Git 返回的 SHA，没有 shell 插值。
ssh "${SSH_OPTIONS[@]}" -- "$HOST" bash -s -- "$APP" "$REMOTE_STAGE" "$REV" <<'REMOTE'
set -euo pipefail
app=$1
stage=$2
revision=$3

# 锁覆盖复制代码、依赖安装、迁移和启动，两个部署不能交叉写线上目录。
exec 9>/run/lock/xianyu-manager-deploy.lock
flock -n 9 || { echo "已有部署正在进行，请等它完成" >&2; exit 1; }
test "$(cat "$stage/REVISION")" = "$revision"
test -r /etc/xym.env
id xym >/dev/null
command -v uv >/dev/null
command -v rsync >/dev/null
command -v curl >/dev/null

failed() {
  echo "部署失败：$revision" >&2
  systemctl status xym --no-pager --lines=0 >&2 || true
  journalctl -u xym -n 30 --no-pager >&2 || true
}
trap failed ERR

# 暂停旧进程再换文件和迁移，避免旧代码读取新文件、新表结构。
systemctl stop xym
rsync -a --delete --no-owner --no-group \
  --exclude /.venv --exclude /data --exclude /web/node_modules \
  "$stage/" "$app/"
cd "$app"
UV_PYTHON_DOWNLOADS=never uv sync --locked --no-dev --compile-bytecode --quiet
set -a
. /etc/xym.env
set +a
.venv/bin/alembic upgrade head
runuser -u xym -- test -r web/dist/index.html
systemctl start xym

healthy() {
  systemctl is-active --quiet xym &&
    curl --connect-timeout 2 --max-time 3 -fsS http://127.0.0.1:8000/api/health |
    .venv/bin/python -c 'import json,sys; r=json.load(sys.stdin); sys.exit(not (r.get("ok") is True and r.get("revision") == sys.argv[1]))' "$revision"
}
for _ in $(seq 1 30); do
  if healthy >/dev/null 2>&1; then
    echo "健康检查通过：运行版本 $revision"
    exit 0
  fi
  sleep 1
done
echo "健康检查未通过，或运行的版本与目标不一致" >&2
false
REMOTE

echo "已部署 ${TARGET}（${REV}）。"

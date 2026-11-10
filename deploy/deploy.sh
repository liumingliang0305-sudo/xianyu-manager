#!/usr/bin/env bash
# 把已提交的代码（HEAD）部署到应用服务器：打包前端、传代码、装依赖、跑迁移、重启 xym。
#
#   deploy/deploy.sh [ssh 主机]      在自己电脑上、项目根目录运行，主机默认 huawei
#
# 服务器第一次要先装好 uv、xym 用户、/etc/xym.env 和两个 systemd 服务，见 docs/deployment.md。
# 只传已提交的代码（git archive），没提交的改动不会上去。服务器上的 .venv 和 data/ 不动。
set -euo pipefail

HOST=${1:-huawei}
APP=/opt/xianyu-manager
REV=$(git rev-parse --short HEAD)

if [ -n "$(git status --porcelain)" ]; then
  echo "注意：有没提交的改动，这次只部署已提交的 $REV" >&2
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
chmod 755 "$TMP" # rsync 会把这个目录的权限带到服务器的 /opt/xianyu-manager，mktemp 默认 700，xym 进不去

echo "打包 $REV 的前端"
git archive HEAD | tar -x -C "$TMP"
echo "$REV" >"$TMP/REVISION"
(cd "$TMP/web" && pnpm install --frozen-lockfile --silent && pnpm build >/dev/null)

echo "传到 $HOST:$APP"
rsync -az --delete --no-owner --no-group \
  --exclude /.venv --exclude /data --exclude /web/node_modules \
  "$TMP/" "$HOST:$APP/"

echo "装依赖、跑迁移、重启"
# shellcheck disable=SC2029  # $APP 就是要在本机展开
ssh "$HOST" "set -euo pipefail
cd $APP
UV_PYTHON_DOWNLOADS=never uv sync --locked --no-dev --compile-bytecode --quiet
set -a; . /etc/xym.env; set +a
.venv/bin/alembic upgrade head
systemctl restart xym
for _ in \$(seq 1 30); do
  curl -fs -o /dev/null http://127.0.0.1:8000/api/health && break
  sleep 1
done
curl -fsS -o /dev/null http://127.0.0.1:8000/api/health || { journalctl -u xym -n 30 --no-pager; exit 1; }
"
# 变量后面紧跟中文时要加花括号：macOS 自带的 bash 3.2 会把"。"的第一个字节算进变量名
echo "已部署 ${REV}。访问：https://<应用服务器的公网 IP>（Caddy），或者 ssh -N -L 18000:127.0.0.1:8000 $HOST 走隧道"

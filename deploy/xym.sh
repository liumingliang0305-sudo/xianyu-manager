#!/usr/bin/env bash
# 在应用服务器上跑 xym 命令。在自己电脑上、项目根目录运行：
#
#   deploy/xym.sh users add alice            建管理后台的用户，在这个终端里输密码
#   deploy/xym.sh users list
#   XYM_HOST=别的主机 deploy/xym.sh users list
#
# 到服务器的项目目录、带上 /etc/xym.env 里的数据库连接串，以 xym 用户运行：命令写出来的文件
# （凭证、熔断状态）后台还能改。用 root 跑会在 data/ 里留下 xym 改不了的文件。
set -euo pipefail

HOST=${XYM_HOST:-huawei}
APP=/opt/xianyu-manager

if [ $# -eq 0 ]; then
  echo "用法：deploy/xym.sh <xym 的参数>，比如 deploy/xym.sh users add alice" >&2
  exit 2
fi

# 参数原样转过去（带空格、引号的也不会被服务器的 shell 拆开）；-t 给远端一个终端，输密码不回显
# shellcheck disable=SC2029  # 就是要在本机展开
ssh -t "$HOST" "cd $APP && set -a && . /etc/xym.env && set +a && exec runuser -u xym -- .venv/bin/xym $(printf '%q ' "$@")"

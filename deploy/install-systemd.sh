#!/usr/bin/env bash
# 一键把 Fund Lens 安装为 systemd 服务（非 Docker 部署）。
# 用法：  sudo bash deploy/install-systemd.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_USER="${SERVICE_USER:-fundlens}"
UNIT=/etc/systemd/system/fund-lens.service
# 国内网络默认走阿里云镜像（实测可用）；走官方源： PIP_INDEX_URL=https://pypi.org/simple sudo -E bash ...
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 运行： sudo bash deploy/install-systemd.sh" >&2
  exit 1
fi

echo "[1/6] 安装目录: $APP_DIR"

# 依赖自检：python3 venv（缺则尝试 apt 安装）
if ! python3 -c "import venv" >/dev/null 2>&1; then
  echo "      未检测到 python3-venv，尝试用 apt 安装"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip
  else
    echo "请先手动安装 python3 / python3-venv 后重试" >&2
    exit 1
  fi
fi

# 专用账号
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "[2/6] 创建系统账号 $SERVICE_USER"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
  echo "[2/6] 账号 $SERVICE_USER 已存在"
fi

# venv + 依赖
echo "[3/6] 创建 venv 并安装依赖（含 akshare，耗时稍长；镜像源: $PIP_INDEX_URL）"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --index-url "$PIP_INDEX_URL" --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q --index-url "$PIP_INDEX_URL" -r "$APP_DIR/requirements.txt"

# .env
if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "[4/6] 生成 .env（请稍后修改 SECRET_KEY / ADMIN_PASSWORD / DB_PATH）"
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  SK="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  sed -i "s#^SECRET_KEY=.*#SECRET_KEY=${SK}#" "$APP_DIR/.env"
  sed -i "s#^DB_PATH=.*#DB_PATH=${APP_DIR}/data/fundlens.db#" "$APP_DIR/.env"
else
  echo "[4/6] 已存在 .env，跳过（请确认 DB_PATH 指向绝对路径）"
fi

mkdir -p "$APP_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/data"

# 生成 unit
echo "[5/6] 写入 $UNIT"
sed -e "s#^WorkingDirectory=.*#WorkingDirectory=${APP_DIR}#" \
    -e "s#^EnvironmentFile=.*#EnvironmentFile=${APP_DIR}/.env#" \
    -e "s#^ExecStart=.*#ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port \${PORT}#" \
    -e "s#^User=.*#User=${SERVICE_USER}#" \
    -e "s#^Group=.*#Group=${SERVICE_USER}#" \
    "$APP_DIR/deploy/fund-lens.service" > "$UNIT"

echo "[6/6] 启用并启动服务"
systemctl daemon-reload
systemctl enable --now fund-lens.service
sleep 2
systemctl --no-pager --full status fund-lens.service | head -n 12 || true

echo
echo "完成。查看日志:  journalctl -u fund-lens -f"
echo "建用户:        sudo -u ${SERVICE_USER} ${APP_DIR}/.venv/bin/python -m app.admin add <用户名> <密码>"
echo "（也可在 .env 设 ADMIN_USER/ADMIN_PASSWORD，首次启动库空时自动建号）"

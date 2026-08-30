#!/usr/bin/env bash
# Налаштування сервера під бота. Запускається НА СЕРВЕРІ від root.
# Ідемпотентний: повторний запуск нічого не ламає.
set -euo pipefail

REPO="https://github.com/ascentman/school-bot.git"
BRANCH="${BRANCH:-main}"
APP_USER="bot"
APP_DIR="/opt/school-bot"

say() { printf "\n\033[1;32m▶ %s\033[0m\n" "$1"; }

say "Блокування емульованого дисковода"
# Без цього apt намертво зависає: blkid намагається прочитати неіснуючий
# /dev/fd0 і лишається в незупинному стані (D). udev щохвилини запускає
# новий — за годину їх накопичується десяток, load average сягає 30,
# а initramfs так і не збирається. Перевірено на 1gb.ua.
printf 'blacklist floppy\ninstall floppy /bin/true\n' > /etc/modprobe.d/blacklist-floppy.conf
rmmod floppy 2>/dev/null || true

say "Оновлення системи"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Саме full-upgrade: звичайний upgrade не встановлює нові пакети, тож
# оновлення ядра лишається незастосованим, і do-release-upgrade потім
# відмовляється працювати.
apt-get full-upgrade -y -qq

say "Базові пакети"
# fonts-dejavu-core — кирилиця в PDF-звітах; tzdata — Europe/Kyiv
apt-get install -y -qq git curl ufw fail2ban fonts-dejavu-core tzdata
timedatectl set-timezone Europe/Kyiv

say "Користувач $APP_USER"
id -u "$APP_USER" &>/dev/null || useradd --create-home --shell /bin/bash "$APP_USER"

say "Файрвол: назовні лише SSH"
# Порт беремо зі sshd_config, а не зашиваємо 22: якщо адміністратор
# переніс SSH на інший порт, жорсткий allow 22 замкнув би його назовні.
SSH_PORTS=$(sshd -T 2>/dev/null | awk '/^port /{print $2}')
[ -z "$SSH_PORTS" ] && SSH_PORTS=$(awk '/^[[:space:]]*Port[[:space:]]/{print $2}' /etc/ssh/sshd_config)
[ -z "$SSH_PORTS" ] && SSH_PORTS=22
echo "  SSH слухає порти: $SSH_PORTS"

# Бот сам ходить до Telegram і Google, вхідних портів йому не треба.
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
for port in $SSH_PORTS; do ufw allow "$port"/tcp >/dev/null; done
ufw --force enable >/dev/null

say "fail2ban проти перебору SSH"
systemctl enable --now fail2ban >/dev/null

say "uv (несе власний Python 3.12 — системний тут 3.8)"
su - "$APP_USER" -c 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'

say "Код"
if [ -d "$APP_DIR/.git" ]; then
    su - "$APP_USER" -c "cd $APP_DIR && git fetch --quiet origin && git reset --hard --quiet origin/$BRANCH"
else
    mkdir -p "$APP_DIR" && chown "$APP_USER:$APP_USER" "$APP_DIR"
    su - "$APP_USER" -c "git clone --quiet --branch $BRANCH $REPO $APP_DIR"
fi

say "Залежності"
su - "$APP_USER" -c "cd $APP_DIR && ~/.local/bin/uv sync --frozen --no-dev"

say "Служба systemd"
cat > /etc/systemd/system/school-bot.service <<'UNIT'
[Unit]
Description=Telegram-бот обліку харчування учнів
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=bot
WorkingDirectory=/opt/school-bot
# Готова точка входу, а не «uv run»: uv пише кеш у домашню теку, яку
# ProtectHome=read-only закриває, і служба падає по колу. До того ж
# перевіряти залежності при кожному старті ні до чого — вони вже стоять.
ExecStart=/opt/school-bot/.venv/bin/school-bot run
Restart=always
RestartSec=10

# Обмеження прав: боту потрібні лише власний каталог і мережа.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/school-bot/data /opt/school-bot/reports_out
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
UNIT

# secrets/ у .gitignore, тож клон його не створює — а scp у неіснуючий
# каталог падає з «No such file or directory».
mkdir -p "$APP_DIR/data" "$APP_DIR/reports_out" "$APP_DIR/secrets"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
systemctl daemon-reload
systemctl enable school-bot >/dev/null

say "Щонічний бекап БД о 03:00"
cat > /etc/systemd/system/school-bot-backup.service <<'UNIT'
[Unit]
Description=Резервна копія бази бота

[Service]
Type=oneshot
User=bot
WorkingDirectory=/opt/school-bot
ExecStart=/opt/school-bot/.venv/bin/school-bot backup
UNIT
cat > /etc/systemd/system/school-bot-backup.timer <<'UNIT'
[Unit]
Description=Щонічна резервна копія бази бота

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now school-bot-backup.timer >/dev/null

# Повторний запуск оновлює код — але процес продовжив би працювати зі
# старою версією, поки хтось не перезапустить вручну.
if systemctl is-active --quiet school-bot; then
    say "Перезапуск служби з оновленим кодом"
    systemctl restart school-bot
fi

say "Готово"
cat <<'NEXT'

Лишилося перенести секрети з робочої машини:

  scp .env root@СЕРВЕР:/opt/school-bot/.env
  scp secrets/service-account.json root@СЕРВЕР:/opt/school-bot/secrets/

  ssh root@СЕРВЕР '
    chown bot:bot /opt/school-bot/.env /opt/school-bot/secrets/service-account.json
    chmod 600 /opt/school-bot/.env /opt/school-bot/secrets/service-account.json
    systemctl start school-bot
  '

NEXT

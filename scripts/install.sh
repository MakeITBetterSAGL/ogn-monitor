#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SERVICE_USER=${SUDO_USER:-$(id -un)}
SERVICE_GROUP=$(id -gn "$SERVICE_USER")

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/config.example.env" "$PROJECT_DIR/.env"
    echo "Created $PROJECT_DIR/.env — edit the station settings before starting the services."
fi

chmod +x "$PROJECT_DIR/scripts/update-ogn-ddb.sh"
mkdir -p "$PROJECT_DIR/database"

escape_sed() { printf '%s' "$1" | sed 's/[&|]/\\&/g'; }
install_unit() {
    source_file=$1
    target_name=$2
    sed \
        -e "s|@INSTALL_DIR@|$(escape_sed "$PROJECT_DIR")|g" \
        -e "s|@USER@|$(escape_sed "$SERVICE_USER")|g" \
        -e "s|@GROUP@|$(escape_sed "$SERVICE_GROUP")|g" \
        "$source_file" | sudo tee "/etc/systemd/system/$target_name" >/dev/null
}

install_unit "$PROJECT_DIR/services/ogn-collector.service.in" ogn-collector.service
install_unit "$PROJECT_DIR/services/ogn-parser.service.in" ogn-parser.service
install_unit "$PROJECT_DIR/services/ogn-monitor.service.in" ogn-monitor.service
install_unit "$PROJECT_DIR/services/ogn-ddb-update.service.in" ogn-ddb-update.service
install_unit "$PROJECT_DIR/services/ogn-retention.service.in" ogn-retention.service
sudo cp "$PROJECT_DIR/services/ogn-ddb-update.timer" /etc/systemd/system/
sudo cp "$PROJECT_DIR/services/ogn-retention.timer" /etc/systemd/system/
sudo systemctl daemon-reload

echo
echo "Installation complete. Next:"
echo "  1. Edit $PROJECT_DIR/.env"
echo "  2. Run $PROJECT_DIR/scripts/update-ogn-ddb.sh"
echo "  3. Run: sudo systemctl enable --now ogn-collector ogn-parser ogn-monitor ogn-ddb-update.timer ogn-retention.timer"
echo "  4. Open: http://<raspberry-pi-address>:5000"

#!/usr/bin/env bash
# =============================================================================
# install_node_exporter.sh
#
# Instala y configura node_exporter en un worker (Ubuntu 20.04+, amd64).
# Expone métricas del host en puerto 9100.
#
# Uso:
#   chmod +x install_node_exporter.sh
#   sudo ./install_node_exporter.sh
#
# Opciones:
#   --port <N>    Puerto de escucha (default: 9100)
#   --dry-run     Muestra los pasos sin ejecutarlos
# =============================================================================

set -euo pipefail

VERSION="1.11.1"
PORT=9100
DRY_RUN=false
BINARY_URL="https://github.com/prometheus/node_exporter/releases/download/v${VERSION}/node_exporter-${VERSION}.linux-amd64.tar.gz"
INSTALL_DIR="/usr/local/bin"
SERVICE_FILE="/etc/systemd/system/node-exporter.service"
SERVICE_USER="node-exporter"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()     { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()    { echo -e "\n${BLUE}──────────────────────────────────────────${NC}"; echo -e "${BLUE}▶ $*${NC}"; }

run() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}[DRY-RUN]${NC} $*"
    else
        eval "$@"
    fi
}

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)    PORT="$2";    shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) error "Unknown option: $1" ;;
    esac
done

[[ $EUID -ne 0 ]] && error "Must be run as root (sudo)."

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   node_exporter installer — PUCP Cloud      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
log "Version : $VERSION"
log "Port    : $PORT"
log "Dry run : $DRY_RUN"
echo ""

# ── Step 1: Download and install binary ───────────────────────────────────────
step "Downloading node_exporter v${VERSION}"

TMPDIR=$(mktemp -d)
run wget -q --show-progress -O "${TMPDIR}/node_exporter.tar.gz" "$BINARY_URL"
run tar xzf "${TMPDIR}/node_exporter.tar.gz" -C "$TMPDIR"
run mv "${TMPDIR}/node_exporter-${VERSION}.linux-amd64/node_exporter" "${INSTALL_DIR}/node_exporter"
run chmod +x "${INSTALL_DIR}/node_exporter"
run rm -rf "$TMPDIR"
success "Binary installed at ${INSTALL_DIR}/node_exporter"

# ── Step 2: Create dedicated system user ──────────────────────────────────────
step "Setting up service user"

if ! id "$SERVICE_USER" &>/dev/null; then
    run useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    success "Created user: $SERVICE_USER"
else
    success "User $SERVICE_USER already exists"
fi

# ── Step 3: Write systemd service ─────────────────────────────────────────────
step "Creating systemd service"

if [ "$DRY_RUN" = false ]; then
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Prometheus Node Exporter — PUCP Cloud
Documentation=https://github.com/prometheus/node_exporter
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}

ExecStart=${INSTALL_DIR}/node_exporter \\
    --web.listen-address=":${PORT}" \\
    --collector.filesystem.mount-points-exclude='^/(dev|proc|sys|var/lib/docker/.+|run/containerd.+)(\$|/)' \\
    --collector.netdev.device-exclude='^(veth|tap|virbr|docker|br-).*\$'

Restart=on-failure
RestartSec=5s

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
EOF
else
    echo -e "${YELLOW}[DRY-RUN]${NC} Would write $SERVICE_FILE"
fi
success "Service file written: $SERVICE_FILE"

# ── Step 4: Enable and start ──────────────────────────────────────────────────
step "Enabling and starting service"

run systemctl daemon-reload
run systemctl enable node-exporter
run systemctl restart node-exporter

# Retry check
if curl -sf "http://localhost:${PORT}/metrics" | grep "node_cpu_seconds_total" > /dev/null; then
    RETRIES=5; OK=false
    for i in $(seq 1 $RETRIES); do
        sleep 2
        if curl -sf "http://localhost:${PORT}/metrics" | grep -q "node_cpu_seconds_total"; then
            OK=true; break
        fi
        log "Waiting for endpoint... ($i/$RETRIES)"
    done
    if [ "$OK" = false ]; then
        error "Endpoint not responding. Check: journalctl -u node-exporter -n 50"
    fi
    success "Metrics endpoint responding"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Installation complete           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
log "Service : node-exporter"
log "Version : ${VERSION}"
log "Port    : ${PORT}"
echo ""
log "Useful commands:"
echo "  systemctl status node-exporter"
echo "  journalctl -u node-exporter -f"
echo "  curl http://localhost:${PORT}/metrics | grep node_cpu_seconds_total"
echo ""
warn "Remember: add port ${PORT} to autossh tunnels in setup_tunnels.sh"
warn "And add host.docker.internal:<local_port> to prometheus.yml scrape targets"
echo ""

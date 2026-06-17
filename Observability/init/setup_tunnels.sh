#!/usr/bin/env bash
# =============================================================================
# setup_tunnels.sh
#
# Configura tunnels autossh persistentes en server1 para exponer los
# exporters de todos los workers como puertos locales.
# Por worker se crean DOS tunnels en un mismo servicio:
#   - libvirt_exporter (puerto 9177) → local 191XX
#   - node_exporter   (puerto 9100) → local 192XX
#
# Ejecutar en: server1 (la máquina donde corren los containers)
#
# Uso:
#   sudo ./setup_tunnels.sh [--dry-run]
#
# Editar GW_IP y WORKERS antes de ejecutar.
# =============================================================================

set -euo pipefail

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

# ── Gateway Configuration ─────────────────────────────────────────────────────
GW_IP="10.20.11.119"
GW_USER="ubuntu"
GW_KEY="/home/ubuntu/.ssh/id_ed25519"
DRY_RUN=false
LIBVIRT_PORT=9177
NODE_PORT=9100

# =============================================================================
# WORKER CONFIGURATION
# Formato: "worker_id:worker_ip:gw_ssh_port:local_libvirt_port:local_node_port"
# =============================================================================
WORKERS=(
    "1:192.168.201.1:5811:19177:19277"
    "2:192.168.201.2:5812:19178:19278"
    "3:192.168.201.3:5813:19179:19279"
    "4:192.168.201.4:5814:19180:19280"
    "5:192.168.202.2:5822:19181:19281"
    "6:192.168.202.3:5823:19182:19282"
    "7:192.168.202.4:5824:19183:19283"
)
# =============================================================================

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) DRY_RUN=true; shift ;;
        *) error "Unknown option: $1" ;;
    esac
done

[[ $EUID -ne 0 ]] && error "Must be run as root (sudo)."

# ── Check autossh ─────────────────────────────────────────────────────────────
step "Checking dependencies"
if ! command -v autossh &>/dev/null; then
    run apt-get update -qq && run apt-get install -y autossh
fi
success "autossh available"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   autossh tunnel setup — PUCP Cloud         ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
log "GW IP    : $GW_IP"
log "GW User  : $GW_USER"
log "Workers  : ${#WORKERS[@]}"
echo ""

# ── Create systemd service per worker ────────────────────────────────────────
step "Creating autossh systemd services"

for entry in "${WORKERS[@]}"; do
    IFS=':' read -r worker_id worker_ip gw_port local_libvirt local_node <<< "$entry"

    SERVICE_NAME="autossh-worker${worker_id}"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    log "Worker $worker_id → libvirt localhost:$local_libvirt  node localhost:$local_node  (via GW:$gw_port → $worker_ip)"

    if [ "$DRY_RUN" = false ]; then
        cat > "$SERVICE_FILE" << EOF
[Unit]
Description=autossh tunnel to worker ${worker_id} (${worker_ip}) — PUCP Cloud
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment="AUTOSSH_GATETIME=0"
Environment="AUTOSSH_LOGFILE=/var/log/autossh-worker${worker_id}.log"

ExecStart=/usr/bin/autossh -M 0 \
    -o "ServerAliveInterval=30" \
    -o "ServerAliveCountMax=3" \
    -o "StrictHostKeyChecking=no" \
    -o "ExitOnForwardFailure=yes" \
    -i ${GW_KEY} \
    -N \
    -L 0.0.0.0:${local_libvirt}:${worker_ip}:${LIBVIRT_PORT} \
    -L 0.0.0.0:${local_node}:${worker_ip}:${NODE_PORT} \
    ${GW_USER}@${GW_IP} -p ${gw_port}

Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF
    else
        echo -e "${YELLOW}[DRY-RUN]${NC} Would write $SERVICE_FILE"
    fi

    success "Created $SERVICE_FILE"
done

# ── Enable and start all services ─────────────────────────────────────────────
step "Enabling and starting tunnel services"

run systemctl daemon-reload

for entry in "${WORKERS[@]}"; do
    IFS=':' read -r worker_id worker_ip gw_port local_libvirt local_node <<< "$entry"
    SERVICE_NAME="autossh-worker${worker_id}"
    run systemctl enable "$SERVICE_NAME"
    run systemctl restart "$SERVICE_NAME"
    success "Started $SERVICE_NAME"
done

# ── Verify tunnels ────────────────────────────────────────────────────────────
step "Verifying tunnels (waiting 5s for connections)"

if [ "$DRY_RUN" = false ]; then
    sleep 5
    all_ok=true
    for entry in "${WORKERS[@]}"; do
        IFS=':' read -r worker_id worker_ip gw_port local_libvirt local_node <<< "$entry"

        if curl -sf --max-time 3 "http://localhost:${local_libvirt}/metrics" | grep -q "go_goroutines" 2>/dev/null; then
            success "worker-${worker_id} libvirt → localhost:${local_libvirt} ✓"
        else
            warn "worker-${worker_id} libvirt → localhost:${local_libvirt} — no response yet"
            all_ok=false
        fi

        if curl -sf --max-time 3 "http://localhost:${local_node}/metrics" | grep -q "node_cpu_seconds_total" 2>/dev/null; then
            success "worker-${worker_id} node   → localhost:${local_node} ✓"
        else
            warn "worker-${worker_id} node   → localhost:${local_node} — no response yet"
            all_ok=false
        fi
    done

    if [ "$all_ok" = true ]; then
        success "All tunnels responding"
    else
        warn "Some tunnels not responding — check: systemctl status autossh-worker<N>"
    fi
fi

# ── Print targets.yml ─────────────────────────────────────────────────────────
step "targets.yml for observability microservice"

echo ""
log "Copy to observability/config/targets.yml:"
echo ""
echo "workers:"
for entry in "${WORKERS[@]}"; do
    IFS=':' read -r worker_id worker_ip gw_port local_libvirt local_node <<< "$entry"
    echo "  - worker_id: ${worker_id}"
    echo "    prometheus_target: \"host.docker.internal:${local_libvirt}\""
    echo "    node_target:       \"host.docker.internal:${local_node}\""
done

echo ""
log "Add to observability/config/prometheus.yml (job: node):"
for entry in "${WORKERS[@]}"; do
    IFS=':' read -r worker_id worker_ip gw_port local_libvirt local_node <<< "$entry"
    echo "          - 'host.docker.internal:${local_node}'   # worker ${worker_id}"
done
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Setup complete                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
log "Useful commands:"
echo "  systemctl status autossh-worker<N>"
echo "  journalctl -u autossh-worker<N> -f"
echo "  curl http://localhost:<port>/metrics | head -3"
echo ""
log "To stop all tunnels:"
echo "  systemctl stop autossh-worker{1..7}"
echo ""

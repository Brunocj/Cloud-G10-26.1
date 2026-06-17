#!/usr/bin/env bash
# =============================================================================
# install_libvirt_exporter.sh
#
# Instala y configura prometheus-libvirt-exporter en un worker.
# Compatible con Linux Cluster y OpenStack workers (Ubuntu 20.04+).
#
# Uso:
#   chmod +x install_libvirt_exporter.sh
#   sudo ./install_libvirt_exporter.sh [--type linux|openstack] [--port 9177]
#
# Opciones:
#   --type       Tipo de worker: 'linux' (default) o 'openstack'
#   --port       Puerto de escucha (default: 9177)
#   --dry-run    Muestra los pasos sin ejecutarlos
# =============================================================================

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
WORKER_TYPE="linux"
PORT=9177
DRY_RUN=false
VERSION="2.4.0"
DEB_URL="https://github.com/inovex/prometheus-libvirt-exporter/releases/download/v${VERSION}/prometheus-libvirt-exporter-${VERSION}.amd64.deb"
DEB_FILE="/tmp/prometheus-libvirt-exporter-${VERSION}.amd64.deb"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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
        --type)    WORKER_TYPE="$2"; shift 2 ;;
        --port)    PORT="$2";        shift 2 ;;
        --dry-run) DRY_RUN=true;     shift ;;
        *) error "Unknown option: $1. Use --type linux|openstack --port <N> --dry-run" ;;
    esac
done

if [[ "$WORKER_TYPE" != "linux" && "$WORKER_TYPE" != "openstack" ]]; then
    error "Invalid --type '$WORKER_TYPE'. Must be 'linux' or 'openstack'."
fi

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (use sudo)."
fi

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   libvirt_exporter installer — PUCP Cloud   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
log "Worker type : $WORKER_TYPE"
log "Listen port : $PORT"
log "Version     : $VERSION"
log "Dry run     : $DRY_RUN"
echo ""

# ── Step 1: Download and install .deb ─────────────────────────────────────────
step "Downloading prometheus-libvirt-exporter v${VERSION}"

run wget -q --show-progress -O "$DEB_FILE" "$DEB_URL"
success "Downloaded $DEB_FILE"

step "Installing .deb package"
run dpkg -i "$DEB_FILE"
run rm -f "$DEB_FILE"
success "Package installed"

# ── Step 2: Check libvirt availability ────────────────────────────────────────
step "Checking libvirt availability"

if ! command -v virsh &>/dev/null; then
    warn "virsh not found — installing libvirt-clients"
    run apt-get update -qq
    run apt-get install -y libvirt-clients
fi

if systemctl is-active --quiet libvirtd 2>/dev/null || \
   systemctl is-active --quiet libvirt-bin 2>/dev/null; then
    success "libvirt daemon is running"
else
    warn "libvirt daemon not detected as active — may be managed differently on OpenStack"
fi

# ── Step 3: Fix permissions — add service user to libvirt group ───────────────
step "Configuring permissions"

# Determine which user runs the service
SERVICE_USER=$(systemctl show prometheus-libvirt-exporter --property=User --value 2>/dev/null || echo "root")
if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == "" ]]; then
    SERVICE_USER="root"
fi
log "Service runs as: $SERVICE_USER"

if getent group libvirt &>/dev/null; then
    run usermod -aG libvirt "$SERVICE_USER"
    success "Added $SERVICE_USER to libvirt group"
fi

# OpenStack: also add to nova group if present
if [[ "$WORKER_TYPE" == "openstack" ]] && getent group nova &>/dev/null; then
    run usermod -aG nova "$SERVICE_USER"
    success "Added $SERVICE_USER to nova group (OpenStack)"
fi

# ── Step 4: Override port if different from default ───────────────────────────
if [[ "$PORT" != "9177" ]]; then
    step "Configuring custom port $PORT"

    OVERRIDE_DIR="/etc/systemd/system/prometheus-libvirt-exporter.service.d"
    run mkdir -p "$OVERRIDE_DIR"
    run cat > "${OVERRIDE_DIR}/port.conf" << EOF
[Service]
ExecStart=
ExecStart=/usr/bin/prometheus-libvirt-exporter --web.listen-address :${PORT}
EOF
    success "Port override written"
fi

# ── Step 5: Enable and start ──────────────────────────────────────────────────
step "Enabling and starting service"

run systemctl daemon-reload
run systemctl enable prometheus-libvirt-exporter
run systemctl restart prometheus-libvirt-exporter

sleep 2

if [ "$DRY_RUN" = false ]; then
    if systemctl is-active --quiet prometheus-libvirt-exporter; then
        success "Service is running"
    else
        error "Service failed to start. Check: journalctl -u prometheus-libvirt-exporter -n 50"
    fi
fi

# ── Step 6: Verify metrics endpoint ───────────────────────────────────────────
step "Verifying metrics endpoint"

if [ "$DRY_RUN" = false ]; then
    # Retry up to 5 times with 2s intervals
    RETRIES=5
    OK=false
    for i in $(seq 1 $RETRIES); do
        sleep 2
        if curl -sf "http://localhost:${PORT}/metrics" | grep -q "go_goroutines"; then
            OK=true
            break
        fi
        log "Waiting for endpoint... ($i/$RETRIES)"
    done
    if [ "$OK" = false ]; then
        error "Endpoint not responding after ${RETRIES} attempts. Check: journalctl -u prometheus-libvirt-exporter -n 50"
    fi
    if curl -sf "http://localhost:${PORT}/metrics" | grep -q "go_goroutines"; then
        success "Metrics endpoint responding"

        # Check for VM metrics (may be empty if no VMs are running yet)
        VM_COUNT=$(curl -sf "http://localhost:${PORT}/metrics" \
            | grep -c "libvirt_domain_info" || true)
        if [[ "$VM_COUNT" -gt 0 ]]; then
            success "Detected $VM_COUNT VM metric lines"
            curl -sf "http://localhost:${PORT}/metrics" \
                | grep "libvirt_domain_info_virtual_cpus" \
                | awk -F'"' '{print "  domain=" $2}' | head -10
        else
            warn "No VM metrics yet — normal if no VMs are running on this worker"
        fi
    else
        error "Endpoint not responding. Check: journalctl -u prometheus-libvirt-exporter -n 50"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Installation complete           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
log "Service   : prometheus-libvirt-exporter"
log "Version   : ${VERSION}"
log "Port      : ${PORT}"
log "Type      : ${WORKER_TYPE}"
echo ""
log "Useful commands:"
echo "  systemctl status prometheus-libvirt-exporter"
echo "  journalctl -u prometheus-libvirt-exporter -f"
echo "  curl http://localhost:${PORT}/metrics | grep libvirt_domain_info"
echo ""
warn "Remember: set up autossh tunnel from server1 to expose this port:"
echo "  Edit WORKERS section in setup_tunnels.sh on server1"
echo ""

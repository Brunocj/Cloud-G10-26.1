#!/bin/bash
# ---------------------------------------------------------------------------
# Gateway del pool de acceso externo de la zona OpenStack (10.60.16.0/24).
#
# La red externa de Neutron ('external') es FLAT sobre physnet0, que en el nodo
# de red mapea al bridge OVS br-provider. El único dispositivo en ese segmento
# es este nodo, así que el gateway que declara la subnet (10.60.16.1) tiene que
# vivir aquí: si nadie responde ARP por esa IP, el router de cada slice devuelve
# ICMP Host Unreachable a sus VMs y NO hay salida a Internet — aunque el router
# exista y enable_snat sea true.
#
# Es el equivalente OpenStack de lo que el Linux Cluster hace en cada worker
# con MASQUERADE sobre ens3 (ver NetworkOrchestrator/app/services/network_executor.py).
#
# Idempotente: se puede reejecutar sin duplicar reglas ni direcciones.
# ---------------------------------------------------------------------------
set -euo pipefail

BR="${BR:-br-provider}"              # bridge OVS al que mapea physnet0
GW_IP="${GW_IP:-10.60.16.1/24}"      # = gateway_ip de external_subnet en Neutron
POOL="${POOL:-10.60.16.0/24}"        # pool de acceso externo asignado

# Interfaz con salida real a Internet. Se autodetecta para no hardcodear un
# nombre que cambie entre nodos; se puede forzar con WAN=... al invocar.
WAN="${WAN:-$(ip route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')}"

if [ -z "$WAN" ]; then
    echo "ERROR: no se pudo determinar la interfaz WAN. Invocar con WAN=<iface>." >&2
    exit 1
fi

if ! ip link show "$BR" >/dev/null 2>&1; then
    echo "ERROR: el bridge '$BR' no existe. Verificar bridge_mappings de physnet0:" >&2
    echo "       grep -r bridge_mappings /etc/neutron/plugins/ml2/" >&2
    exit 1
fi

echo "[ext-gw] bridge=$BR gateway=$GW_IP pool=$POOL wan=$WAN"

# 'replace' es idempotente: crea o actualiza, nunca falla por duplicado
ip addr replace "$GW_IP" dev "$BR"
ip link set "$BR" up

sysctl -qw net.ipv4.ip_forward=1

# -C comprueba si la regla ya existe; solo se añade cuando falta
add_rule() {
    local table=$1; shift
    if ! iptables -t "$table" -C "$@" 2>/dev/null; then
        iptables -t "$table" -I "$@"
        echo "[ext-gw] regla añadida: -t $table $*"
    fi
}

add_rule nat  POSTROUTING -s "$POOL" -o "$WAN" -j MASQUERADE
add_rule filter FORWARD -s "$POOL" -j ACCEPT
add_rule filter FORWARD -d "$POOL" -m state --state ESTABLISHED,RELATED -j ACCEPT

echo "[ext-gw] listo. Verificar con:"
echo "  ip netns exec qrouter-<ID> ping -c2 10.60.16.1"
echo "  ip netns exec qrouter-<ID> ping -c2 8.8.8.8"

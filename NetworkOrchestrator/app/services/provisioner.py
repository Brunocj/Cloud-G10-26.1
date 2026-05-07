"""
Servicio principal del Network Orchestrator.
Orquesta la configuración de red iterando sobre los enlaces lógicos de la topología.
"""

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

from app.core.config import settings
from app.models.schemas import (
    DeployNetworkRequest, DeployNetworkResponse, DestroyNetworkRequest, DestroyNetworkResponse,
    LinkResult, ProvisioningStatus, NetworkLink
)
from app.services.network_executor import NetworkExecutor
from app.services.ssh_client import SSHClient

logger = logging.getLogger(__name__)

class NetworkProvisioner:

    # ── DEPLOY (Completamente Real) ──────────────────────────────────────────

    def deploy(self, request: DeployNetworkRequest) -> DeployNetworkResponse:
        logger.info(f"[slice={request.slice_id}] Iniciando despliegue de red ({len(request.links)} enlaces)")

        # 1. Agrupar Enlaces
        endpoints_by_worker = defaultdict(list)
        for link in request.links:
            endpoints_by_worker[link.vm1_worker_ip].append({
                "link_id": link.connection_id, "vlan": link.vlan_id, 
                "tap": link.vm1_tap, "rules": link.vm1_security_rules,
                "user": link.vm1_ssh_user, "key": link.vm1_ssh_private_key
            })
            endpoints_by_worker[link.vm2_worker_ip].append({
                "link_id": link.connection_id, "vlan": link.vlan_id, 
                "tap": link.vm2_tap, "rules": link.vm2_security_rules,
                "user": link.vm2_ssh_user, "key": link.vm2_ssh_private_key
            })

        # 2. Agrupar VMs 
        vms_by_worker = defaultdict(list)
        for vm in request.vms:
            vms_by_worker[vm.worker_ip].append(vm)

        successful_links = set()
        failed_links_errors = {}

        # 3. Lanzar Hilos
        with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_WORKERS) as pool:
            futures = {}

            # 🔥 Convertimos las llaves a lista para elegir al primer worker como LÍDER del Gateway
            worker_ips = list(endpoints_by_worker.keys())
            if not worker_ips and vms_by_worker:
                 worker_ips = list(vms_by_worker.keys()) # Por si hay VMs pero 0 enlaces

            for worker_ip in worker_ips:
                endpoints = endpoints_by_worker.get(worker_ip, [])
                vms_list = vms_by_worker.get(worker_ip, [])
                
                # 🔥 Solo el primer worker será el encargado del Gateway y el NAT
                is_gateway_leader = (worker_ip == worker_ips[0]) 

                futures[pool.submit(self._deploy_on_worker, worker_ip, endpoints, vms_list, request.slice_id, is_gateway_leader)] = worker_ip

            for future in as_completed(futures):
                worker_ip = futures[future]
                try:
                    ok_endpoints, fail_endpoints = future.result()
                    for ep in ok_endpoints:
                        successful_links.add(ep["link_id"])
                    for ep, err in fail_endpoints:
                        failed_links_errors[ep["link_id"]] = err
                except Exception as exc:
                    logger.error(f"[worker={worker_ip}] Error crítico: {exc}")

        links_ok, links_failed = [], []
        for link in request.links:
            if link.connection_id in failed_links_errors:
                links_failed.append(LinkResult(connection_id=link.connection_id, error=failed_links_errors[link.connection_id]))
            else:
                links_ok.append(LinkResult(connection_id=link.connection_id))

        status = ProvisioningStatus.SUCCESS if not links_failed else ProvisioningStatus.PARTIAL
        if not links_ok: status = ProvisioningStatus.ERROR

        return DeployNetworkResponse(
            slice_id=request.slice_id, request_id=request.request_id,
            status=status, links_ok=links_ok, links_failed=links_failed
        )

    def _deploy_on_worker(self, worker_ip: str, endpoints: List[dict], vms_list: list, slice_id: str, is_gateway_leader: bool) -> Tuple[List[dict], List[Tuple[dict, str]]]:
        ok_eps, fail_eps = [], []
        executor = NetworkExecutor(worker_ip)
        
        try:
            # Extraemos credenciales (asumimos que si hay VMs pero no enlaces, sacamos credenciales de la primera VM)
            user = endpoints[0]["user"] if endpoints else vms_list[0].ssh_user
            key = endpoints[0]["key"] if endpoints else vms_list[0].ssh_private_key

            with SSHClient(worker_ip, user, key) as ssh:
                # A. Configurar Enlaces L2 (VLANs)
                for ep in endpoints:
                    try:
                        executor.configure_vlan_and_port(ssh, ep["tap"], ep["vlan"])
                        executor.apply_security_groups(ssh, ep["tap"], ep["rules"])
                        ok_eps.append(ep)
                    except Exception as exc:
                        logger.error(f"Fallo en tap {ep['tap']}: {exc}")
                        fail_eps.append((ep, str(exc)))

                # 🔥 NUEVO: Enchufar el eth0 (tap de gestión) de todas las VMs al Gateway
                mgmt_vlan = 1000 + int(slice_id) 
                for vm in vms_list:
                    if vm.tap_interfaces:
                        mgmt_tap = vm.tap_interfaces[0].tap_name
                        try:
                            # Conecta el eth0 a la VLAN de gestión
                            executor.configure_vlan_and_port(ssh, mgmt_tap, mgmt_vlan)
                        except Exception as exc:
                            logger.error(f"Fallo al conectar TAP de gestión {mgmt_tap}: {exc}")

                # 🔥 B. Configurar Ruteo, NAT y Gateway SOLO en el Nodo Líder
                if is_gateway_leader and vms_list:
                    executor.configure_gateway_and_nat(ssh, slice_id, vms_list, mgmt_vlan)

        except Exception as exc:
            for ep in endpoints: fail_eps.append((ep, f"SSH Fail: {exc}"))

        return ok_eps, fail_eps


    # ── DESTROY (Completamente Real y sin Zombies) ─────────────────────────────────────────

    def destroy(self, request: DestroyNetworkRequest) -> DestroyNetworkResponse:
        logger.info(f"[slice={request.slice_id}] Iniciando destrucción REAL de red...")

        endpoints_by_worker = defaultdict(list)
        # 1. Agrupamos los enlaces a destruir (si existen)
        if hasattr(request, 'links') and request.links:
            for link in request.links:
                endpoints_by_worker[link.vm1_worker_ip].append({"tap": link.vm1_tap, "user": link.vm1_ssh_user, "key": link.vm1_ssh_private_key})
                endpoints_by_worker[link.vm2_worker_ip].append({"tap": link.vm2_tap, "user": link.vm2_ssh_user, "key": link.vm2_ssh_private_key})

        vms_by_worker = defaultdict(list)
        # 2. Agrupamos las VMs (si existen) para saber quién era el Gateway Líder
        if hasattr(request, 'vms') and request.vms:
            for vm in request.vms:
                vms_by_worker[vm.worker_ip].append(vm)

        # Si no hay ni VMs ni Links, no hay nada que hacer en la red
        if not endpoints_by_worker and not vms_by_worker:
            logger.warning(f"[slice={request.slice_id}] Sin links ni vms en el JSON. Nada que destruir en red.")
            return DestroyNetworkResponse(slice_id=request.slice_id, request_id=request.request_id, status=ProvisioningStatus.SUCCESS)

        # 🔥 Calculamos los workers involucrados
        worker_ips = list(endpoints_by_worker.keys())
        if not worker_ips and vms_by_worker:
            worker_ips = list(vms_by_worker.keys())

        with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_WORKERS) as pool:
            futures = {}
            for worker_ip in worker_ips:
                endpoints = endpoints_by_worker.get(worker_ip, [])
                vms_list = vms_by_worker.get(worker_ip, [])
                
                # 🔥 Identificamos al LÍDER (el mismo algoritmo que en el Deploy)
                is_gateway_leader = (worker_ip == worker_ips[0])

                futures[pool.submit(self._destroy_on_worker, worker_ip, endpoints, vms_list, request.slice_id, is_gateway_leader)] = worker_ip
                
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"[worker={futures[future]}] Error en limpieza de worker: {exc}")

        logger.info(f"[slice={request.slice_id}] Destrucción física completada.")
        return DestroyNetworkResponse(slice_id=request.slice_id, request_id=request.request_id, status=ProvisioningStatus.SUCCESS)

    def _destroy_on_worker(self, worker_ip: str, endpoints: List[dict], vms_list: list, slice_id: str, is_gateway_leader: bool) -> None:
        executor = NetworkExecutor(worker_ip)
        try:
            # Sacamos credenciales de los endpoints o de las VMs como fallback
            user = endpoints[0]["user"] if endpoints else vms_list[0].ssh_user
            key = endpoints[0]["key"] if endpoints else vms_list[0].ssh_private_key

            with SSHClient(worker_ip, user, key) as ssh:
                # 1. Limpiar los puertos TAPs de Capa 2
                for ep in endpoints:
                    try:
                        executor.destroy_port(ssh, ep["tap"]) 
                    except Exception as exc:
                        logger.error(f"Fallo al borrar tap {ep['tap']}: {exc}")
                
                # 🔥 NUEVO: Limpiar los TAPs de Gestión
                for vm in vms_list:
                    if vm.tap_interfaces:
                        mgmt_tap = vm.tap_interfaces[0].tap_name
                        try:
                            executor.destroy_port(ssh, mgmt_tap)
                        except Exception as exc:
                            logger.error(f"Fallo al borrar tap de gestión {mgmt_tap}: {exc}")
                        
                # 🔥 2. Limpiar el Gateway, DHCP y NAT (Solo si es el líder)
                if is_gateway_leader and vms_list:
                    try:
                        executor.destroy_gateway(ssh, slice_id, vms_list)
                    except Exception as exc:
                        logger.error(f"Fallo al borrar Gateway/NAT en líder {worker_ip}: {exc}")
                        
        except Exception as exc:
            logger.error(f"SSH Fail en worker {worker_ip} durante destroy: {exc}")
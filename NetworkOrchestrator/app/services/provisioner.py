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

        # 1. Agrupar por (worker_ip, worker_port) — clave compuesta porque varios
        #    workers pueden compartir el mismo gateway IP con distintos puertos SSH
        endpoints_by_worker = defaultdict(list)
        for link in request.links:
            key1 = (link.vm1_worker_ip, link.vm1_worker_port)
            endpoints_by_worker[key1].append({
                "link_id": link.connection_id, "vlan": link.vlan_id, 
                "tap": link.vm1_tap, "rules": link.vm1_security_rules,
                "user": link.vm1_ssh_user, "key": link.vm1_ssh_private_key,
                "port": link.vm1_worker_port,
            })
            key2 = (link.vm2_worker_ip, link.vm2_worker_port)
            endpoints_by_worker[key2].append({
                "link_id": link.connection_id, "vlan": link.vlan_id, 
                "tap": link.vm2_tap, "rules": link.vm2_security_rules,
                "user": link.vm2_ssh_user, "key": link.vm2_ssh_private_key,
                "port": link.vm2_worker_port,
            })

        # 2. Agrupar VMs por (worker_ip, worker_port)
        #    Modo Edición: las VMs ya desplegadas NO se reprocesan (su gateway,
        #    TAP de gestión y NAT ya existen). Los TAPs de sus enlaces NUEVOS se
        #    crean vía `endpoints` (request.links), de donde QMP los engancha.
        vms_by_worker = defaultdict(list)
        for vm in request.vms:
            if getattr(vm, 'already_deployed', False):
                continue
            key = (vm.worker_ip, getattr(vm, 'worker_port', 22))
            vms_by_worker[key].append(vm)

        successful_links = set()
        failed_links_errors = {}

        # 3. Lanzar Hilos
        with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_WORKERS) as pool:
            futures = {}

            worker_keys = list(endpoints_by_worker.keys())
            if not worker_keys and vms_by_worker:
                worker_keys = list(vms_by_worker.keys())

            for worker_key in worker_keys:
                worker_ip, worker_port = worker_key
                endpoints = endpoints_by_worker.get(worker_key, [])
                vms_list = vms_by_worker.get(worker_key, [])
                
                is_gateway_leader = (worker_key == worker_keys[0]) 

                futures[pool.submit(self._deploy_on_worker, worker_ip, worker_port, endpoints, vms_list, request.slice_id, is_gateway_leader)] = worker_key

            for future in as_completed(futures):
                worker_key = futures[future]
                worker_ip = worker_key[0] if isinstance(worker_key, tuple) else worker_key
                try:
                    ok_endpoints, fail_endpoints = future.result()
                    for ep in ok_endpoints:
                        successful_links.add(ep["link_id"])
                    for ep, err in fail_endpoints:
                        logger.error(f"[worker={worker_ip}] Fallo en endpoint TAP={ep.get('tap', 'N/A')}: {err}")
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

    def _deploy_on_worker(self, worker_ip: str, worker_port: int, endpoints: List[dict], vms_list: list, slice_id: str, is_gateway_leader: bool) -> Tuple[List[dict], List[Tuple[dict, str]]]:
        ok_eps, fail_eps = [], []
        executor = NetworkExecutor(worker_ip)
        
        try:
            user = endpoints[0]["user"] if endpoints else vms_list[0].ssh_user
            key = endpoints[0]["key"] if endpoints else vms_list[0].ssh_private_key
            port = endpoints[0].get("port", worker_port) if endpoints else getattr(vms_list[0], 'worker_port', worker_port)

            with SSHClient(worker_ip, user, key, port=port) as ssh:
                mgmt_vlan = 1000 + int(slice_id)

                # A. Batch: configurar TODOS los TAPs de este worker en 2 SSH calls
                #    (TAPs de enlace L2 + TAPs de gestión de VMs en una sola pasada)
                tap_vlan_pairs: list[tuple[str, int]] = []
                for ep in endpoints:
                    tap_vlan_pairs.append((ep["tap"], ep["vlan"]))
                for vm in vms_list:
                    if vm.tap_interfaces:
                        tap_vlan_pairs.append((vm.tap_interfaces[0].tap_name, mgmt_vlan))

                try:
                    executor.configure_taps_batch(ssh, tap_vlan_pairs)
                    # Marcar todos los endpoints L2 como OK
                    for ep in endpoints:
                        executor.apply_security_groups(ssh, ep["tap"], ep["rules"])
                        ok_eps.append(ep)
                except Exception as exc:
                    logger.error(f"Fallo batch TAPs en worker {worker_ip}: {exc}")
                    for ep in endpoints:
                        fail_eps.append((ep, str(exc)))

                # B. Configurar Gateway, DHCP y NAT
                if vms_list:
                    executor.configure_gateway_and_nat(ssh, slice_id, vms_list, mgmt_vlan)

        except Exception as exc:
            for ep in endpoints: fail_eps.append((ep, f"SSH Fail: {exc}"))

        return ok_eps, fail_eps


    # ── SHRINK: limpieza quirúrgica de TAPs (sin tocar el gateway) ─────────────

    def _destroy_shrink(self, request: DestroyNetworkRequest) -> DestroyNetworkResponse:
        logger.info(f"[slice={request.slice_id}] SHRINK de red: limpiando TAPs de lo eliminado...")

        # TAPs a borrar, agrupados por worker: (ip, port, user, key) → [tap,...]
        taps_by_worker = defaultdict(lambda: {"creds": None, "taps": set()})

        # TAPs de gestión de las VMs eliminadas
        for vm in (request.vms or []):
            key = (vm.worker_ip, getattr(vm, "worker_port", 22))
            entry = taps_by_worker[key]
            entry["creds"] = (vm.ssh_user, vm.ssh_private_key, getattr(vm, "worker_port", 22))
            for t in (vm.tap_interfaces or []):
                tap = getattr(t, "tap_name", None) or (t.get("tap_name") if isinstance(t, dict) else None)
                if tap:
                    entry["taps"].add(tap)

        # TAPs de las VMs eliminadas en los enlaces borrados (la sobreviviente la
        # limpia el CP vía QMP, así que aquí solo agregamos la del lado eliminado)
        removed_ips = {vm.worker_ip for vm in (request.vms or [])}
        for link in (request.links or []):
            for ip, port, user, key, tap in (
                (link.vm1_worker_ip, link.vm1_worker_port, link.vm1_ssh_user, link.vm1_ssh_private_key, link.vm1_tap),
                (link.vm2_worker_ip, link.vm2_worker_port, link.vm2_ssh_user, link.vm2_ssh_private_key, link.vm2_tap),
            ):
                k = (ip, port)
                entry = taps_by_worker[k]
                if entry["creds"] is None:
                    entry["creds"] = (user, key, port)
                entry["taps"].add(tap)

        for (worker_ip, worker_port), entry in taps_by_worker.items():
            user, key, port = entry["creds"]
            try:
                with SSHClient(worker_ip, user, key, port=port or 22) as ssh:
                    for tap in entry["taps"]:
                        ssh.exec(f"sudo ovs-vsctl --if-exists del-port br-int {tap}")
                        ssh.exec(f"sudo ip link del {tap} 2>/dev/null || true")
                        logger.info(f"[SHRINK][{worker_ip}] TAP {tap} eliminado")
            except Exception as exc:
                logger.warning(f"[SHRINK][{worker_ip}] Error limpiando TAPs: {exc}")

        return DestroyNetworkResponse(slice_id=request.slice_id, request_id=request.request_id,
                                      status=ProvisioningStatus.SUCCESS)

    # ── DESTROY (Completamente Real y sin Zombies) ─────────────────────────────────────────

    def destroy(self, request: DestroyNetworkRequest) -> DestroyNetworkResponse:
        # ── SHRINK (Modo Edición): borrar SOLO los TAPs de lo eliminado, sin
        # tocar el gateway/DHCP/NAT (compartido por slice+worker con las VMs
        # sobrevivientes). Las NICs de las sobrevivientes las quita el CP (QMP). ──
        if getattr(request, "mode", "full") == "shrink":
            return self._destroy_shrink(request)

        logger.info(f"[slice={request.slice_id}] Iniciando destrucción REAL de red...")

        endpoints_by_worker = defaultdict(list)
        if hasattr(request, 'links') and request.links:
            for link in request.links:
                key1 = (link.vm1_worker_ip, link.vm1_worker_port)
                endpoints_by_worker[key1].append({"tap": link.vm1_tap, "user": link.vm1_ssh_user, "key": link.vm1_ssh_private_key, "port": link.vm1_worker_port})
                key2 = (link.vm2_worker_ip, link.vm2_worker_port)
                endpoints_by_worker[key2].append({"tap": link.vm2_tap, "user": link.vm2_ssh_user, "key": link.vm2_ssh_private_key, "port": link.vm2_worker_port})

        vms_by_worker = defaultdict(list)
        if hasattr(request, 'vms') and request.vms:
            for vm in request.vms:
                key = (vm.worker_ip, getattr(vm, 'worker_port', 22))
                vms_by_worker[key].append(vm)

        worker_keys = list(set(list(endpoints_by_worker.keys()) + list(vms_by_worker.keys())))

        if not worker_keys:
            logger.warning(f"[slice={request.slice_id}] Sin links ni vms en el JSON. Nada que destruir en red.")
            return DestroyNetworkResponse(slice_id=request.slice_id, request_id=request.request_id, status=ProvisioningStatus.SUCCESS)

        with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_WORKERS) as pool:
            futures = {}
            for worker_key in worker_keys:
                worker_ip, worker_port = worker_key
                endpoints = endpoints_by_worker.get(worker_key, [])
                vms_list = vms_by_worker.get(worker_key, [])
                
                is_gateway_leader = (worker_key == worker_keys[0])

                futures[pool.submit(self._destroy_on_worker, worker_ip, worker_port, endpoints, vms_list, request.slice_id, is_gateway_leader)] = worker_key
                
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"[worker={futures[future]}] Error en limpieza de worker: {exc}")

        logger.info(f"[slice={request.slice_id}] Destrucción física completada.")
        return DestroyNetworkResponse(slice_id=request.slice_id, request_id=request.request_id, status=ProvisioningStatus.SUCCESS)

    def _destroy_on_worker(self, worker_ip: str, worker_port: int, endpoints: List[dict], vms_list: list, slice_id: str, is_gateway_leader: bool) -> None:
        executor = NetworkExecutor(worker_ip)
        try:
            user = endpoints[0]["user"] if endpoints else vms_list[0].ssh_user
            key = endpoints[0]["key"] if endpoints else vms_list[0].ssh_private_key
            port = endpoints[0].get("port", worker_port) if endpoints else getattr(vms_list[0], 'worker_port', worker_port)

            with SSHClient(worker_ip, user, key, port=port) as ssh:
                # 1. Limpiar los puertos TAPs L2 (Si hay)
                for ep in endpoints:
                    try:
                        executor.destroy_port(ssh, ep["tap"]) 
                    except Exception as exc:
                        logger.error(f"Fallo al borrar tap {ep['tap']}: {exc}")
                
                # 2. Limpiar TAPs de Gestión L3
                for vm in vms_list:
                    if getattr(vm, 'tap_interfaces', None):
                        mgmt_tap = vm.tap_interfaces[0].tap_name
                        try:
                            executor.destroy_port(ssh, mgmt_tap)
                        except Exception as exc:
                            logger.error(f"Fallo al borrar tap de gestión {mgmt_tap}: {exc}")
                        
                # 🔥 3. Limpiar Gateway, DHCP y NAT
                if vms_list or endpoints:
                    try:
                        executor.destroy_gateway(ssh, slice_id, vms_list)
                    except Exception as exc:
                        logger.error(f"Fallo al borrar Gateway/NAT en worker {worker_ip}: {exc}")
                        
        except Exception as exc:
            logger.error(f"SSH Fail en worker {worker_ip} durante destroy: {exc}")
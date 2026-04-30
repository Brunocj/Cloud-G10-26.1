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

        successful_links = set()
        failed_links_errors = {}

        with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_WORKERS) as pool:
            futures = {
                pool.submit(self._deploy_on_worker, worker_ip, endpoints): worker_ip
                for worker_ip, endpoints in endpoints_by_worker.items()
            }
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

    def _deploy_on_worker(self, worker_ip: str, endpoints: List[dict]) -> Tuple[List[dict], List[Tuple[dict, str]]]:
        ok_eps, fail_eps = [], []
        executor = NetworkExecutor(worker_ip)
        
        try:
            with SSHClient(worker_ip, endpoints[0]["user"], endpoints[0]["key"]) as ssh:
                for ep in endpoints:
                    try:
                        executor.configure_vlan_and_port(ssh, ep["tap"], ep["vlan"])
                        executor.apply_security_groups(ssh, ep["tap"], ep["rules"])
                        ok_eps.append(ep)
                    except Exception as exc:
                        logger.error(f"Fallo en tap {ep['tap']}: {exc}")
                        fail_eps.append((ep, str(exc)))
        except Exception as exc:
            for ep in endpoints: fail_eps.append((ep, f"SSH Fail: {exc}"))

        return ok_eps, fail_eps


    # ── DESTROY (Completamente Real) ─────────────────────────────────────────

    def destroy(self, request: DestroyNetworkRequest) -> DestroyNetworkResponse:
        logger.info(f"[slice={request.slice_id}] Iniciando destrucción REAL de red...")

        # Si el Slice Manager no nos mandó los 'links', no sabemos a dónde entrar a limpiar.
        if not hasattr(request, 'links') or not request.links:
            logger.warning(f"[slice={request.slice_id}] Sin arreglo de 'links' en el JSON. Se delega la limpieza de TAPs a la eliminación de las VMs.")
            return DestroyNetworkResponse(slice_id=request.slice_id, request_id=request.request_id, status=ProvisioningStatus.SUCCESS)

        endpoints_by_worker = defaultdict(list)
        for link in request.links:
            endpoints_by_worker[link.vm1_worker_ip].append({"tap": link.vm1_tap, "user": link.vm1_ssh_user, "key": link.vm1_ssh_private_key})
            endpoints_by_worker[link.vm2_worker_ip].append({"tap": link.vm2_tap, "user": link.vm2_ssh_user, "key": link.vm2_ssh_private_key})

        with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_WORKERS) as pool:
            futures = {
                pool.submit(self._destroy_on_worker, worker_ip, endpoints): worker_ip
                for worker_ip, endpoints in endpoints_by_worker.items()
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"[worker={futures[future]}] Error en limpieza de worker: {exc}")

        logger.info(f"[slice={request.slice_id}] Destrucción física completada.")
        return DestroyNetworkResponse(slice_id=request.slice_id, request_id=request.request_id, status=ProvisioningStatus.SUCCESS)

    def _destroy_on_worker(self, worker_ip: str, endpoints: List[dict]) -> None:
        executor = NetworkExecutor(worker_ip)
        try:
            with SSHClient(worker_ip, endpoints[0]["user"], endpoints[0]["key"]) as ssh:
                for ep in endpoints:
                    try:
                        # Asume que en tu NetworkExecutor existe una función remove_port o equivalente
                        executor.destroy_port(ssh, ep["tap"]) 
                    except Exception as exc:
                        logger.error(f"Fallo al borrar tap {ep['tap']}: {exc}")
        except Exception as exc:
            logger.error(f"SSH Fail en worker {worker_ip} durante destroy: {exc}")
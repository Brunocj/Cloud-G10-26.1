from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Image, Slice, Vm, Vlan, IpPool, Worker, AvailabilityZone
from app.routers.slice_router import _validate_topology
from app.schemas import DeployRequest, BulkDeployRequest, DraftSaveRequest
from app.auth import CurrentUser, get_current_user
from app.services.placement_worker import placement_queue
from app.nats_producer import nats_producer
from app.routers.project_router import can_deploy_directly, user_can_choose_project
from app.services.notification_hub import notification_hub
from app.services.slice_destroyer import destroy_deployed_slice
from app.services.permissions import can_operate_slice
from app.services.audit import audit
import os
import uuid
import json
import logging
from datetime import datetime

router = APIRouter(prefix="/api/v1/slices", tags=["Deploy"])
logger = logging.getLogger("SliceManager.Deploy")


def _read_ssh_key(key_path: str) -> str:
    """Lee la clave SSH desde el path de BD, con fallback al path del contenedor."""
    if not key_path:
        logger.warning("[SSH_KEY] key_path vacío")
        return ""
    candidates = [key_path, f"/app/keys/{os.path.basename(key_path)}"]
    for path in candidates:
        exists = os.path.exists(path)
        logger.info("[SSH_KEY] probando '%s' → existe=%s", path, exists)
        if exists:
            try:
                content = open(path).read()
                logger.info("[SSH_KEY] leída OK len=%d", len(content))
                return content
            except Exception as exc:
                logger.error("[SSH_KEY] error leyendo '%s': %s", path, exc)
    logger.error("[SSH_KEY] clave no encontrada en ninguno de: %s", candidates)
    return ""


def _enrich_links_with_ssh_keys(links: list, workers_by_ip: dict) -> list:
    """Re-inyecta la clave SSH en deployed_links desde BD."""
    any_worker = next(iter(workers_by_ip.values()), None)
    if not any_worker:
        logger.error("[ENRICH] workers_by_ip vacío — no se puede enriquecer links")
        return links
    logger.info("[ENRICH] usando worker ip=%s ssh_key_path=%s", any_worker.ip, any_worker.ssh_key_path)
    fresh_key = _read_ssh_key(any_worker.ssh_key_path)
    if not fresh_key:
        logger.error("[ENRICH] clave vacía — links quedan sin ssh_private_key")
        return links
    for link in links:
        for side in ("vm1", "vm2"):
            link[f"{side}_ssh_private_key"] = fresh_key
    logger.info("[ENRICH] %d links enriquecidos con SSH key", len(links))
    return links


def _enrich_vms_with_ssh_keys(vms: list, workers_by_ip: dict) -> list:
    """Re-inyecta la clave SSH en deployed_vms desde BD."""
    any_worker = next(iter(workers_by_ip.values()), None)
    if not any_worker:
        logger.error("[ENRICH] workers_by_ip vacío — no se puede enriquecer vms")
        return vms
    logger.info("[ENRICH] usando worker ip=%s ssh_key_path=%s", any_worker.ip, any_worker.ssh_key_path)
    fresh_key = _read_ssh_key(any_worker.ssh_key_path)
    if not fresh_key:
        logger.error("[ENRICH] clave vacía — vms quedan sin ssh_private_key")
        return vms
    for vm in vms:
        vm["ssh_private_key"] = fresh_key
    logger.info("[ENRICH] %d vms enriquecidas con SSH key", len(vms))
    return vms


def _release_external_ips(db: Session, slice_id: int) -> None:
    vms = db.query(Vm).filter(
        Vm.slice_id == slice_id,
        Vm.external_ip.isnot(None)
    ).all()
    if not vms:
        return

    ips = [vm.external_ip for vm in vms if vm.external_ip]
    if not ips:
        return

    ip_records = db.query(IpPool).filter(IpPool.ip_address.in_(ips)).all()
    for record in ip_records:
        record.is_used = 0
        record.vm_id = None

@router.post("/{slice_id}/deploy", status_code=202)
async def request_deploy(
    slice_id: int,
    request:  DeployRequest,
    db:       Session     = Depends(get_db),
    user:     CurrentUser = Depends(get_current_user),
):
    logger.info("="*70)
    logger.info("[DEPLOY] 📥 Solicitud de despliegue recibida para slice_id=%s", slice_id)
    logger.info("[DEPLOY]    zona=%s  TTL=%sh  project_id=%s  motivo=%s",
                request.availability_zone_id, request.ttl_hours,
                getattr(request, 'project_id', None), getattr(request, 'motivo', 'N/A'))
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrada")

    # ── Autorización de negocio (dueño / admin+ / jefe del proyecto) ─────────
    if not can_operate_slice(db, user, db_slice):
        raise HTTPException(
            status_code=403,
            detail="No tienes permiso para desplegar el slice de otro usuario.",
        )

    logger.info("[DEPLOY] ✅ Slice '%s' encontrado en BD (estado actual: %s)", db_slice.name, db_slice.status)

    # ── Validación del proyecto elegido ─────────────────────────────────────
    project_id = getattr(request, 'project_id', None)
    if project_id is not None:
        if not user_can_choose_project(db, user.user_id, user.role, project_id):
            raise HTTPException(
                status_code=403,
                detail="No puedes desplegar en ese proyecto (no eres miembro).",
            )
        db_slice.project_id = project_id
    else:
        db_slice.project_id = None

    # ── Validación estructural de la topología ──────────────────────────────
    # Reglas: sin VMs huérfanas; slices de 1 VM solo en Linux Cluster; edges
    # deben referenciar nodos existentes. Se ejecuta ANTES de tocar el bus.
    _sj = db_slice.slice_json or {}
    if isinstance(_sj, str):
        _sj = json.loads(_sj)
    # slice_json en DRAFT puede no traer "nodes" (el repo solo persiste edges).
    # Reconstruimos los nodos a partir de las VMs de la BD para validar.
    if not _sj.get("nodes"):
        _sj = dict(_sj)
        _sj["nodes"] = [{"id": v.name} for v in db.query(Vm).filter(Vm.slice_id == slice_id).all()]
    _validate_topology(db, _sj, availability_zone_id=request.availability_zone_id)

    # ── Validación Fail-Fast: compatibilidad de imágenes con la AZ ──────────
    # Principio: verificar ANTES de emitir cualquier evento al bus de mensajes.
    vms_del_slice = db.query(Vm).filter(Vm.slice_id == slice_id).all()
    for vm in vms_del_slice:
        if vm.image_id is None:
            continue  # VM sin imagen asignada: se valida en el worker
        img = db.query(Image).filter(Image.id == vm.image_id).first()
        if img is None:
            continue
        # Si la imagen tiene AZ asignada y NO coincide con la zona solicitada → ABORT
        if img.availability_zone_id is not None and img.availability_zone_id != request.availability_zone_id:
            logger.warning(
                "[DEPLOY] ❌ Imagen '%s' (id=%d, az_id=%s) no compatible con zona=%d. Slice abortado.",
                img.name, img.id, img.availability_zone_id, request.availability_zone_id,
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Error: La imagen seleccionada para el nodo '{vm.name}' no es compatible "
                    f"con la Zona de Disponibilidad elegida."
                ),
            )

    logger.info("[DEPLOY] ✅ Validación Fail-Fast superada: todas las imágenes son compatibles con la zona %d",
                request.availability_zone_id)

    # ── Decidir despliegue directo vs. solicitud pendiente ──────────────────
    is_direct = can_deploy_directly(db, user.user_id, user.role, project_id)

    db_slice.status = "PENDING_APPROVAL"
    db_slice.TTL = request.ttl_hours
    db_slice.availability_zone_id = request.availability_zone_id

    # Persistir los datos de la solicitud: la zona y el motivo se necesitan
    # después (bandeja de aprobación / encolado diferido al aprobar).
    s_json = db_slice.slice_json or {}
    if isinstance(s_json, str):
        s_json = json.loads(s_json)
    s_json["deploy_request"] = {
        "availability_zone_id": request.availability_zone_id,
        "ttl_hours":            request.ttl_hours,
        "motivo":               request.motivo,
        "requested_by":         user.user_id,
        "requested_at":         datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "needs_approval":       not is_direct,
    }
    s_json.pop("review", None)   # limpiar revisión previa si se re-solicita tras un rechazo
    db_slice.slice_json = dict(s_json)
    db.commit()

    audit(user.user_id, user.role, "SliceManager",
          "deploy_direct" if is_direct else "deploy_requested",
          f"Slice '{db_slice.name}' — zona={request.availability_zone_id} ttl={request.ttl_hours}h. Motivo: {request.motivo}",
          slice_id=slice_id, project_id=project_id)

    if is_direct:
        logger.info("[DEPLOY] 🟢 Despliegue directo — encolando en placement_queue")
        await placement_queue.put({"slice_id": slice_id, "zone_id": request.availability_zone_id})
        logger.info("[DEPLOY] 📤 Solicitud encolada en placement_queue → worker en background la procesará")
        logger.info("="*70)
        return {
            "status":  "ACCEPTED",
            "message": "Enviado a validación de recursos.",
            "direct":  True,
        }
    else:
        logger.info("[DEPLOY] 🟡 Requiere aprobación humana — NO se encola. Queda en PENDING_APPROVAL.")

        # Notificar en tiempo real a los aprobadores: admins/superAdmins
        # conectados + jefes del proyecto (si hay proyecto).
        event = {
            "type":     "new_request",
            "slice_id": slice_id,
            "title":    "Nueva solicitud de despliegue",
            "message":  f"El slice \"{db_slice.name}\" espera tu aprobación.",
        }
        await notification_hub.notify_roles(["admin", "superAdmin"], event)
        if project_id is not None:
            from app.models import Role, UserProject
            jefe_role = db.query(Role).filter(Role.role_name == "jefeProyecto").first()
            if jefe_role:
                jefes = db.query(UserProject).filter(
                    UserProject.project_id == project_id,
                    UserProject.project_role_id == jefe_role.id,
                ).all()
                await notification_hub.notify_users([j.user_id for j in jefes], event)

        logger.info("="*70)
        return {
            "status":  "PENDING_APPROVAL",
            "message": "Solicitud enviada. Requiere aprobación del jefe del proyecto o admin.",
            "direct":  False,
        }

@router.post("/bulk-deploy", status_code=202)
async def bulk_deploy(
    request: BulkDeployRequest,
    db:      Session     = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    """
    Despliegue masivo: clona la topología para cada miembro del proyecto.
    Solo jefeProyecto (del proyecto), admin o superAdmin.
    Cada slice se crea con creator_id = UUID del miembro y se encola directamente.
    """
    from app.models import UserProject, Role, Project

    logger.info("=" * 70)
    logger.info("[BULK] 📥 Solicitud de despliegue masivo: project_id=%s, az=%s, prefix='%s'",
                request.project_id, request.availability_zone_id, request.name_prefix)

    # ── Validar proyecto ────────────────────────────────────────────────────
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    # ── Autorización: admin/superAdmin o jefe del proyecto ──────────────────
    if not can_deploy_directly(db, user.user_id, user.role, request.project_id):
        raise HTTPException(
            status_code=403,
            detail="Solo admin, superAdmin o jefeProyecto del proyecto pueden hacer despliegue masivo.",
        )

    # ── Obtener miembros del proyecto ───────────────────────────────────────
    memberships = db.query(UserProject).filter(
        UserProject.project_id == request.project_id
    ).all()
    if not memberships:
        raise HTTPException(status_code=400, detail="El proyecto no tiene miembros")

    # Excluir al usuario que hace el deploy (el jefe ya puede crear su propio slice aparte)
    member_ids = [m.user_id for m in memberships if m.user_id != user.user_id]
    if not member_ids:
        raise HTTPException(status_code=400, detail="No hay otros miembros en el proyecto para desplegar")

    logger.info("[BULK] 👥 %d miembros encontrados (excluyendo al solicitante)", len(member_ids))

    # ── Validación estructural de la topología (fail-fast una sola vez) ─────
    _validate_topology(db, request.slice_json,
                       availability_zone_id=request.availability_zone_id)

    # ── Validar imágenes vs. zona (fail-fast una sola vez) ──────────────────
    nodes_template = request.slice_json.get("nodes", [])
    edges_template = request.slice_json.get("edges", [])
    for node in nodes_template:
        img_id = node.get("image_id")
        if img_id is None or int(img_id) < 0:
            continue
        img = db.query(Image).filter(Image.id == int(img_id)).first()
        if img and img.availability_zone_id is not None and img.availability_zone_id != request.availability_zone_id:
            raise HTTPException(
                status_code=400,
                detail=f"Imagen '{img.name}' no compatible con la zona seleccionada.",
            )

    # ── Workers disponibles ────────────────────────────────────────────────
    workers_db = db.query(Worker).all()
    num_workers = len(workers_db)

    results = []
    errors  = 0

    for member_id in member_ids:
        try:
            # 1. Crear draft con owner = miembro
            import copy
            nodes_copy = copy.deepcopy(nodes_template)
            edges_copy = copy.deepcopy(edges_template)

            nuevo_slice = Slice(
                name=f"{request.name_prefix}-{member_id[:6]}",
                status="DRAFT",
                creator_id=member_id,
                project_id=request.project_id,
                slice_json={"edges": edges_copy},
            )
            db.add(nuevo_slice)
            db.flush()

            # 2. Crear VMs del slice
            for index, vm_data in enumerate(nodes_copy):
                asignado = workers_db[index % num_workers] if num_workers > 0 else None

                img_id = vm_data.get("image_id")
                if img_id is not None and int(img_id) < 0:
                    img_id = None

                nueva_vm = Vm(
                    name=vm_data.get("id"),
                    vcore=int(vm_data.get("vcores", 1)),
                    ram=float(vm_data.get("ram", 512.0)),
                    disk=float(vm_data.get("disk", 5.0)),
                    state="DRAFT",
                    slice_id=nuevo_slice.id,
                    image_id=img_id,
                    worker_id=asignado.id if asignado else None,
                    external_ip=None,
                    internet_access=0,
                )
                vm_data["worker"]    = asignado.name if asignado else "Unassigned"
                vm_data["worker_id"] = asignado.id if asignado else None
                db.add(nueva_vm)
                db.flush()

            # 3. Guardar slice_json completo
            nuevo_slice.slice_json = {"nodes": nodes_copy, "edges": edges_copy}

            # 4. Marcar como PENDING_APPROVAL y encolar
            nuevo_slice.status = "PENDING_APPROVAL"
            nuevo_slice.TTL = request.ttl_hours
            nuevo_slice.availability_zone_id = request.availability_zone_id
            db.commit()

            await placement_queue.put({"slice_id": nuevo_slice.id, "zone_id": request.availability_zone_id})

            results.append({
                "member_id": member_id,
                "slice_id":  nuevo_slice.id,
                "status":    "QUEUED",
            })
            logger.info("[BULK] ✅ Slice %d creado y encolado para user=%s…", nuevo_slice.id, member_id[:8])

        except Exception as e:
            errors += 1
            results.append({
                "member_id": member_id,
                "slice_id":  None,
                "status":    "ERROR",
                "detail":    str(e),
            })
            logger.error("[BULK] ❌ Error creando slice para user=%s…: %s", member_id[:8], e)
            db.rollback()

    logger.info("[BULK] 📊 Resumen: %d creados, %d errores", len(member_ids) - errors, errors)
    logger.info("=" * 70)
    return {
        "message":      f"Despliegue masivo completado: {len(member_ids) - errors} slices creados, {errors} errores",
        "total":        len(member_ids),
        "success":      len(member_ids) - errors,
        "errors":       errors,
        "results":      results,
        "project_name": project.name,
    }

@router.delete("/{slice_id}", status_code=202)
async def request_destroy(
    slice_id: int,
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()

    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    if db_slice.status == "TERMINATED":
        raise HTTPException(status_code=400, detail="El slice ya está destruido.")
    if db_slice.status == "TERMINATING":
        raise HTTPException(status_code=409, detail="Ya hay una destrucción en curso para este slice.")

    # ── Autorización de negocio ──────────────────────────────────────
    # Dueño, admin/superAdmin, o jefeProyecto del proyecto del slice
    # (REQ-JP-07: el jefe puede apagar la red de un alumno de su curso).
    if not can_operate_slice(db, user, db_slice):
        raise HTTPException(
            status_code=403,
            detail="No tienes permiso para destruir el slice de otro usuario.",
        )

    # Si es un borrador o una solicitud rechazada, solo borramos de la BD
    if db_slice.status in ("DRAFT", "REJECTED"):
        _release_external_ips(db, slice_id)
        db.query(Vm).filter(Vm.slice_id == slice_id).delete()
        db.delete(db_slice)
        db.commit()
        logger.info("[DESTROY] Borrador slice_id=%s eliminado por user=%s…", slice_id, user.user_id[:8])
        return {"status": "DELETED", "message": "Borrador eliminado de la base de datos."}

    # Si es PENDING_APPROVAL sin encolar (esperando aprobación humana),
    # también se puede eliminar directamente ya que no hay recursos desplegados.
    if db_slice.status == "PENDING_APPROVAL":
        # Chequeamos si hay VMs con state distinto de DRAFT/PENDING (indicaría
        # que sí llegó al worker). Si no, es una solicitud pendiente y se
        # elimina como borrador.
        vms_deployed = db.query(Vm).filter(
            Vm.slice_id == slice_id,
            Vm.state.notin_(["DRAFT", "PENDING"]),
        ).count()
        if vms_deployed == 0:
            _release_external_ips(db, slice_id)
            db.query(Vm).filter(Vm.slice_id == slice_id).delete()
            db.delete(db_slice)
            db.commit()
            logger.info("[DESTROY] Solicitud pendiente slice_id=%s eliminada por user=%s…", slice_id, user.user_id[:8])
            return {"status": "DELETED", "message": "Solicitud pendiente eliminada."}

    # Slice desplegado: lógica compartida con el TTL scheduler
    published = await destroy_deployed_slice(db, db_slice)
    if published:
        logger.info("[DESTROY] slice_id=%s terminado por user=%s…", slice_id, user.user_id[:8])
        audit(user.user_id, user.role, "SliceManager", "slice_destroyed",
              f"Slice '{db_slice.name}' destruido manualmente.",
              slice_id=slice_id, project_id=db_slice.project_id)
        return {"status": "ACCEPTED", "message": "Orden de destrucción enviada."}

    raise HTTPException(status_code=500, detail="Error enviando orden a NATS")


# ── Modo Edición: ELIMINACIÓN incremental (shrink) ────────────────────────────

async def _shrink_slice(db, user, db_slice, zone_id, s_json,
                        removed_nodes, removed_edges, old_edges,
                        new_nodes_all, new_edges_all):
    """
    Destruye las VMs y enlaces eliminados de un slice ACTIVO sin tocar el resto:
      · VMs eliminadas → destroy de la instancia (Nova) / kill del proceso (QEMU).
      · Enlaces eliminados / incidentes → limpieza de su red + HOT-UNPLUG de la NIC
        en las VMs sobrevivientes (QMP device_del en Linux / detach en OpenStack).
    """
    from app.services.slice_destroyer import _read_ssh_key

    slice_id = db_slice.id
    deployed_vms   = s_json.get("deployed_vms", [])
    deployed_links = s_json.get("deployed_links", [])

    # Worker de la zona del slice con llave válida (no el primero de la tabla,
    # que podría ser de otra zona o sin ssh_key_path).
    key_worker = (
        db.query(Worker)
          .filter(Worker.availability_zones_id == zone_id, Worker.ssh_key_path.isnot(None))
          .first()
        or db.query(Worker).filter(Worker.ssh_key_path.isnot(None)).first()
    )
    fresh_key  = _read_ssh_key(key_worker.ssh_key_path) if key_worker else ""
    if not fresh_key:
        logger.error("[SHRINK] ⚠️ ssh_private_key vacía para slice %s (az=%s)", slice_id, zone_id)

    # Pares (from,to) de los enlaces eliminados explícitamente
    removed_edge_pairs = set()
    for e in old_edges:
        if e.get("id") in removed_edges:
            a = e.get("from", e.get("source")); b = e.get("to", e.get("target"))
            removed_edge_pairs.add(frozenset((a, b)))

    # Enlaces desplegados a limpiar + unplugs de sobrevivientes
    links_to_remove, unplugs, removed_link_ids = [], [], []
    for dl in deployed_links:
        pair = frozenset((dl.get("vm1_id"), dl.get("vm2_id")))
        touches_removed = bool(pair & removed_nodes)
        is_removed_edge = pair in removed_edge_pairs
        if not (touches_removed or is_removed_edge):
            continue
        dlc = dict(dl)
        if fresh_key:
            dlc["vm1_ssh_private_key"] = fresh_key
            dlc["vm2_ssh_private_key"] = fresh_key
        links_to_remove.append(dlc)
        removed_link_ids.append(dl.get("connection_id"))
        # Extremos sobrevivientes → hot-unplug de su NIC en este enlace
        for side in ("vm1", "vm2"):
            vid = dl.get(f"{side}_id")
            if vid in removed_nodes:
                continue
            dv = next((d for d in deployed_vms if d.get("vm_id") == vid), {})
            unplugs.append({
                "vm_id":                vid,
                "tap_name":             dl.get(f"{side}_tap"),
                "vlan_id":              dl.get("vlan_id"),
                "worker_ip":            dv.get("worker_ip"),
                "worker_port":          dv.get("worker_port", 22),
                "ssh_user":             dv.get("ssh_user"),
                "ssh_private_key":      fresh_key,
                "provider_instance_id": dv.get("provider_instance_id"),
            })

    # VMs a destruir (registros desplegados con su clave SSH)
    vms_to_destroy = []
    for dv in deployed_vms:
        if dv.get("vm_id") in removed_nodes:
            d = dict(dv)
            d["ssh_private_key"] = fresh_key
            vms_to_destroy.append(d)

    # Publicar la orden shrink (reusa slice.destroy con mode=shrink)
    import uuid as _uuid
    payload = {
        "slice_id":             str(slice_id),
        "request_id":           f"req-shrink-{_uuid.uuid4().hex[:8]}",
        "availability_zone_id": zone_id,
        "mode":                 "shrink",
        "vms":                  vms_to_destroy,
        "links":                links_to_remove,
        "unplugs":              unplugs,
    }

    s_json["pending_shrink"] = {
        "removed_vm_names": list(removed_nodes),
        "removed_edge_ids": list(removed_edges),
        "removed_link_ids": removed_link_ids,
        "new_nodes":        new_nodes_all,
        "new_edges":        new_edges_all,
        "external_ips":     [d.get("external_ip") for d in vms_to_destroy if d.get("external_ip")],
    }
    db_slice.slice_json = dict(s_json)
    db_slice.status = "PROVISIONING"
    db.commit()

    audit(user.user_id, user.role, "SliceManager", "slice_shrink_requested",
          f"Slice '{db_slice.name}': −{len(removed_nodes)} VM(s), −{len(removed_edges)} enlace(s).",
          slice_id=slice_id, project_id=db_slice.project_id)

    published = await nats_producer.publish_destroy(payload)
    if not published:
        # Revertir el estado si NATS no aceptó la orden
        s_json.pop("pending_shrink", None)
        db_slice.slice_json = dict(s_json)
        db_slice.status = "ACTIVE"
        db.commit()
        raise HTTPException(status_code=500, detail="Error enviando la orden de eliminación a NATS")

    logger.info("[MODIFY] ✂️  Shrink encolado slice=%s: −%d VMs, −%d enlaces",
                slice_id, len(vms_to_destroy), len(links_to_remove))
    return {
        "status": "ACCEPTED",
        "message": f"Eliminando {len(vms_to_destroy)} VM(s) y {len(links_to_remove)} enlace(s)…",
    }


# ── Modo Edición Post-Despliegue (REQ-US-14): aprovisionamiento incremental ───

@router.post("/{slice_id}/modify", status_code=202)
async def modify_active_slice(
    slice_id: int,
    request:  DraftSaveRequest,
    db:       Session     = Depends(get_db),
    user:     CurrentUser = Depends(get_current_user),
):
    """
    Extiende un slice ACTIVO: acepta la topología completa editada, calcula el
    diff (nodos y enlaces NUEVOS) y los aprovisiona incrementalmente sin tocar
    lo ya desplegado. Los enlaces hacia VMs en ejecución se conectan en caliente
    (QMP hot-plug en Linux Cluster / interface-attach de Nova en OpenStack).
    No se permiten eliminaciones de nodos/enlaces (fuera del alcance de R1B).
    """
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    if db_slice.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Solo se pueden modificar slices ACTIVOS.")
    if not can_operate_slice(db, user, db_slice):
        raise HTTPException(status_code=403, detail="No tienes permiso para modificar este slice.")

    s_json = db_slice.slice_json or {}
    if isinstance(s_json, str):
        s_json = json.loads(s_json)
    if s_json.get("pending_extension") or s_json.get("pending_shrink"):
        raise HTTPException(status_code=409, detail="Ya hay una modificación en curso para este slice.")

    old_nodes = {n.get("id"): n for n in s_json.get("nodes", [])}
    old_edges = s_json.get("edges", [])
    old_edge_ids = {e.get("id") for e in old_edges}

    new_nodes_all = request.slice_json.get("nodes", [])
    new_edges_all = request.slice_json.get("edges", [])
    incoming_node_ids = {n.get("id") for n in new_nodes_all}
    incoming_edge_ids = {e.get("id") for e in new_edges_all}

    removed_nodes = set(old_nodes) - incoming_node_ids
    removed_edges = old_edge_ids - incoming_edge_ids
    added_nodes = [n for n in new_nodes_all if n.get("id") not in old_nodes]
    added_edges = [e for e in new_edges_all if e.get("id") not in old_edge_ids]

    zone_id = db_slice.availability_zone_id or 1

    # No mezclar altas y bajas en una sola aplicación (dos sagas distintas)
    if (added_nodes or added_edges) and (removed_nodes or removed_edges):
        raise HTTPException(
            status_code=400,
            detail="Aplica los cambios por separado: primero agrega, luego elimina (o al revés).",
        )

    if not added_nodes and not added_edges and not removed_nodes and not removed_edges:
        raise HTTPException(status_code=400, detail="No hay cambios que aplicar.")

    # ── Validación estructural de la topología resultante ────────────────────
    # La topología final (post-modificación) no debe dejar VMs huérfanas ni
    # infringir la regla de 1-VM solo en Linux Cluster.
    _validate_topology(
        db,
        {"nodes": new_nodes_all, "edges": new_edges_all},
        availability_zone_id=zone_id,
    )

    # ── ELIMINACIÓN (shrink): destruir VMs/enlaces sin tocar el resto ────────
    if removed_nodes or removed_edges:
        return await _shrink_slice(
            db, user, db_slice, zone_id, s_json,
            removed_nodes, removed_edges, old_edges, new_nodes_all, new_edges_all,
        )

    # Todo enlace nuevo debe conectar nodos válidos
    for e in added_edges:
        a, b = e.get("from", e.get("source")), e.get("to", e.get("target"))
        if a not in incoming_node_ids or b not in incoming_node_ids:
            raise HTTPException(status_code=400, detail=f"El enlace {e.get('id')} referencia nodos inexistentes.")

    # ── Validar imágenes de los nodos nuevos vs. la AZ del slice ─────────────
    for n in added_nodes:
        img_id = n.get("image_id")
        if img_id is None or int(img_id) < 0:
            continue
        img = db.query(Image).filter(Image.id == int(img_id)).first()
        if img and img.availability_zone_id is not None and img.availability_zone_id != zone_id:
            raise HTTPException(
                status_code=400,
                detail=f"La imagen del nodo '{n.get('id')}' no es compatible con la zona del slice.",
            )

    # ── Crear filas Vm para los nodos nuevos (misma lógica que el draft) ─────
    workers_db  = db.query(Worker).filter(Worker.availability_zones_id == zone_id).all()
    num_workers = len(workers_db)
    new_vm_names = []
    for index, vm_data in enumerate(added_nodes):
        asignado = workers_db[index % num_workers] if num_workers > 0 else None
        ext_ip     = vm_data.get("external_ip", None)
        int_access = 1 if ext_ip else int(vm_data.get("internet_access", 0))
        img_id = vm_data.get("image_id")
        if img_id is not None and int(img_id) < 0:
            img_id = None

        nueva_vm = Vm(
            name=vm_data.get("id"),
            vcore=int(vm_data.get("vcores", 1)),
            ram=float(vm_data.get("ram", 512.0)),
            disk=float(vm_data.get("disk", 5.0)),
            state="DRAFT",
            slice_id=slice_id,
            image_id=img_id,
            worker_id=asignado.id if asignado else None,
            external_ip=ext_ip,
            internet_access=int_access,
        )
        vm_data["worker"]    = asignado.name if asignado else "Unassigned"
        vm_data["worker_id"] = asignado.id if asignado else None
        db.add(nueva_vm)
        db.flush()
        new_vm_names.append(vm_data.get("id"))

        if ext_ip and ext_ip != "random":   # "random" se resuelve en placement_worker
            ip_record = db.query(IpPool).filter(IpPool.ip_address == ext_ip).first()
            if not ip_record:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"La IP '{ext_ip}' no existe en el pool.")
            if ip_record.is_used and ip_record.vm_id != nueva_vm.id:
                db.rollback()
                raise HTTPException(status_code=409, detail=f"La IP '{ext_ip}' ya está en uso.")
            ip_record.is_used = 1
            ip_record.vm_id   = nueva_vm.id

    # ── Persistir la topología fusionada + marcar la extensión en curso ──────
    s_json["nodes"] = new_nodes_all
    s_json["edges"] = new_edges_all
    s_json["pending_extension"] = {
        "new_vm_names": new_vm_names,
        "new_edge_ids": [e.get("id") for e in added_edges],
        "requested_by": user.user_id,
        "requested_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }
    db_slice.slice_json = dict(s_json)
    db_slice.status = "PROVISIONING"
    db.commit()

    audit(user.user_id, user.role, "SliceManager", "slice_extend_requested",
          f"Slice '{db_slice.name}': +{len(added_nodes)} nodo(s), +{len(added_edges)} enlace(s) (incremental).",
          slice_id=slice_id, project_id=db_slice.project_id)

    await placement_queue.put({
        "slice_id": slice_id,
        "zone_id":  zone_id,
        "extend": {
            "new_vm_names": new_vm_names,
            "new_edge_ids": [e.get("id") for e in added_edges],
        },
    })
    logger.info("[MODIFY] 🧩 Extensión encolada slice=%s: +%d VMs, +%d enlaces",
                slice_id, len(added_nodes), len(added_edges))
    return {
        "status": "ACCEPTED",
        "message": f"Aplicando cambios: {len(added_nodes)} nodo(s) y {len(added_edges)} enlace(s) nuevos.",
        "new_vms": new_vm_names,
    }


# ── Kill Switch (REQ-AD-07): destrucción administrativa forzada ───────────────

from pydantic import BaseModel as _BaseModel

class ForceDestroyRequest(_BaseModel):
    reason: str


@router.post("/{slice_id}/force-destroy", status_code=202)
async def force_destroy(
    slice_id: int,
    request:  ForceDestroyRequest,
    db:       Session     = Depends(get_db),
    user:     CurrentUser = Depends(get_current_user),
):
    """
    Kill Switch: destrucción forzada de cualquier slice por un administrador
    (o jefeProyecto sobre slices de sus proyectos). El motivo es OBLIGATORIO,
    queda registrado en el slice y en los logs, y se notifica al dueño en
    tiempo real explicando por qué su laboratorio fue apagado.
    """
    if not request.reason or not request.reason.strip():
        raise HTTPException(status_code=400, detail="El motivo de la destrucción es obligatorio.")

    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    if db_slice.status == "TERMINATED":
        raise HTTPException(status_code=400, detail="El slice ya está destruido.")
    if db_slice.status == "TERMINATING":
        raise HTTPException(status_code=409, detail="Ya hay una destrucción en curso para este slice.")

    # Solo roles con poder de intervención sobre slices ajenos
    if user.role not in ("admin", "superAdmin"):
        if not (user.role == "jefeProyecto" and can_operate_slice(db, user, db_slice)):
            raise HTTPException(status_code=403, detail="Solo admin, superAdmin o el jefe del proyecto pueden forzar la destrucción.")

    reason = request.reason.strip()
    owner_id = db_slice.creator_id
    slice_name = db_slice.name

    # Registrar el motivo ANTES de destruir (trazabilidad)
    s_json = db_slice.slice_json or {}
    if isinstance(s_json, str):
        s_json = json.loads(s_json)
    s_json["kill_switch"] = {
        "reason":       reason,
        "destroyed_by": user.user_id,
        "destroyed_role": user.role,
        "destroyed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }
    db_slice.slice_json = dict(s_json)
    db.commit()

    logger.warning("[KILL-SWITCH] ⚠ Slice %s ('%s') destruido forzosamente por %s… (rol=%s). Motivo: %s",
                   slice_id, slice_name, user.user_id[:8], user.role, reason)
    audit(user.user_id, user.role, "KillSwitch", "force_destroy",
          f"Slice '{slice_name}' (dueño {owner_id[:8]}…) destruido forzosamente. Motivo: {reason}",
          level="WARNING", slice_id=slice_id, project_id=db_slice.project_id)

    # Sin recursos desplegados → eliminar directo; desplegado → orden NATS
    if db_slice.status in ("DRAFT", "REJECTED", "PENDING_APPROVAL"):
        _release_external_ips(db, slice_id)
        db.query(Vm).filter(Vm.slice_id == slice_id).delete()
        db.delete(db_slice)
        db.commit()
        result = {"status": "DELETED", "message": "Slice eliminado forzosamente."}
    else:
        published = await destroy_deployed_slice(db, db_slice)
        if not published:
            raise HTTPException(status_code=500, detail="Error enviando orden a NATS")
        result = {"status": "ACCEPTED", "message": "Destrucción forzada enviada."}

    await notification_hub.notify_user(owner_id, {
        "type":     "slice_killed",
        "slice_id": slice_id,
        "title":    "Slice destruido por un administrador",
        "message":  f"Tu slice \"{slice_name}\" fue destruido forzosamente. Motivo: {reason}",
    })
    return result

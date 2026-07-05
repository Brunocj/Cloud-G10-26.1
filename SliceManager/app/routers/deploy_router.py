from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Image, Slice, Vm, Vlan, IpPool, Worker
from app.schemas import DeployRequest, BulkDeployRequest
from app.auth import CurrentUser, get_current_user
from app.services.placement_worker import placement_queue
from app.nats_producer import nats_producer
from app.routers.project_router import can_deploy_directly, user_can_choose_project
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

    # ── Autorización de negocio ──────────────────────────────────────
    if not user.is_owner_or_above(db_slice.creator_id, min_role="admin"):
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
    db.commit()

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

    # ── Autorización de negocio ──────────────────────────────────────
    # Un usuario solo puede destruir sus propios slices.
    # admin y superAdmin pueden destruir cualquier slice.
    if not user.is_owner_or_above(db_slice.creator_id, min_role="admin"):
        raise HTTPException(
            status_code=403,
            detail="No tienes permiso para destruir el slice de otro usuario.",
        )

    # Si es un borrador, solo borramos de la BD
    if db_slice.status == "DRAFT":
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

    db.query(Vlan).filter(Vlan.slice_id == slice_id).delete()

    s_json = db_slice.slice_json
    if isinstance(s_json, str):
        s_json = json.loads(s_json)
    if not s_json:
        s_json = {}

    # Re-inyectar claves SSH desde BD (pueden estar vacías en el JSON guardado
    # si al momento del deploy el key_path era incorrecto)
    all_workers = db.query(Worker).all()
    workers_by_ip = {w.ip: w for w in all_workers}

    deployed_vms   = _enrich_vms_with_ssh_keys(s_json.get("deployed_vms", []),   workers_by_ip)
    deployed_links = _enrich_links_with_ssh_keys(s_json.get("deployed_links", []), workers_by_ip)

    payload = {
        "slice_id":             str(slice_id),
        "request_id":           f"req-destroy-{uuid.uuid4().hex[:8]}",
        "availability_zone_id": db_slice.availability_zone_id or 1,
        "vms":                  deployed_vms,
        "links":                deployed_links,
    }

    published = await nats_producer.publish_destroy(payload)

    if published:
        db_slice.status = "TERMINATED"
        db_slice.date_destruction = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        db.query(Vm).filter(Vm.slice_id == slice_id).update({"state": "TERMINATED"})
        _release_external_ips(db, slice_id)
        db.commit()
        logger.info("[DESTROY] slice_id=%s terminado por user=%s…", slice_id, user.user_id[:8])
        return {"status": "ACCEPTED", "message": "Orden de destrucción enviada."}

    raise HTTPException(status_code=500, detail="Error enviando orden a NATS")

from fastapi import APIRouter, HTTPException
from app.database import SessionLocal, Worker, Vm
from app.targets import get_targets, get_target, get_node_targets, get_node_target
from app.services.prometheus_client import (
    get_worker_cpu_usage, get_worker_ram_usage,
    check_connectivity,
)
from app.services.weight_updater import get_window_state
from app.services.scheduler import get_scheduler_status
from app.config import OC_CPU_DEFAULT, OC_RAM_DEFAULT, OC_DISK_DEFAULT

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/workers")
async def get_all_workers():
    """Current OC factors and live usage for all workers."""
    db = SessionLocal()
    try:
        workers = db.query(Worker).all()
        targets = get_targets()
        window = get_window_state()
        result = []

        node_targets = get_node_targets()
        for w in workers:
            instance = targets.get(w.id)
            node_instance = node_targets.get(w.id)
            cpu_usage = await get_worker_cpu_usage(node_instance) if node_instance else None
            ram_usage = await get_worker_ram_usage(node_instance) if node_instance else None
            reachable = await check_connectivity(instance) if instance else False
            wstate = window.get(str(w.id), {})

            result.append({
                "worker_id":          w.id,
                "prometheus_target":  instance,
                "reachable":          reachable,
                "cpu_cores":          w.cpu,
                "ram_gb":             round(w.ram / 1024, 2) if w.ram else None,
                "disk_gb":            w.disk_gb,
                "oc_cpu":             w.oc_cpu   or OC_CPU_DEFAULT,
                "oc_ram":             w.oc_ram   or OC_RAM_DEFAULT,
                "oc_disco":           w.oc_disco or OC_DISK_DEFAULT,
                "oc_source":          "observability" if w.oc_cpu else "default",
                "live_cpu_usage_pct": round(cpu_usage * 100, 2) if cpu_usage is not None else None,
                "live_ram_usage_pct": round(ram_usage * 100, 2) if ram_usage is not None else None,
                "active_vms":         [],
                "window":             wstate,
            })

        return {"workers": result}
    finally:
        db.close()


@router.get("/workers/{worker_id}")
async def get_worker(worker_id: int):
    """OC factors, live usage and Welford state for a specific worker."""
    db = SessionLocal()
    try:
        w = db.query(Worker).filter(Worker.id == worker_id).first()
        if not w:
            raise HTTPException(status_code=404, detail=f"Worker {worker_id} not found")

        instance = get_target(worker_id)
        node_instance = get_node_target(worker_id)
        cpu_usage = await get_worker_cpu_usage(node_instance) if node_instance else None
        ram_usage = await get_worker_ram_usage(node_instance) if node_instance else None
        reachable = await check_connectivity(instance) if instance else False
        wstate = get_window_state().get(str(worker_id), {})

        # Active VMs on this worker
        vms = db.query(Vm).filter(Vm.worker_id == worker_id, Vm.state == "ACTIVE").all()
        vms_detail = [
            {
                "vm_id":     vm.id,
                "name":      vm.name,
                "vcore":     vm.vcore,
                "ram_mb":    vm.ram,
                "disk_gb":   vm.disk,
            }
            for vm in vms
        ]

        return {
            "worker_id":          w.id,
            "prometheus_target":  instance,
            "reachable":          reachable,
            "cpu_cores":          w.cpu,
            "ram_gb":             round(w.ram / 1024, 2) if w.ram else None,
            "disk_gb":            w.disk_gb,
            "oc_cpu":             w.oc_cpu   or OC_CPU_DEFAULT,
            "oc_ram":             w.oc_ram   or OC_RAM_DEFAULT,
            "oc_disco":           w.oc_disco or OC_DISK_DEFAULT,
            "oc_source":          "observability" if w.oc_cpu else "default",
            "live_cpu_usage_pct": round(cpu_usage * 100, 2) if cpu_usage is not None else None,
            "live_ram_usage_pct": round(ram_usage * 100, 2) if ram_usage is not None else None,
            "welford":            wstate,
            "active_vms":         vms_detail,
        }
    finally:
        db.close()


@router.get("/workers/{worker_id}/window")
async def get_worker_window(worker_id: int):
    """Welford sliding window state for a specific worker."""
    wstate = get_window_state().get(str(worker_id))
    if not wstate:
        raise HTTPException(status_code=404, detail=f"No window data for worker {worker_id} yet")
    return {"worker_id": worker_id, **wstate}


@router.get("/status")
async def get_status():
    """Scheduler status and targets connectivity summary."""
    targets = get_targets()
    connectivity = {}
    for wid, instance in targets.items():
        connectivity[wid] = await check_connectivity(instance)

    return {
        "scheduler":    get_scheduler_status(),
        "targets":      targets,
        "connectivity": connectivity,
        "window_state": get_window_state(),
    }

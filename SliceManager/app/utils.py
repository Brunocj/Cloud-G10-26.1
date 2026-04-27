# app/utils.py
import logging

logger = logging.getLogger("SliceManager.Utils")

def extract_vms_for_placement(topology_json: dict) -> list:
    """
    Recorre el JSON del lienzo, filtra solo los nodos que son VMs
    y los formatea para que coincidan con lo que pide el VM Placement.
    """
    vms_for_placement = []
    
    # Validamos que el JSON tenga la estructura mínima esperada
    if not topology_json or "nodes" not in topology_json:
        logger.warning("El JSON de la topología está vacío o no tiene nodos.")
        return []

    for node in topology_json.get("nodes", []):
        if node.get("type") == "vm":
            # Extraemos los datos, usando valores por defecto de seguridad si el frontend falla
            vm_data = {
                "vm_id": node.get("id"),
                "vcpus": node.get("vcpus", 1),
                "ram_mb": node.get("ram_mb", 512),
                "disk_gb": node.get("disk_gb", 10)
            }
            vms_for_placement.append(vm_data)
            
    return vms_for_placement

def validate_topology_graph(topology_json: dict) -> bool:
    """
    FU-01: Valida la consistencia del grafo.
    Por ejemplo: Verifica que no haya VMs 'flotando' sin conectar a nada.
    """
    # En una versión más avanzada, aquí verificas que todos los IDs en 'nodes'
    # existan dentro de alguna conexión en 'edges'.
    nodes = topology_json.get("nodes", [])
    edges = topology_json.get("edges", [])
    
    if not nodes:
        return False
        
    return True # Por ahora asumimos que es válido si tiene nodos
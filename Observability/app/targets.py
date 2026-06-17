import yaml
import logging
from typing import Dict, Optional, Tuple
from app.config import TARGETS_FILE

logger = logging.getLogger("observability.targets")

# { worker_id: prometheus_target }  — libvirt_exporter
_targets: Dict[int, str] = {}

# { worker_id: node_target }  — node_exporter
_node_targets: Dict[int, str] = {}


def load_targets() -> Tuple[Dict[int, str], Dict[int, str]]:
    global _targets, _node_targets
    try:
        with open(TARGETS_FILE, "r") as f:
            data = yaml.safe_load(f)
        _targets = {}
        _node_targets = {}
        for entry in data.get("workers", []):
            wid = int(entry["worker_id"])
            _targets[wid] = entry["prometheus_target"]
            if "node_target" in entry:
                _node_targets[wid] = entry["node_target"]
        logger.info(
            "Loaded %d libvirt targets, %d node targets from %s",
            len(_targets), len(_node_targets), TARGETS_FILE
        )
    except FileNotFoundError:
        logger.error("targets.yml not found at %s", TARGETS_FILE)
    except Exception as e:
        logger.error("Error loading targets.yml: %s", e)
    return _targets, _node_targets


def get_targets() -> Dict[int, str]:
    if not _targets:
        load_targets()
    return _targets


def get_node_targets() -> Dict[int, str]:
    if not _node_targets:
        load_targets()
    return _node_targets


def get_target(worker_id: int) -> Optional[str]:
    return get_targets().get(worker_id)


def get_node_target(worker_id: int) -> Optional[str]:
    return get_node_targets().get(worker_id)

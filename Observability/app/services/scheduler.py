import asyncio
import logging
from app.config import OC_UPDATE_INTERVAL
from app.services.weight_updater import run_update_cycle

logger = logging.getLogger("observability.scheduler")

_last_cycle_at: str | None = None
_cycle_count: int = 0
_last_error: str | None = None


async def scheduler_loop():
    """Main background loop — runs OC update cycle every OC_UPDATE_INTERVAL seconds."""
    global _last_cycle_at, _cycle_count, _last_error

    logger.info("Scheduler started — interval=%ds", OC_UPDATE_INTERVAL)

    while True:
        await asyncio.sleep(OC_UPDATE_INTERVAL)
        try:
            logger.info("Running OC update cycle #%d", _cycle_count + 1)
            await run_update_cycle()
            from datetime import datetime
            _last_cycle_at = datetime.utcnow().isoformat() + "Z"
            _cycle_count += 1
            _last_error = None
        except Exception as e:
            _last_error = str(e)
            logger.error("Scheduler cycle error: %s", e)


def get_scheduler_status() -> dict:
    return {
        "last_cycle_at": _last_cycle_at,
        "cycle_count":   _cycle_count,
        "last_error":    _last_error,
        "interval_s":    OC_UPDATE_INTERVAL,
    }

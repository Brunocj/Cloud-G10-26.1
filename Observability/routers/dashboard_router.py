from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pathlib import Path

router = APIRouter(tags=["dashboard"])

DASHBOARD_PATH = Path(__file__).parent.parent / "static" / "dashboard.html"


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_PATH.read_text()

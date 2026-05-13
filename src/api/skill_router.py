from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
import pathlib

router = APIRouter()
TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "skill_templates"


@router.get("/api/skill/daily-brief.md", response_class=PlainTextResponse)
async def download_skill_md(request: Request):
    base_url = str(request.base_url).rstrip("/")
    template = (TEMPLATES_DIR / "daily_brief_skill.md").read_text(encoding="utf-8")
    return PlainTextResponse(
        content=template.replace("{BASE_URL}", base_url),
        headers={"Content-Disposition": 'attachment; filename="daily_brief_skill.md"'}
    )


@router.get("/api/skill/daily-brief.py", response_class=PlainTextResponse)
async def download_skill_script(request: Request):
    base_url = str(request.base_url).rstrip("/")
    template = (TEMPLATES_DIR / "daily_brief_script.py").read_text(encoding="utf-8")
    return PlainTextResponse(
        content=template.replace("{BASE_URL}", base_url),
        headers={"Content-Disposition": 'attachment; filename="daily_brief.py"'}
    )

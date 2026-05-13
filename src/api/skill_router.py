from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
import io
import pathlib
import zipfile

router = APIRouter()
SKILL_DIR = pathlib.Path(__file__).parent.parent / "skill_templates" / "dorami-daily-brief"
SKILL_NAME = "dorami-daily-brief"


@router.get("/api/skill/daily-brief")
async def download_skill(request: Request):
    base_url = str(request.base_url).rstrip("/")
    skill_content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    skill_content = skill_content.replace("{BASE_URL}", base_url)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src_file in SKILL_DIR.rglob("*"):
            if src_file.is_dir():
                continue
            rel = src_file.relative_to(SKILL_DIR)
            if src_file.name == "SKILL.md":
                zf.writestr(f"{SKILL_NAME}/SKILL.md", skill_content)
            else:
                zf.write(src_file, f"{SKILL_NAME}/{rel}")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{SKILL_NAME}.zip"'},
    )

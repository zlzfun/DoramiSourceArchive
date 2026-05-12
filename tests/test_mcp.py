import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from sqlmodel import create_engine, SQLModel, Session

def make_engine():
    """In-memory SQLite engine with all tables created."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


# ── Task 2 ────────────────────────────────────────────────────────────────────

def test_app_setting_crud():
    from models.db import AppSettingRecord
    engine = make_engine()
    with Session(engine) as s:
        s.add(AppSettingRecord(key="mcp_enabled", value="true"))
        s.commit()
    with Session(engine) as s:
        rec = s.get(AppSettingRecord, "mcp_enabled")
        assert rec is not None and rec.value == "true"
        rec.value = "false"
        s.add(rec)
        s.commit()
    with Session(engine) as s:
        rec = s.get(AppSettingRecord, "mcp_enabled")
        assert rec.value == "false"

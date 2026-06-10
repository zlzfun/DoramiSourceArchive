import json
import os
import sys

from sqlmodel import Session, select


PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

from ensure_daily_collection_job import ensure_collection_job, selected_fetcher_ids
from models.db import CollectionJobRecord
from storage.impl.db_storage import DatabaseStorage


GENERIC_ADVANCED_FETCHER_IDS = {
    "generic_rss",
    "generic_github_releases",
    "generic_github_repositories",
    "generic_huggingface_models",
}


def test_selected_fetcher_ids_excludes_generic_advanced_by_default():
    fetcher_ids = set(selected_fetcher_ids())

    assert fetcher_ids
    assert not (fetcher_ids & GENERIC_ADVANCED_FETCHER_IDS)


def test_selected_fetcher_ids_can_include_advanced_nodes():
    fetcher_ids = set(selected_fetcher_ids(include_advanced=True))

    assert GENERIC_ADVANCED_FETCHER_IDS <= fetcher_ids


def test_ensure_collection_job_upserts_by_name(tmp_path):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'cms.db'}")

    first = ensure_collection_job(
        engine=db.engine,
        name="每日全量采集",
        description="first",
        cron_expr="10 7 * * *",
        fetcher_ids=["rss_openai_news"],
        is_active=True,
    )
    second = ensure_collection_job(
        engine=db.engine,
        name="每日全量采集",
        description="second",
        cron_expr="20 8 * * *",
        fetcher_ids=["rss_openai_news", "web_anthropic_news"],
        is_active=False,
    )

    assert first["action"] == "created"
    assert second["action"] == "updated"
    assert first["job_id"] == second["job_id"]

    with Session(db.engine) as session:
        jobs = session.exec(select(CollectionJobRecord)).all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.description == "second"
        assert job.cron_expr == "20 8 * * *"
        assert job.is_active is False
        assert json.loads(job.fetcher_ids_json) == ["rss_openai_news", "web_anthropic_news"]

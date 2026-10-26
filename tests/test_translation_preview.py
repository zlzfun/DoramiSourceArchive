"""Production block projection, including cross-request Markdown boundaries."""
import asyncio
import json

import pytest
from sqlmodel import Session

from services import translation_preview as preview, translation_jobs as jobs, translation_updates
from services.translation_markdown import plan_translation
from tests.test_translation_jobs import env  # noqa: F401 (isolated DB/provider fixture)


def state(plan, done=None, cursor=None):
    return preview.project(plan, done or {}, job_id="job", input_key="fingerprint", cursor=cursor)


def test_long_paragraph_waits_for_all_its_chunks_but_later_paragraph_is_ready():
    body = "Long prose with a formula $x+y$ and useful words. " * 12 + "\n\nA short final paragraph."
    plan = plan_translation(body, segment_chars=100, namespace="0123456789ab")
    layout = preview.layout_for(plan)
    assert len(layout.blocks) == 2
    assert len(layout.blocks[0].dependencies) > 2
    last = len(plan.chunks) - 1
    first = state(plan, {last: plan.chunks[last].text})
    assert first["reset"] and not first["body_complete"]
    assert first["blocks"][0]["translation"] is None
    assert first["blocks"][1]["translation"] == "A short final paragraph."
    assert "DORAMITR" not in json.dumps(first)
    done = {i: chunk.text for i, chunk in enumerate(plan.chunks)}
    delta = state(plan, done, first["cursor"])
    assert not delta["reset"] and delta["body_complete"]
    assert [b["id"] for b in delta["blocks"]] == ["b0"]
    assert "source" not in delta["blocks"][0]
    assert delta["blocks"][0]["translation"] == layout.blocks[0].source
    assert state(plan, done, delta["cursor"])["blocks"] == []


def test_multichunk_table_and_loose_nested_list_never_render_in_pieces():
    body = ("Before.\n\n| Name | Count |\n| :--- | ---: |\n" +
            "\n".join(f"| Experiment {i} | {i} |" for i in range(30)) +
            "\n\n- First item\n\n  A continuation paragraph.\n\n  - nested item\n\n- Second item\n\nAfter.")
    plan = plan_translation(body, segment_chars=100, namespace="0123456789ab")
    layout = preview.layout_for(plan)
    table = next(b for b in layout.blocks if b.kind == "table")
    listing = next(b for b in layout.blocks if b.kind == "list")
    assert len(table.dependencies) > 2
    assert "nested item" in listing.source and "Second item" in listing.source
    done = {i: plan.chunks[i].text for i in table.dependencies}
    blocked = dict(done)
    blocked.pop(min(table.dependencies))
    assert next(b for b in state(plan, blocked)["blocks"] if b["id"] == table.id)["translation"] is None
    ready = next(b for b in state(plan, done)["blocks"] if b["id"] == table.id)
    assert ready["translation"] == table.source
    assert "| :--- | ---: |" in ready["translation"]


def test_formula_code_images_and_reference_links_restore_without_placeholder_leak():
    body = ("# Heading\n\nRead [the source][ref].\n\n```py\nprint('hello')\n\nprint('world')\n```"
            "\n\n$$\nx = y\n\nz = 2\n$$\n\n![plot](https://example.invalid/image.png)"
            "\n\n[ref]: https://example.invalid/page \"Reference\"")
    plan = plan_translation(body, segment_chars=100, namespace="0123456789ab")
    layout = preview.layout_for(plan)
    assert {"heading", "code", "math", "image"}.issubset({b.kind for b in layout.blocks})
    data = state(plan, {i: c.text for i, c in enumerate(plan.chunks)})
    assert data["body_complete"] and "DORAMITR" not in json.dumps(data)
    link = next(b for b in data["blocks"] if "Read [the source]" in b["source"])
    assert '[ref]: https://example.invalid/page "Reference"' in link["translation"]
    assert plan.assemble([c.text for c in plan.chunks]) == body


@pytest.mark.parametrize("cursor", ["other:1", "job:gg", "job:" + "f" * 600, "job:ffffffff", "missing"])
def test_untrusted_cursors_can_only_force_a_layout_reset(cursor):
    plan = plan_translation("A paragraph.")
    data = state(plan, cursor=cursor)
    assert data["reset"] and data["blocks"][0]["translation"] is None


def test_corrupted_checkpoint_is_not_ready_and_resets_previously_ready_client():
    plan = plan_translation("First $x$.\n\nSecond paragraph.", namespace="0123456789ab")
    full = state(plan, {0: plan.chunks[0].text})
    damaged = state(plan, {0: plan.chunks[0].text.replace(next(iter(plan.literals)), "")}, full["cursor"])
    assert damaged["reset"] and not damaged["body_complete"]
    assert all(b["translation"] is None for b in damaged["blocks"])


def test_status_projects_db_checkpoints_and_invalidates_input(env):
    engine, _, _ = env
    started = jobs.request(engine, "user", "a")
    assert started["preview"]["reset"]
    job_id, lease = jobs.claim(engine)
    with Session(engine) as session:
        article, config = jobs._current(session, session.get(jobs.Job, job_id))
        plan, _ = jobs._plan(article, jobs.input_key(article, config))
    last = len(plan.chunks) - 1
    jobs._checkpoint(engine, job_id, lease, last, plan.chunks[last].text)
    data = jobs.status(engine, "user", "a", started["preview"]["cursor"])
    assert data["preview"]["cursor"] != started["preview"]["cursor"]
    assert data["result"] is None
    with Session(engine) as session:
        article = session.get(jobs.ArticleRecord, "a")
        article.content = "A different current body."
        session.commit()
    changed = jobs.status(engine, "user", "a", data["preview"]["cursor"])
    assert changed["preview"] is None and changed["job"] is None


def test_notification_observes_committed_data_and_subscription_is_released(env):
    engine, _, _ = env
    started = jobs.request(engine, "user", "a")
    job_id, lease = jobs.claim(engine)
    with Session(engine) as session:
        article, config = jobs._current(session, session.get(jobs.Job, job_id))
        plan, _ = jobs._plan(article, jobs.input_key(article, config))

    async def observe():
        async with translation_updates.watch(engine, "a") as changed:
            await asyncio.to_thread(jobs._checkpoint, engine, job_id, lease, 0, plan.chunks[0].text)
            await asyncio.wait_for(changed.wait(), timeout=1)
            data = await asyncio.to_thread(jobs.status, engine, "user", "a", started["preview"]["cursor"])
            assert any(block["translation"] for block in data["preview"]["blocks"])
        assert not translation_updates._waiters
    asyncio.run(observe())


def test_disabled_ai_cannot_read_partial_content(env):
    engine, _, _ = env
    jobs.request(engine, "user", "a")
    from models.db import UserRecord
    with Session(engine) as session:
        session.get(UserRecord, "user").ai_beta_enabled = False
        session.commit()
    with pytest.raises(jobs.Error) as error:
        jobs.status(engine, "user", "a")
    assert error.value.status_code == 403


@pytest.mark.parametrize("literal", ["```python\n   print(1)\n   ```", "$$\n   x = 1\n   $$"])
def test_literal_preserves_its_list_container(literal):
    body = "1. First item\n\n   " + literal + "\n\n2. Second item\n\nLast paragraph."
    plan = plan_translation(body, namespace="0123456789ab")
    blocks = preview.layout_for(plan).blocks
    assert len(blocks) == 2
    assert blocks[0].kind == "list"
    assert blocks[0].source == body.rsplit("\n\n", 1)[0]
    assert "2. Second item" in blocks[0].source


def test_many_references_do_not_multiply_every_definition_per_paragraph():
    body = "\n\n".join(f"Paragraph {i} cites [reference {i}][r{i}]." for i in range(200))
    body += "\n\n" + "\n".join(f"[r{i}]: https://example.invalid/source/{i}" for i in range(200))
    plan = plan_translation(body, namespace="0123456789ab")
    data = state(plan, {i: c.text for i, c in enumerate(plan.chunks)})
    assert len(data["blocks"]) == 200
    assert len(json.dumps(data)) < len(body) * 5
    assert data["blocks"][0]["translation"].endswith("[r0]: https://example.invalid/source/0")
    assert "[r199]:" not in data["blocks"][0]["translation"]


@pytest.mark.parametrize("revocation", ["epoch", "expiry", "ai_permission"])
def test_sse_rechecks_cookie_and_permission_after_first_snapshot(env, monkeypatch, revocation):
    from types import SimpleNamespace
    import importlib
    from starlette.requests import Request
    from api.routers import reader
    from models.db import UserRecord
    app = importlib.import_module("api.app")
    engine, _, _ = env
    monkeypatch.setattr(app, "db_sink", SimpleNamespace(engine=engine))
    monkeypatch.setattr(jobs, "POLL_SECONDS", 0.001)
    jobs.request(engine, "user", "a")
    with Session(engine) as session:
        epoch = session.get(UserRecord, "user").session_epoch
    token = app.create_auth_token("user", "user", epoch=epoch)
    request = Request({"type": "http", "method": "GET", "path": "/api/reader/ai/translation-jobs/events",
                       "headers": [(b"cookie", f"{app.AUTH_COOKIE_NAME}={token}".encode())]})

    async def connected():
        return False
    monkeypatch.setattr(request, "is_disconnected", connected)

    async def observe():
        response = await reader.reader_translation_events("a", request)
        first = await anext(response.body_iterator)
        assert "event: snapshot" in first and '"blocks"' in first
        assert request.state.dorami_auth_session["sub"] == "user"
        if revocation == "expiry":
            now = app.time.time()
            monkeypatch.setattr(app, "time", SimpleNamespace(time=lambda: now + app.AUTH_SESSION_SECONDS + 2))
        else:
            with Session(engine) as session:
                account = session.get(UserRecord, "user")
                if revocation == "epoch":
                    account.session_epoch = "revoked-after-snapshot"
                else:
                    account.ai_beta_enabled = False
                session.add(account)
                session.commit()
        denied = await anext(response.body_iterator)
        assert "event: error" in denied
        assert f'"status": {403 if revocation == "ai_permission" else 401}' in denied
        assert "blocks" not in denied
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)
        assert not translation_updates._waiters
    asyncio.run(observe())

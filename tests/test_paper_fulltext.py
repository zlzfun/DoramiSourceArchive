"""Paper enrichment: fidelity, durable recovery, identity and analysis budget."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import hashlib
import json
import sys
import unicodedata
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fetchers.impl.arxiv_paper_extractor import extract_arxiv_html
from models.content import WebPageArticleContent
from models.db import ArticleRecord, PaperFulltextJobRecord
from services import paper_fulltext as pf
from services.article_analysis import analysis_input_from_article, compute_analysis_input_hash, compute_content_hash
from services.paper_content import analysis_body, delivery_body, metadata
from storage.impl.db_storage import DatabaseStorage

ABSTRACT = "We propose a method for reliable reasoning."
FULL_ONLY = "fulltextuniquesentinel"
PAPER_HTML = (
    '<article class="ltx_document"><h1>Test paper</h1>'
    '<div class="ltx_abstract"><h2>Abstract</h2><p>Short abstract.</p></div>'
    '<section class="ltx_section"><h2>1 Method</h2><p>'
    + (f"{FULL_ONLY} with a detailed evaluation. " * 18)
    + '</p><p>Inline <math alttext="x^2"><mi>DUPLICATED</mi><annotation encoding="application/x-tex">x^2</annotation></math>.</p>'
    '<table class="ltx_eqn_table"><tr><td><math display="block" alttext="E=mc^2">DUPLICATED</math></td><td>(1)</td></tr></table>'
    '<figure><object data="2501.12948v1/figure.svg" type="image/svg+xml"></object><figcaption>Figure 1: Caption.</figcaption></figure>'
    '<table><tr><th rowspan="2">Model</th><th colspan="2">Scores</th></tr>'
    '<tr><th>A</th><th>B</th></tr><tr><td>Ours</td><td>9</td><td>8</td></tr></table>'
    '<p>See <a href="#S1">Method</a> and <a href="javascript:alert(1)">bad link</a>.</p>'
    '</section><section class="ltx_section"><h2>References</h2><p>Last reference.</p></section></article>'
)


def extracted():
    return extract_arxiv_html(PAPER_HTML, "https://arxiv.org/html/2501.12948v1")


@pytest.fixture
def storage(tmp_path):
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'paper.db'}")
    yield sink
    sink.engine.dispose()


def seed(storage, *, article_id="paper", raw_abstract=None):
    raw = {"arxiv_id": "2501.12948v1", "detail_extraction_method": "huggingface_daily_papers_json"}
    if raw_abstract is not None:
        raw["paper_abstract"] = raw_abstract
    article = ArticleRecord(
        id=article_id, title="Paper title", source_id="web_huggingface_daily_papers",
        source_url="https://huggingface.co/papers/2501.12948", content_type="web_article",
        content=ABSTRACT, has_content=True, publish_date="2026-01-01", fetched_date="2026-01-02",
        extensions_json=json.dumps({"raw_data": raw, "unrelated": 42, "translation_zh": "old", "summary_zh": "old"}),
    )
    with Session(storage.engine) as session:
        session.add(article)
        session.flush()
        assert pf.enqueue(session, article)
        session.commit()
    return article_id


def test_converter_preserves_tex_graphics_spans_and_references():
    result = extracted()
    assert result.math_count == 2 and result.image_count == 1 and result.table_count == 1
    assert "$x^2$" in result.content and "$$\nE=mc^2\n$$" in result.content
    assert "DUPLICATED" not in result.content
    assert "DORAMIPAPERTOKEN" not in result.content
    assert "https://arxiv.org/html/2501.12948v1/figure.svg" in result.content
    assert "Figure 1: Caption." in result.content
    assert "| Model | Scores | Scores |" in result.content
    assert "| Model | A | B |" in result.content
    assert "https://arxiv.org/html/2501.12948v1#S1" in result.content
    assert "javascript:" not in result.content
    assert result.content.endswith("Last reference.")
    assert result.warnings == ()


def test_converter_fails_honestly_and_does_not_fetch_embedded_resources():
    with pytest.raises(ValueError, match="not_paper_html"):
        extract_arxiv_html("<html>Cloud challenge</html>", "https://arxiv.org/html/2501.12948")
    result = extract_arxiv_html(PAPER_HTML.replace("2501.12948v1/figure.svg", "http://127.0.0.1/private"), "https://arxiv.org/html/2501.12948v1")
    assert result.image_count == 0 and "unsupported_image" in result.warnings
    assert "127.0.0.1" not in result.content
    with pytest.raises(ValueError, match="table_too_large"):
        extract_arxiv_html(PAPER_HTML.replace('colspan="2"', 'colspan="999999"'), "https://arxiv.org/html/2501.12948v1")
    with pytest.raises(ValueError, match="paper_too_long"):
        extract_arxiv_html(PAPER_HTML.replace(FULL_ONLY, FULL_ONLY * 1000), "https://arxiv.org/html/2501.12948v1")


def test_tex_colors_array_options_and_adjacent_math_remain_separate():
    html = PAPER_HTML.replace(
        "Inline <math",
        'Inline <math alttext="\\color[rgb]{1,0,0}x">x</math><math',
    ).replace('alttext="E=mc^2"', 'alttext="\\begin{array}[]{lc}a&amp;b\\end{array}"')
    result = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1")
    assert r"\color{#ff0000}x" in result.content
    assert r"\begin{array}{lc}" in result.content
    assert "$ $x^2$" in result.content or "$  $x^2$" in result.content


def test_table_math_preserves_single_and_double_bars():
    formula = r"p(y|x)+\|x\|+\left|x\right|"
    html = PAPER_HTML.replace("<td>Ours</td>", f'<td><math alttext="{formula}">formula</math></td>')
    result = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1")
    assert r"p(y\vert x)+\Vert x\Vert +\left\vert x\right\vert " in result.content
    assert r"\|" not in result.content
    assert not result.warnings


@pytest.mark.parametrize("environment", ["array", "tabular"])
def test_table_tex_column_rules_are_explicit_partial(environment):
    formula = rf"\begin{{{environment}}}[t]{{|lc|}}a&amp;b\end{{{environment}}}"
    html = PAPER_HTML.replace("<td>Ours</td>", f'<td><math alttext="{formula}">formula</math></td>')
    result = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1")
    assert "table_tex_layout" in result.warnings
    assert rf"` \begin{{{environment}}}" in result.content
    assert r"\vert" not in result.content


def test_undefined_document_setup_does_not_precede_title():
    html = PAPER_HTML.replace(
        "<h1>Test paper</h1>",
        '<div><span class="ltx_ERROR undefined">setup macro</span><p>001</p></div>'
        '<h1 class="ltx_title_document">Test paper</h1>',
    )
    result = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1")
    assert result.content.startswith("# Test paper\n")
    assert "setup macro" not in result.content
    assert FULL_ONLY in result.content


def test_latexml_fonts_abstract_caption_and_alignment_survive_conversion():
    html = PAPER_HTML.replace(
        "<h2>Abstract</h2>",
        '<h6 class="ltx_title_abstract">Abstract</h6>',
    ).replace(
        "Inline <math",
        '<span class="ltx_font_bold">Reasoning tasks</span>: '
        '<span class="ltx_font_italic">emphasis</span> '
        '<span class="ltx_font_bold ltx_font_italic">both</span> '
        '<span class="ltx_font_typewriter">model_name</span> Inline <math',
    ).replace(
        "<figcaption>Figure 1: Caption.</figcaption>",
        '<figcaption class="ltx_centering"><span class="ltx_font_bold">'
        'Figure 1:</span> Caption.</figcaption>',
    ).replace(
        "<td>Ours</td>",
        '<td class="ltx_align_left">Ours</td>',
    ).replace("<td>9</td>", '<td class="ltx_align_center">9</td>').replace(
        "<td>8</td>", '<td class="ltx_align_right">8</td>',
    ).replace(
        "<h2>References</h2>",
        '<h2>References</h2><div class="ltx_centering"><p>Centered paragraph.</p>'
        '<p style="text-align:right;position:fixed">Right paragraph.</p></div>',
    )
    result = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1")
    assert "[zqpl]: #zq1.zq4\n\n###### Abstract" in result.content
    assert "**Reasoning tasks**: *emphasis* ***both*** `model_name`" in result.content
    assert "[zqpl]: #zq2.zq4\n\n**Figure 1:** Caption." in result.content
    assert "| :--- | :---: | ---: |" in result.content
    assert "[zqpl]: #zq4\n\nCentered paragraph." in result.content
    assert "[zqpl]: #zq5\n\nRight paragraph." in result.content
    assert "position:fixed" not in result.content
    assert "zqpl" not in result.abstract
    assert "DORAMIPAPERTOKEN" not in result.content
    assert "$x^2$" in result.content and result.math_count == 2


def test_layout_markers_cannot_be_forged_by_source_and_lists_keep_emphasis():
    html = PAPER_HTML.replace(
        "<h2>References</h2>",
        '<h2>References</h2><ul><li><p><span class="ltx_font_bold">'
        'Item</span>: details.</p></li></ul>',
    )
    result = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1")
    assert "- **Item**: details." in result.content
    with pytest.raises(ValueError, match="reserved_marker_in_source"):
        extract_arxiv_html(
            html.replace("details.", "[zqpl]: #zq4"),
            "https://arxiv.org/html/2501.12948v1",
        )


def test_source_soft_newlines_reflow_but_explicit_breaks_and_code_remain():
    html = PAPER_HTML.replace(
        "<h2>References</h2>",
        '<h2>References</h2><p>Source\nwraps <span>in\nHTML</span>.<br>Explicit break.</p>'
        '<pre>first\n  second</pre>',
    )
    result = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1")
    assert "Source wraps in HTML.\nExplicit break." in result.content
    assert "```\nfirst\n  second\n```" in result.content


def test_list_preview_removes_layout_metadata_before_truncation():
    from api.articles_view import serialize_article_list_item

    body = "[zqpl]: #zq1.zq4\n\n###### Abstract\n\n" + "Body " * 80
    article = ArticleRecord(
        id="layout-preview", title="Paper", source_id="web_huggingface_daily_papers",
        source_url="https://huggingface.co/papers/2501.12948", content=body,
        content_type="web_article", has_content=True,
    )
    item = serialize_article_list_item(article)
    assert "zqpl" not in item["content_preview"]
    assert len(item["content_preview"]) == 280
    assert item["content"] == body


def test_typewriter_links_and_dollars_keep_their_original_meaning():
    html = PAPER_HTML.replace(
        "Inline <math",
        '<span class="ltx_font_typewriter"><a href="https://example.com/code">Code</a></span> '
        '<span class="ltx_font_typewriter"><span>$HOME</span></span> '
        '<span class="ltx_font_typewriter">line<br>break</span> Inline <math',
    )
    content = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1").content
    assert "[Code](https://example.com/code)" in content
    assert "`[Code]" not in content
    assert "`$HOME`" in content and r"`\$HOME`" not in content
    assert "line\nbreak" in content


def test_structural_font_classes_do_not_remove_items_rows_or_block_math():
    html = PAPER_HTML.replace(
        "<h2>References</h2>",
        '<h2>References</h2><ul class="ltx_font_bold"><li>Kept item.</li></ul>'
        '<p class="ltx_font_italic">Kept formula <math display="block" alttext="a=b">x</math></p>',
    ).replace("<tr><td>Ours", '<tr class="ltx_font_italic"><td>Ours')
    content = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1").content
    assert "- Kept item." in content and "| Ours | 9 | 8 |" in content
    assert "Kept formula \n\n$$\na=b\n$$" in content
    assert "*Kept formula" not in content


def test_nested_caption_has_only_a_block_boundary_marker_and_code_keeps_language():
    html = PAPER_HTML.replace(
        "<figcaption>Figure 1: Caption.</figcaption>",
        '<figcaption class="ltx_centering">Figure 1: <p class="ltx_align_center">Nested caption.</p></figcaption>',
    ).replace("<h2>References</h2>", '<h2>References</h2><pre class="ltx_centering"><code class="language-python">print(1)</code></pre>')
    content = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1").content
    assert "[zqpl]: #zq2.zq4\n\nFigure 1: Nested caption." in content
    assert content.count("[zqpl]") == 1
    assert "```python\nprint(1)\n```" in content


def test_layout_annotations_do_not_create_english_fts_matches(storage):
    html = PAPER_HTML.replace(
        "<h2>Abstract</h2>", '<h6 class="ltx_title_abstract">Abstract</h6>',
    ).replace(
        "<figcaption>Figure 1: Caption.</figcaption>",
        '<figcaption class="ltx_centering">Figure 1: Evaluation.</figcaption>',
    )
    content = extract_arxiv_html(html, "https://arxiv.org/html/2501.12948v1").content
    words = ("center", "caption", "style", "dorami")
    with Session(storage.engine) as session:
        session.add(ArticleRecord(
            id="layout-fts", title="Evaluation", content=content,
            content_type="web_article", source_id="test",
            source_url="https://example.test/layout", publish_date="2026-01-01", fetched_date="2026-01-02",
        ))
        session.commit()
        for word in words:
            assert session.exec(text(
                "SELECT count(*) FROM articles_fts WHERE articles_fts MATCH :query"
            ).bindparams(query=word)).scalar() == 0
        session.add(ArticleRecord(
            id="prose-fts", title="Actual prose", content=" ".join(words),
            content_type="web_article", source_id="test",
            source_url="https://example.test/prose", publish_date="2026-01-01", fetched_date="2026-01-02",
        ))
        session.commit()
        for word in words:
            assert session.exec(text(
                "SELECT count(*) FROM articles_fts WHERE articles_fts MATCH :query"
            ).bindparams(query=word)).scalar() == 1


@pytest.mark.parametrize("location", ["http://127.0.0.1/private", "/html/9999.99999v1", "/html/2501.12948v2"])
def test_download_rejects_redirect_outside_paper_identity(monkeypatch, location):
    @asynccontextmanager
    async def no_wait():
        yield
    original = httpx.AsyncClient
    seen = []
    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": location})
    monkeypatch.setattr(pf, "arxiv_request", no_wait)
    monkeypatch.setattr(pf.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)))
    with pytest.raises(pf.FetchFailure):
        asyncio.run(pf.download_html("2501.12948v1"))
    assert seen == ["https://arxiv.org/html/2501.12948v1"]


def test_download_follows_same_paper_version_and_rejects_oversized_html(monkeypatch):
    @asynccontextmanager
    async def no_wait():
        yield
    original = httpx.AsyncClient
    def respond(request):
        if request.url.path.endswith("v1"):
            return httpx.Response(200, text=PAPER_HTML, headers={"content-type": "text/html"})
        return httpx.Response(302, headers={"location": "/html/2501.12948v1"})
    monkeypatch.setattr(pf, "arxiv_request", no_wait)
    monkeypatch.setattr(pf.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)))
    assert asyncio.run(pf.fetch_html("2501.12948")).arxiv_id == "2501.12948v1"
    monkeypatch.setattr(pf, "MAX_HTML_BYTES", 200)
    with pytest.raises(pf.FetchFailure, match="html_too_large"):
        asyncio.run(pf.download_html("2501.12948"))


def test_fulltext_preserves_identity_analysis_hash_and_updates_fts(storage):
    seed(storage)
    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, "paper")
        old_hash = compute_content_hash(article)
        old_input_hash = compute_analysis_input_hash(analysis_input_from_article(article, None), ())
        old_revision = article.archive_updated_at
        assert not pf.enqueue(session, article)
    task = pf.claim_one(storage.engine, now=100)
    assert pf.finish(storage.engine, task, extraction=extracted(), now=101)
    assert not pf.finish(storage.engine, task, extraction=replace(extracted(), content="stale"), now=102)
    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, "paper")
        assert article.content == extracted().content
        assert article.title == "Paper title" and article.fetched_date == "2026-01-02"
        assert article.source_url == "https://huggingface.co/papers/2501.12948"
        assert article.archive_updated_at > old_revision
        assert analysis_body(article) == ABSTRACT
        assert compute_content_hash(article) == old_hash
        assert compute_analysis_input_hash(analysis_input_from_article(article, None), ()) == old_input_hash
        assert metadata(article)["unrelated"] == 42
        assert "translation_zh" not in metadata(article) and "summary_zh" not in metadata(article)
        assert delivery_body(article) == ABSTRACT
        assert session.exec(text("SELECT count(*) FROM articles_fts WHERE articles_fts MATCH 'fulltextuniquesentinel'")).scalar() == 1
        assert session.get(PaperFulltextJobRecord, "paper").status == "succeeded"
    assert pf.claim_one(storage.engine, now=1000) is None


def test_restart_recovers_expired_lease_without_accepting_old_result(storage):
    seed(storage)
    first = pf.claim_one(storage.engine, now=100)
    assert pf.claim_one(storage.engine, now=101) is None
    second = pf.claim_one(storage.engine, now=100 + pf.LEASE_SECONDS + 1)
    assert second.attempts == 2 and first.lease_token != second.lease_token
    assert not pf.finish(storage.engine, first, extraction=extracted(), now=300)
    assert pf.finish(storage.engine, second, extraction=extracted(), now=301)


@pytest.mark.parametrize("mutation", ["edit", "authority", "consumer", "paper_identity"])
def test_manual_edit_or_receiver_fence_wins_over_inflight_download(storage, mutation):
    seed(storage)
    task = pf.claim_one(storage.engine, now=100)
    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, "paper")
        if mutation == "edit":
            article.content = "Editor correction"
        elif mutation == "authority":
            article.analysis_authority_id = "remote"
        elif mutation == "paper_identity":
            ext = metadata(article)
            ext["raw_data"]["arxiv_id"] = "2408.09869v5"
            article.extensions_json = json.dumps(ext)
        else:
            from services.sync_consumer_policy import activate_v2_consumer_mode
            activate_v2_consumer_mode(session, reason="test", commit=False)
        session.add(article)
        session.commit()
    assert not pf.finish(storage.engine, task, extraction=extracted(), now=101)
    with Session(storage.engine) as session:
        assert session.get(ArticleRecord, "paper").content == ("Editor correction" if mutation == "edit" else ABSTRACT)
        assert session.get(PaperFulltextJobRecord, "paper").status == "skipped"


def test_unavailable_is_bounded_and_never_replaces_abstract(storage):
    seed(storage)
    now = 100.0
    for attempt in range(3):
        task = pf.claim_one(storage.engine, now=now)
        assert task.attempts == attempt + 1
        assert not pf.finish(storage.engine, task, failure=pf.FetchFailure("http_404", 86400, True), now=now + 1)
        assert pf.claim_one(storage.engine, now=now + 2) is None
        now += 200000
    with Session(storage.engine) as session:
        assert session.get(ArticleRecord, "paper").content == ABSTRACT
        assert session.get(PaperFulltextJobRecord, "paper").status == "unavailable"
    assert pf.claim_one(storage.engine, now=now) is None


def test_new_ingestion_enqueues_atomically_without_network(storage):
    item = WebPageArticleContent(
        id="new", title="Paper", source_id="web_huggingface_daily_papers",
        source_url="https://huggingface.co/papers/2501.12948", content=ABSTRACT,
        publish_date="2026-01-01",
        raw_data={"arxiv_id": "2501.12948", "detail_extraction_method": "huggingface_daily_papers_json",
                  "paper_abstract": ABSTRACT + " Untruncated."},
    )
    assert asyncio.run(storage.save(item))
    assert not asyncio.run(storage.save(item))
    with Session(storage.engine) as session:
        assert len(session.exec(select(PaperFulltextJobRecord)).all()) == 1
        assert session.get(PaperFulltextJobRecord, "new").abstract.endswith("Untruncated.")
        session.delete(session.get(ArticleRecord, "new"))
        session.commit()
        assert session.get(PaperFulltextJobRecord, "new") is None


def test_backfill_is_dry_run_by_default_and_idempotent(storage):
    path = Path(__file__).resolve().parents[1] / "scripts/backfill_paper_fulltext.py"
    spec = importlib.util.spec_from_file_location("paper_backfill", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seed(storage)
    with Session(storage.engine) as session:
        session.delete(session.get(PaperFulltextJobRecord, "paper"))
        session.commit()
    assert module.backfill(storage.engine)["count"] == 1
    with Session(storage.engine) as session:
        assert session.get(PaperFulltextJobRecord, "paper") is None
        assert "paper" not in metadata(session.get(ArticleRecord, "paper"))
    assert module.backfill(storage.engine, apply=True)["count"] == 1
    assert module.backfill(storage.engine, apply=True)["count"] == 0


def test_retry_does_not_steal_active_job(storage):
    seed(storage)
    first = pf.claim_one(storage.engine, now=100)
    with Session(storage.engine) as session:
        assert not pf.enqueue(session, session.get(ArticleRecord, "paper"), retry_terminal=True)
        session.commit()
        assert session.get(PaperFulltextJobRecord, "paper").lease_token == first.lease_token
    pf.finish(storage.engine, first, failure=pf.FetchFailure("not_html"), now=101)
    with Session(storage.engine) as session:
        assert pf.enqueue(session, session.get(ArticleRecord, "paper"), retry_terminal=True)
        session.commit()
        assert session.get(PaperFulltextJobRecord, "paper").attempts == 0


def test_retry_never_revives_skipped_manual_edit(storage):
    seed(storage, raw_abstract=ABSTRACT)
    task = pf.claim_one(storage.engine, now=100)
    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, "paper")
        article.content = "Manual correction"
        session.add(article)
        session.commit()
    assert not pf.finish(storage.engine, task, extraction=extracted(), now=101)
    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, "paper")
        assert not pf.enqueue(session, article, retry_terminal=True)
        assert session.get(PaperFulltextJobRecord, "paper").status == "skipped"
        assert article.content == "Manual correction"


@pytest.mark.parametrize("source_id", ["user_rss_arxiv", "manual", "web_huggingface_daily_papers"])
def test_unmanaged_paper_keeps_legacy_archive_hash_but_analyzes_abstract(source_id):
    article = ArticleRecord(
        id="legacy", title="Ａ paper", source_id=source_id, content_type="web_article",
        source_url="https://arxiv.org/abs/2501.12948", content="Existing archived body",
        extensions_json=json.dumps({"summary": ABSTRACT}),
        fetched_date="2026-01-01", publish_date="2026-01-01",
    )
    old_payload = json.dumps([
        unicodedata.normalize("NFKC", article.title).strip(), article.content,
    ], ensure_ascii=False, separators=(",", ":"))
    assert compute_content_hash(article) == hashlib.sha256(old_payload.encode()).hexdigest()
    assert analysis_input_from_article(article, None).body == ABSTRACT


@pytest.mark.parametrize("raw_abstract", [None, ABSTRACT + " Untruncated."])
@pytest.mark.parametrize("succeed", [True, False])
def test_managed_paper_hash_is_stable_for_new_old_success_failure(storage, raw_abstract, succeed):
    seed(storage, raw_abstract=raw_abstract)
    with Session(storage.engine) as session:
        before = compute_content_hash(session.get(ArticleRecord, "paper"))
    task = pf.claim_one(storage.engine, now=100)
    if succeed:
        pf.finish(storage.engine, task, extraction=extracted(), now=101)
    else:
        pf.finish(storage.engine, task, failure=pf.FetchFailure("not_html"), now=101)
    with Session(storage.engine) as session:
        assert compute_content_hash(session.get(ArticleRecord, "paper")) == before


def test_worker_handles_network_error_without_losing_job(storage):
    seed(storage)
    async def failed(_):
        raise httpx.ReadTimeout("test")
    assert asyncio.run(pf.run_batch(storage.engine, fetcher=failed)) == []
    with Session(storage.engine) as session:
        task = session.get(PaperFulltextJobRecord, "paper")
        assert task.status == "pending" and task.last_error == "network_error" and not task.lease_token
        assert session.get(ArticleRecord, "paper").content == ABSTRACT


def test_other_paper_does_not_leak_fulltext_without_abstract():
    article = ArticleRecord(
        id="other", title="No abstract", source_id="research", content_type="arxiv",
        source_url="https://arxiv.org/html/2501.12948", content="UNBOUNDED FULL TEXT",
        fetched_date="2026-01-01", publish_date="2026-01-01",
    )
    assert analysis_body(article) == ""
    article.content = "# Abstract\nActual abstract.\n\n# Method\nUNBOUNDED FULL TEXT"
    assert analysis_body(article) == "Actual abstract."
    article.content = "FULL TEXT"
    article.extensions_json = json.dumps({
        "paper": {"content_level": "fulltext"},
        "raw_data": {"detail_extraction_method": "huggingface_daily_papers_json"},
    })
    assert analysis_body(article) == ""


def test_daily_brief_uses_abstract_for_candidates_scoring_and_editorial(storage, monkeypatch):
    from config import LLMConfig
    from services import daily_brief as brief
    seed(storage)
    task = pf.claim_one(storage.engine, now=100)
    pf.finish(storage.engine, task, extraction=extracted(), now=101)
    with Session(storage.engine) as session:
        candidates, _, _ = brief.collect_candidates(session, cursor="")
    assert len(candidates) == 1 and candidates[0].body == ABSTRACT
    vision_calls, inputs = [], []

    async def vision(*args, **kwargs):
        vision_calls.append(args)
        return "SHOULD NOT BE USED"

    async def analyze(article_input, *args, **kwargs):
        inputs.append(article_input)
        # Valid minimal analysis payload; errors elsewhere cannot hide vision.
        return {"quality_score": 5, "score_reason": "abstract", "summary": "- point",
                "content_genre": "research_paper", "content_kind": "paper", "entities": [], "tags": []}

    async def editorial(**kwargs):
        message = kwargs["messages"][-1].content
        assert FULL_ONLY not in message and ABSTRACT in message
        return '{"summary":["short"],"comment":"Based on abstract."}'

    monkeypatch.setattr(brief.image_insights_service, "ensure_notes", vision)
    monkeypatch.setattr(brief, "analyze_article_with_llm", analyze)
    monkeypatch.setattr(brief, "chat_completion", editorial)
    cfg = LLMConfig(base_url="https://test.invalid", api_key="fake", model="fake")
    asyncio.run(brief._score_one(candidates[0], cfg))
    assert inputs[0].body == ABSTRACT and inputs[0].analysis_basis == "paper_abstract"
    _, ok = asyncio.run(brief._polish_one(brief.ScoredItem(candidates[0]), cfg))
    assert ok and vision_calls == []


def test_delivery_projects_abstract_but_archive_keeps_fulltext(storage):
    from api.articles_view import serialize_feed_article, serialize_article_list_item
    from api.routers.subscriptions import _atom_feed_xml
    from api.routers.articles import export_feed_articles_markdown
    from mcp_server import _get_article_impl, _get_rag_context_impl, _search_articles_impl
    seed(storage)
    task = pf.claim_one(storage.engine, now=100)
    pf.finish(storage.engine, task, extraction=extracted(), now=101)
    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, "paper")
        assert serialize_feed_article(article)["content"] == ABSTRACT
        assert FULL_ONLY in serialize_article_list_item(article)["content"]
        listed = serialize_article_list_item(article, include_content=False)
        assert listed["paper"]["content_level"] == "fulltext"
        assert "abstract" not in listed["paper"] and "extensions_json" not in listed
        for include_content in (True, False):
            atom = _atom_feed_xml("reader", [article], include_content).decode()
            assert ABSTRACT in atom and FULL_ONLY not in atom
        markdown = export_feed_articles_markdown(session=session).body.decode()
        assert ABSTRACT in markdown and FULL_ONLY not in markdown
    assert _get_article_impl(storage, "paper")["content"] == ABSTRACT
    search = asyncio.run(_search_articles_impl(storage, FULL_ONLY))
    assert len(search) == 1 and search[0]["summary"] == ABSTRACT
    context = asyncio.run(_get_rag_context_impl(storage, FULL_ONLY))
    assert ABSTRACT in context and FULL_ONLY not in context


def test_worker_does_not_prefetch_paper_figures(monkeypatch):
    from api import app as api

    async def completed(_):
        return ["paper"]

    def unexpected(_):
        pytest.fail("Paper enrichment must not automatically prefetch figures")

    monkeypatch.setattr(pf, "run_batch", completed)
    monkeypatch.setattr(api, "schedule_media_prefetch", unexpected)
    asyncio.run(api.execute_paper_fulltext_job())


def test_archive_sync_keeps_paper_fulltext_and_abstract_analysis(storage, tmp_path):
    from models.db import ArticleAnalysisRecord
    from services import archive_sync_v2 as sync

    seed(storage)
    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, "paper")
        session.add(ArticleAnalysisRecord(
            article_id=article.id, status="succeeded", tagging_status="succeeded",
            content_hash=compute_content_hash(article), analysis_basis="paper_abstract",
            quality_score=6.0, created_at="2026-01-02", updated_at="2026-01-02",
        ))
        session.commit()
    receiver = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'receiver.db'}")
    try:
        # Import the original abstract and its score before enrichment.
        for stream in ("articles", "analyses"):
            sync.import_page(receiver.engine, sync.export_page(storage.engine, stream), expected_stream=stream)
        task = pf.claim_one(storage.engine, now=100)
        assert pf.finish(storage.engine, task, extraction=extracted(), now=101)
        sync.import_page(receiver.engine, sync.export_page(storage.engine, "articles"), expected_stream="articles")
        with Session(receiver.engine) as session:
            article = session.get(ArticleRecord, "paper")
            analysis = session.get(ArticleAnalysisRecord, "paper")
            assert FULL_ONLY in article.content and analysis_body(article) == ABSTRACT
            assert analysis.quality_score == 6.0 and analysis.content_hash == compute_content_hash(article)
            assert analysis.analysis_basis == "paper_abstract"
            assert session.get(PaperFulltextJobRecord, "paper") is None
            assert not pf.enqueue(session, article)
    finally:
        receiver.engine.dispose()


def test_article_sync_byte_pages_continue_and_allow_one_large_tex_row(storage):
    from services import archive_sync_v2 as sync
    for index in range(3):
        seed(storage, article_id=f"paper-{index}")
        with Session(storage.engine) as session:
            article = session.get(ArticleRecord, f"paper-{index}")
            article.content = (r"\alpha|" * 28000 if index == 1 else "body " * 1000)
            session.add(article)
            session.commit()
    seen, after, snapshot, sizes = [], "", "", []
    for _ in range(5):
        page = sync.export_page(storage.engine, "articles", after=after, snapshot=snapshot,
                                limit=1000, article_page_max_bytes=8192)
        # The existing receiver honors manifest.complete, not count == limit.
        manifest, rows = sync.parse_page(page, expected_stream="articles", requested_limit=1000)
        seen.extend(row["identity"] for row in rows)
        sizes.append(len(page.encode()))
        assert len(rows) == 1
        snapshot = manifest["snapshot"]
        if manifest["complete"]:
            break
        assert manifest["next_cursor"] != after
        after = manifest["next_cursor"]
    assert sorted(seen) == ["paper-0", "paper-1", "paper-2"]
    assert sizes[0] <= 8192 and sizes[1] > 200000 and sizes[2] <= 8192

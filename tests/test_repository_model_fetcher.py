import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.repository_model_fetcher import (
    DeepSeekGitHubRepositoriesFetcher,
    GenericGitHubRepositoriesFetcher,
)


FAKE_REPOS = [
    {
        "id": 1,
        "name": "no-desc-repo",
        "full_name": "acme/no-desc-repo",
        "description": "",
        "html_url": "https://github.com/acme/no-desc-repo",
        "created_at": "2026-05-01T00:00:00Z",
        "stargazers_count": 10,
        "default_branch": "main",
    },
    {
        "id": 2,
        "name": "has-desc-repo",
        "full_name": "acme/has-desc-repo",
        "description": "A real one-line description.",
        "html_url": "https://github.com/acme/has-desc-repo",
        "created_at": "2026-04-01T00:00:00Z",
        "stargazers_count": 5,
        "default_branch": "main",
    },
]


def _make_fetcher():
    fetcher = GenericGitHubRepositoriesFetcher()

    async def fake_fetch_repositories(client, owner, limit):
        return list(FAKE_REPOS)

    readme_calls = []

    async def fake_fetch_readme(client, owner, repo_name, max_chars):
        readme_calls.append(repo_name)
        return f"Readme body for {repo_name}."

    fetcher._fetch_repositories = fake_fetch_repositories
    fetcher._fetch_readme = fake_fetch_readme
    return fetcher, readme_calls


def test_clean_readme_strips_markdown_noise_and_truncates():
    fetcher = GenericGitHubRepositoriesFetcher()
    raw = (
        "# Awesome Repo\n"
        "<!-- a hidden comment -->\n"
        "[![CI](https://img.shields.io/badge/ci.svg)](https://ci.example/build)\n"
        "![logo](https://example/logo.png)\n"
        "\n"
        "A **curated** list with a [link](https://example.com/page) inside.\n"
        "\n"
        "---\n"
        "| Tool | Description |\n"
        "| --- | --- |\n"
        "| AstrBot | An `agent` assistant. |\n"
    )
    cleaned = fetcher._clean_readme(raw, max_chars=2000)

    # 标题井号、HTML 注释、徽章/图片、分隔线、表格分隔行都被剥除。
    assert "Awesome Repo" in cleaned
    assert "hidden comment" not in cleaned
    assert "img.shields.io" not in cleaned
    assert "logo.png" not in cleaned
    assert "---" not in cleaned
    # 链接→纯文本，加粗/行内代码标记被去掉。
    assert "A curated list with a link inside." in cleaned
    assert "**" not in cleaned and "`" not in cleaned
    assert "https://example.com/page" not in cleaned
    # 表格内容行用 " · " 串联。
    assert "Tool · Description" in cleaned
    assert "AstrBot · An agent assistant." in cleaned

    # 截断按行边界并追加省略号。
    short = fetcher._clean_readme("first line\nsecond line\nthird line", max_chars=15)
    assert short.endswith("…")
    assert "third line" not in short


def test_github_repos_enrich_readme_only_when_description_empty():
    fetcher, readme_calls = _make_fetcher()

    async def collect():
        return [item async for item in fetcher._run(None, owner="acme", source_id="github_acme", fetch_readme=True)]

    items = asyncio.run(collect())

    assert len(items) == 2
    by_title = {item.title: item for item in items}

    # 描述为空 → 拉 README 并拼进正文。
    no_desc = by_title["acme/no-desc-repo"]
    assert "Readme excerpt:" in no_desc.content
    assert "Readme body for no-desc-repo." in no_desc.content
    assert no_desc.raw_data["readme_chars"] > 0

    # 有描述 → 不触发 README 请求。
    has_desc = by_title["acme/has-desc-repo"]
    assert "Readme excerpt:" not in has_desc.content
    assert has_desc.raw_data["readme_chars"] == 0

    # README 仅对描述为空的那个仓库请求了一次。
    assert readme_calls == ["no-desc-repo"]


def test_github_repos_skip_readme_when_toggle_off():
    fetcher, readme_calls = _make_fetcher()

    async def collect():
        return [item async for item in fetcher._run(None, owner="acme", source_id="github_acme", fetch_readme=False)]

    items = asyncio.run(collect())

    assert all("Readme excerpt:" not in item.content for item in items)
    assert readme_calls == []


def test_github_repos_skip_readme_for_already_stored_via_dedup():
    fetcher, readme_calls = _make_fetcher()

    stored_id = fetcher._repo_id("github_acme", FAKE_REPOS[0])

    async def fake_dedup_lookup(ids):
        # 描述为空的仓库已入库且有正文 → 不应再拉 README。
        return {item_id: True for item_id in ids if item_id == stored_id}

    fetcher.dedup_lookup = fake_dedup_lookup

    async def collect():
        return [item async for item in fetcher._run(None, owner="acme", source_id="github_acme", fetch_readme=True)]

    items = asyncio.run(collect())
    by_title = {item.title: item for item in items}

    assert "Readme excerpt:" not in by_title["acme/no-desc-repo"].content
    assert readme_calls == []


def test_deepseek_repos_schema_is_limit_only_with_fixed_readme_defaults():
    # 参数固化波:抓取偏好固化为类默认,schema 只剩 limit;README 补充恒开且对齐全文硬上限。
    schema_fields = {entry["field"] for entry in DeepSeekGitHubRepositoriesFetcher.get_parameter_schema()}
    assert schema_fields == {"limit"}
    assert DeepSeekGitHubRepositoriesFetcher.default_fetch_readme is True
    from fetchers.impl.article_extractor import DETAIL_HARD_CAP

    # 断言「对齐全文硬上限」这一语义本身,而非其具体取值(取值随上限调整漂移)
    assert DeepSeekGitHubRepositoriesFetcher.default_readme_max_chars == DETAIL_HARD_CAP

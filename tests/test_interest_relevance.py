"""Weak primary labels must not widen public-interest reading or brief pools."""
import pytest
from sqlmodel import Session, select

from tests.test_custom_interests import store, add_article
from tests.test_reader_interests import _seed_tag, _assign
from models.db import ArticleRecord, ArticleTagAssignmentRecord
from services import reader_interests as reader, personal_digest as digest
from services.interest_relevance import public_interest_qualifies, public_interest_predicate


@pytest.mark.parametrize('value,expected', [(None,False),('',False),(True,False),('invalid',False),
    (float('nan'),False),(float('inf'),False),(1.1,False),(0.35,False),(0.79,False),(0.8,True),(0.9,True),(1,True)])
def test_numeric_gate(value, expected):
    assert public_interest_qualifies(value) is expected


def test_primary_is_only_display_and_all_consumers_agree(store):
    tag = _seed_tag(store.engine, 'topic.ai', 'AI')
    expected = []
    with Session(store.engine) as s:
        for n, (primary, relevance) in enumerate([(True,0.35),(True,0.79),(True,0.8),(False,0.9)]):
            a = add_article(s, f'real-shape-{n}')
            aid = a.id
            _assign(store.engine, aid, tag, primary=primary, relevance=relevance)
            if relevance >= 0.8: expected.append(aid)
        before = [r.model_dump() for r in s.exec(select(ArticleTagAssignmentRecord)).all()]
        rows = s.exec(select(ArticleRecord).where(reader.interest_filter_condition([tag]))).all()
        assert sorted(a.id for a in rows) == expected
        assert {r.article_id for r in s.exec(select(ArticleTagAssignmentRecord).where(public_interest_predicate())).all()} == set(expected)
        snapshots = {r['article_id']: [{'id':tag, 'code':'topic.ai', 'is_primary':r['is_primary'], 'relevance':r['relevance']}]
                     for r in before}
        mapping = reader.InterestMap(followed={tag:'AI'})
        for aid, tags in snapshots.items():
            assert bool(reader.interest_matches(tags, mapping)) == (aid in expected)
            assert bool(digest._qualifying_interest_codes(snapshots, [aid])[aid]) == (aid in expected)
        assert [r.model_dump() for r in s.exec(select(ArticleTagAssignmentRecord)).all()] == before

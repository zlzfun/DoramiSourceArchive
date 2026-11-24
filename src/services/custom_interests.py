"""Private compound interests; bounded, recoverable and locally evaluated.

The lexical fallback is local. Authorized semantic scans use durable paid fences.
Conservative title matches are core; a condition
only mentioned in a body is not promoted to core by confidence or word count.
Current publication pointers, content fingerprints and reader ACLs are checked
again when results are consumed, so stale projections cannot widen a brief.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
import unicodedata
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, select

from models.custom_interests import (
    UserInterestConditionRecord as Condition, UserInterestStateRecord as State,
    InterestArticleMatchRecord as Match, InterestMatchJobRecord as Job,
    InterestSemanticScanRecord as Scan, InterestSemanticRequestRecord as Request,
)
from models.db import (
    AppSettingRecord, ArticleRecord, ArticleAnalysisRecord, ArticleTagAssignmentRecord,
    PodcastTextArtifactRecord, PodcastTextPublicationRecord, UserRecord,
)
from services import source_visibility, user_sources
from services.article_time import in_time_window
from services import interest_match_scope as match_scope

MATCHER_VERSION = 'compound-semantic-scan-v2'
MATCHING_ENABLED_KEY = 'custom_interest_matching_enabled'
MAX_CONDITIONS = 30
MAX_PAGE = 200
UTC = dt.timezone.utc
EVIDENCE_BINDING_VERSION = 2
# Explicit contract: additions to the archive require a binding audit.
EVIDENCE_ARTICLE_FIELDS = frozenset({
    'id', 'title', 'content_type', 'source_id', 'source_url', 'publish_date',
    'fetched_date', 'fetch_run_id', 'job_id', 'job_run_id', 'source_group_id',
    'run_scope', 'has_content', 'content', 'extensions_json', 'analysis_authority_id',
})


def evidence_binding_version(evidence):
    if not isinstance(evidence, dict):
        return None
    version = evidence.get('evidence_binding_version', 1)
    return version if type(version) is int and version in (1, 2) else None


def _bound_article(article, version):
    if version == 1:
        return {key: getattr(article, key) for key in (
            'title', 'content', 'extensions_json', 'source_id', 'publish_date',
            'archive_updated_at', 'analysis_authority_id')}
    from models.db import ARTICLE_ARCHIVE_FIELDS
    if EVIDENCE_ARTICLE_FIELDS != ARTICLE_ARCHIVE_FIELDS | {'id', 'analysis_authority_id'}:
        raise ValueError('article archive changed; audit evidence binding')
    values = {key: getattr(article, key) for key in EVIDENCE_ARTICLE_FIELDS}
    raw = article.extensions_json
    try:
        parsed = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, TypeError):
        parsed = None
    # Preserve malformed/non-object bytes and SQL NULL, rather than erase them.
    values['extensions_json'] = ({'object': {k: v for k, v in parsed.items()
        if k not in {'translation_zh_title', 'translation_zh_title_fp'}}}
        if isinstance(parsed, dict) else {'opaque': raw})
    return values


def bound_evidence_snapshot(session, article, evidence, *, cache=None, assets=None):
    version = evidence_binding_version(evidence)
    if version is None:
        return '', '', None
    cache = cache if cache is not None else {}
    key = (article.id, version)
    if key not in cache:
        cache[key] = evidence_snapshot(session, article, assets=assets, binding_version=version)
    return cache[key]


def _now():
    return dt.datetime.now(UTC)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def normalize(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


class ConceptGroup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    any_of: list[str] = Field(min_length=1, max_length=8)

    @field_validator('any_of')
    @classmethod
    def terms(cls, values):
        return validate_terms(values)


def validate_terms(values):
    result = []
    for raw in values:
        value = normalize(raw)
        if not 2 <= len(value) <= 80 or any(ord(c) < 32 for c in raw):
            raise ValueError('每个词组须为 2–80 个字符')
        if value not in result:
            result.append(value)
    return result


class SemanticIntent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1]
    requirement: str = Field(min_length=1, max_length=1000)
    negative_examples: list[str] = Field(default_factory=list, max_length=6)

    @field_validator('version', mode='before')
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError('语义版本须为整数')
        return value

    @field_validator('requirement', mode='before')
    @classmethod
    def requirement_text(cls, value):
        return cls.clean_prose(value, 1000)

    @field_validator('negative_examples')
    @classmethod
    def examples(cls, values):
        return [cls.clean_prose(value, 500) for value in values]

    @staticmethod
    def clean_prose(value, limit):
        if not isinstance(value, str) or any(
                unicodedata.category(char) == 'Cc' and char not in '\n\r' for char in value):
            raise ValueError('语义说明须为普通文本')
        value = value.strip()
        if not 1 <= len(value) <= limit:
            raise ValueError(f'语义文本须为 1–{limit} 个字符')
        return value


class ConditionAST(BaseModel):
    model_config = ConfigDict(extra='forbid')
    all_of: list[ConceptGroup] = Field(max_length=6)
    exclude: list[str] = Field(default_factory=list, max_length=20)
    semantic_intent: SemanticIntent | None = None
    match_mode: Literal['topic'] | None = None

    @model_validator(mode='after')
    def valid_mode(self):
        if self.match_mode == 'topic':
            if self.all_of or self.semantic_intent is None:
                raise ValueError('话题匹配须填写匹配说明，不使用条件组')
        elif not self.all_of:
            raise ValueError('至少填写一个条件组')
        return self

    @field_validator('exclude')
    @classmethod
    def exclusions(cls, values):
        return validate_terms(values)


class ConditionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=500)
    condition: ConditionAST
    status: Literal['active', 'paused'] = 'active'
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = ' '.join(value.split())
        if not value:
            raise ValueError('兴趣名称不能为空')
        return value


class RevisionConflict(ValueError):
    pass


def lock_mutation(session: Session):
    if session.bind.dialect.name == 'sqlite':
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')


def state_revision(session: Session, username: str) -> int:
    state = session.get(State, username)
    return int(state.revision) if state else 0


def bump_revision(session: Session, username: str) -> int:
    """Atomic increment in the caller's mutation transaction (never commits)."""
    stamp = _now().isoformat()
    session.exec(insert(State).values(owner_username=username, revision=1, updated_at=stamp)
        .on_conflict_do_update(index_elements=['owner_username'], set_={
            'revision': State.revision + 1, 'updated_at': stamp,
        }))
    session.flush()
    session.expire_all()
    return state_revision(session, username)


def list_conditions(session: Session, username: str, *, active_only=False):
    query = select(Condition).where(Condition.owner_username == username)
    query = query.where(Condition.status == 'active' if active_only else Condition.status != 'deleted')
    return session.exec(query.order_by(Condition.id)).all()


def payload(row: Condition):
    return {'id': row.id, 'name': row.name, 'description': row.description,
            'condition': json.loads(row.condition_json), 'revision': row.revision,
            'schema_version': row.schema_version, 'status': row.status, 'updated_at': row.updated_at}


def save_condition(session: Session, username: str, body: ConditionInput, condition_id=None):
    lock_mutation(session)
    stamp = _now().isoformat()
    definition = body.condition.model_dump(exclude_none=True)
    schema_version = 3 if body.condition.match_mode == 'topic' else (2 if body.condition.semantic_intent is not None else 1)
    encoded = _json(definition)
    if condition_id is None:
        if len(list_conditions(session, username)) >= MAX_CONDITIONS:
            raise ValueError('最多保存 30 项自定义兴趣')
        row = Condition(owner_username=username, name=body.name, description=body.description,
                        condition_json=encoded, schema_version=schema_version,
                        status=body.status, created_at=stamp, updated_at=stamp)
        session.add(row)
        session.flush()
    else:
        row = session.get(Condition, condition_id)
        if row is None or row.owner_username != username or row.status == 'deleted':
            raise LookupError('自定义兴趣不存在')
        if body.expected_revision != row.revision:
            raise RevisionConflict('兴趣已在其他页面更新，请刷新后重试')
        if (json.loads(row.condition_json).get('match_mode') == 'topic'
                and 'match_mode' not in body.condition.model_fields_set):
            raise ValueError('请使用支持话题匹配的编辑器，或显式选择条件组匹配')
        if 'semantic_intent' not in body.condition.model_fields_set:
            previous_intent = json.loads(row.condition_json).get('semantic_intent')
            if previous_intent is not None:
                definition['semantic_intent'] = SemanticIntent.model_validate(previous_intent).model_dump()
                encoded, schema_version = _json(definition), 2
        if (row.name, row.description, row.condition_json, row.status, row.schema_version) == (
                body.name, body.description, encoded, body.status, schema_version):
            return row
        result = session.exec(update(Condition).where(
            Condition.id == condition_id, Condition.owner_username == username,
            Condition.revision == body.expected_revision, Condition.status != 'deleted',
        ).values(name=body.name, description=body.description, condition_json=encoded,
                 schema_version=schema_version, status=body.status,
                 revision=Condition.revision + 1, updated_at=stamp))
        if result.rowcount != 1:
            raise RevisionConflict('兴趣已在其他页面更新，请刷新后重试')
        session.expire(row)
    bump_revision(session, username)
    job = session.get(Job, row.id)
    if job is None:
        job = Job(condition_id=row.id, condition_revision=row.revision, cycle_cutoff_at=stamp,
                  next_attempt_at=stamp, updated_at=stamp)
    else:
        job.condition_revision = row.revision
        job.cursor = ''
        job.cycle_cutoff_at = stamp
        job.next_attempt_at = stamp
        job.lease_token = ''
        job.lease_expires_at = ''
        job.last_error = ''
        job.updated_at = stamp
    session.add(job)
    session.exec(update(Scan).where(Scan.condition_id == row.id,
        Scan.status.in_(['deferred','partial']),
        or_(Scan.condition_revision != row.revision, row.status != 'active'),
    ).values(status='superseded', updated_at=stamp))
    return row


def delete_condition(session: Session, username: str, condition_id: int, expected_revision: int):
    lock_mutation(session)
    row = session.get(Condition, condition_id)
    if row is None or row.owner_username != username or row.status == 'deleted':
        raise LookupError('自定义兴趣不存在')
    result = session.exec(update(Condition).where(
        Condition.id == condition_id, Condition.owner_username == username,
        Condition.revision == expected_revision, Condition.status != 'deleted',
    ).values(status='deleted', revision=Condition.revision + 1, updated_at=_now().isoformat()))
    if result.rowcount != 1:
        raise RevisionConflict('兴趣已在其他页面更新，请刷新后重试')
    session.exec(update(Scan).where(Scan.condition_id == condition_id,
        Scan.status.in_(['deferred','partial'])).values(status='superseded', updated_at=_now().isoformat()))
    bump_revision(session, username)


def readable_source_ids(session: Session, username: str, source_ids):
    sources = {str(s) for s in source_ids if s}
    sources -= source_visibility.reader_unavailable_source_ids(session)
    sources -= set(user_sources.unauthorized_user_source_ids(session, username, list(sources)))
    sources.discard('dorami_daily_brief')
    return sources


def all_readable_source_ids(session: Session, username: str):
    # Source identifiers carry no article body; apply the ACL before retrieving
    # any per-reader content or published transcript.
    sources = session.exec(select(ArticleRecord.source_id).distinct()).all()
    return readable_source_ids(session, username, sources)


def _contains(text: str, term: str):
    # Latin short words must not match substrings (AI != said, Apple != pineapple).
    if re.fullmatch(r'[a-z0-9][a-z0-9 _+./-]*', term):
        return re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text) is not None
    return term in text


def evidence_snapshot(session: Session, article: ArticleRecord, *, assets=None, binding_version=1):
    # Default preserves exact legacy preimages; new writers opt in explicitly.
    if type(binding_version) is not int or binding_version not in (1, 2):
        raise ValueError('unsupported evidence binding version')
    analysis = assets['analyses'].get(article.id) if assets is not None else session.get(ArticleAnalysisRecord, article.id)
    assignments = assets['assignments'].get(article.id, []) if assets is not None else session.exec(select(ArticleTagAssignmentRecord).where(
        ArticleTagAssignmentRecord.article_id == article.id).order_by(ArticleTagAssignmentRecord.id)).all()
    text = article.content or ''
    publications = []
    if article.content_type == 'podcast_episode':
        # Only original, currently published full transcript; no description proxy.
        rows = assets['publications'].get(article.id, []) if assets is not None else session.exec(select(PodcastTextPublicationRecord, PodcastTextArtifactRecord).join(
            PodcastTextArtifactRecord,
            (PodcastTextArtifactRecord.id == PodcastTextPublicationRecord.artifact_id)
            & (PodcastTextArtifactRecord.episode_id == PodcastTextPublicationRecord.episode_id)
            & (PodcastTextArtifactRecord.kind == PodcastTextPublicationRecord.kind)
            & (PodcastTextArtifactRecord.authority_id == PodcastTextPublicationRecord.authority_id),
        ).where(PodcastTextPublicationRecord.episode_id == article.id,
                PodcastTextPublicationRecord.status == 'published',
                PodcastTextPublicationRecord.published_at.is_not(None),
                PodcastTextPublicationRecord.kind.in_(['publisher_transcript','normalized_transcript']))).all()
        text = ''
        from config import PodcastConfig
        from services.podcast_text_reader import _plain_text, PodcastTextReaderError
        for pub, artifact in rows:
            publications.append((pub.model_dump(), artifact.content_hash))
            if hashlib.sha256(artifact.inline_text.encode()).hexdigest() != artifact.content_hash:
                continue
            try:
                text += '\n' + _plain_text(artifact, PodcastConfig(), for_reading=True)
            except (PodcastTextReaderError, ValueError, TypeError, UnicodeError, RecursionError):
                pass
    fingerprint = hashlib.sha256(_json({
        **({'evidence_binding_version': binding_version} if binding_version == 2 else {}),
        'article': _bound_article(article, binding_version),
        'analysis': analysis.model_dump() if analysis else None,
        'assignments': [row.model_dump() for row in assignments],
        'publications': publications, 'matcher': MATCHER_VERSION,
        'source_metadata': assets['sources'][article.source_id] if assets is not None else _source_metadata(session, article),
    }).encode()).hexdigest()
    return fingerprint, text, analysis


def evaluate(condition: ConditionAST, title: str, body: str, *, usable=True):
    if not usable:
        return 'unknown', {'reason': 'semantic_intent_requires_verification'
            if condition.semantic_intent is not None else '尚无可用全文证据', 'groups': []}
    title, body = normalize(title), normalize(body)
    whole = title + '\n' + body
    excluded = [term for term in condition.exclude if _contains(whole, term)]
    hits = [[term for term in group.any_of if _contains(whole, term)] for group in condition.all_of]
    title_hits = [[term for term in group.any_of if _contains(title, term)] for group in condition.all_of]
    if (condition.match_mode == 'topic' or condition.semantic_intent is not None) and not excluded:
        outcome, reason = 'unknown', 'semantic_intent_requires_verification'
    elif excluded or not all(hits):
        outcome, reason = 'no_match', '包含排除项' if excluded else '未同时满足全部条件'
    elif all(title_hits):
        outcome, reason = 'core', '标题同时包含全部条件组'
    else:
        outcome, reason = 'mention', '全文提及，尚不能确定为核心议题'
    return outcome, {'reason': reason, 'groups': hits, 'title_groups': title_hits, 'excluded': excluded}


def evidence_assets(session: Session, articles):
    """Batch the existing fingerprint inputs without changing their serialization."""
    assets = {'analyses': {}, 'assignments': {}, 'publications': {}, 'sources': {}}
    ids = list(dict.fromkeys(a.id for a in articles))
    for offset in range(0, len(ids), 400):
        chunk = ids[offset:offset + 400]
        for row in session.exec(select(ArticleAnalysisRecord).where(ArticleAnalysisRecord.article_id.in_(chunk))).all():
            assets['analyses'][row.article_id] = row
        for row in session.exec(select(ArticleTagAssignmentRecord).where(
                ArticleTagAssignmentRecord.article_id.in_(chunk)).order_by(ArticleTagAssignmentRecord.id)).all():
            assets['assignments'].setdefault(row.article_id, []).append(row)
        rows = session.exec(select(PodcastTextPublicationRecord, PodcastTextArtifactRecord).join(
            PodcastTextArtifactRecord,
            (PodcastTextArtifactRecord.id == PodcastTextPublicationRecord.artifact_id)
            & (PodcastTextArtifactRecord.episode_id == PodcastTextPublicationRecord.episode_id)
            & (PodcastTextArtifactRecord.kind == PodcastTextPublicationRecord.kind)
            & (PodcastTextArtifactRecord.authority_id == PodcastTextPublicationRecord.authority_id),
        ).where(PodcastTextPublicationRecord.episode_id.in_(chunk),
                PodcastTextPublicationRecord.status == 'published',
                PodcastTextPublicationRecord.published_at.is_not(None),
                PodcastTextPublicationRecord.kind.in_(['publisher_transcript', 'normalized_transcript']))).all()
        for pub, artifact in rows:
            assets['publications'].setdefault(pub.episode_id, []).append((pub, artifact))
    for article in articles:
        if article.source_id not in assets['sources']:
            assets['sources'][article.source_id] = _source_metadata(session, article)
    return assets


def current_matches(session: Session, username: str, *, condition_ids=None, article_ids=None, limit=2000, snapshot_cache=None, order_by_id=False):
    from services.interest_governance_llm import current_verifier_policy, valid_verifier_policy
    policy = current_verifier_policy(session)
    query = select(Match, Condition, ArticleRecord).join(Condition, Condition.id == Match.condition_id).join(
        ArticleRecord, ArticleRecord.id == Match.article_id).where(
            Match.owner_username == username, Condition.owner_username == username,
            Condition.status == 'active', Match.condition_revision == Condition.revision,
            Match.matcher_version == MATCHER_VERSION, Match.outcome == 'core',
            ArticleRecord.source_id.in_(all_readable_source_ids(session, username)))
    if condition_ids is not None:
        query = query.where(Condition.id.in_(list(condition_ids)))
    if article_ids is not None:
        query = query.where(ArticleRecord.id.in_(list(article_ids)))
    ordering = (Match.id,) if order_by_id else (Match.updated_at.desc(), Match.id)
    rows = session.exec(query.order_by(*ordering)).all()
    cache = snapshot_cache if snapshot_cache is not None else {}
    articles = {article.id: article for match, _, article in rows
                if (article.id, evidence_binding_version(_decode(match.evidence_json))) not in cache}
    assets = evidence_assets(session, list(articles.values())) if articles else None
    semantic_sources = {}
    allowed = readable_source_ids(session, username, [article.source_id for _,_,article in rows])
    result = []
    for match, condition, article in rows:
        if article.source_id not in allowed:
            continue
        try:
            evidence = json.loads(match.evidence_json)
            intent = json.loads(condition.condition_json).get('semantic_intent')
            if not isinstance(evidence, dict):
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        if intent is not None:
            from services.interest_evidence_windows import semantic_intent_hash
            assessment = evidence.get('intent_assessment') or {}
            if (not isinstance(assessment, dict) or evidence.get('method') != 'semantic'
                    or not valid_verifier_policy(evidence.get('verifier_policy'))
                    or evidence['verifier_policy'] != policy
                    or evidence.get('semantic_intent_version') != intent.get('version')
                    or evidence.get('semantic_intent_hash') != semantic_intent_hash(intent)
                    or assessment.get('verdict') != 'satisfied'
                    or not assessment.get('evidence_span_ids')
                    or assessment.get('negative_example_indexes') != []):
                continue
        if evidence.get('method') == 'semantic':
            if article.source_id not in semantic_sources:
                semantic_sources[article.source_id] = _semantic_allowed(session, username, article.source_id)
            if not semantic_sources[article.source_id]:
                continue
        fingerprint, _, _ = bound_evidence_snapshot(session, article, evidence, cache=cache, assets=assets)
        if fingerprint and match.fingerprint == fingerprint:
            result.append((match, condition, article))
    return result if limit is None else result[:limit]


def preview(session: Session, username: str, condition: ConditionAST, *, limit=100):
    now = _now()
    rows = session.exec(select(ArticleRecord).where(
        ArticleRecord.source_id.in_(all_readable_source_ids(session, username)),
        ArticleRecord.publish_date >= (now-dt.timedelta(days=8)).date().isoformat(),
        ArticleRecord.publish_date < (now+dt.timedelta(days=1)).date().isoformat(),
    ).order_by(ArticleRecord.fetched_date.desc(),ArticleRecord.id).limit(min(limit, MAX_PAGE))).all()
    allowed = readable_source_ids(session, username, [a.source_id for a in rows])
    result, candidates = [], []
    for article in rows:
        if article.source_id not in allowed or not in_time_window(article.publish_date,start=now-dt.timedelta(days=7),end=now):
            continue
        _, text, analysis = evidence_snapshot(session, article)
        outcome, evidence = evaluate(condition,article.title,text,usable=bool(text.strip()) and bool(analysis))
        if outcome in {'core','mention'}:
            result.append({'article_id':article.id,'title':article.title,'source_id':article.source_id,
                           'outcome':outcome,'evidence':evidence})
        elif condition.match_mode == 'topic':
            # These are pending semantic inputs, never positive preview results.
            if text.strip() and analysis and not evidence.get('excluded'):
                candidates.append({'article_id':article.id,'title':article.title,'source_id':article.source_id,
                                   'outcome':'unknown','evidence':evidence})
        elif condition.semantic_intent is not None:
            lexical, _ = evaluate(condition.model_copy(update={'semantic_intent':None}),
                article.title,text,usable=bool(text.strip()) and bool(analysis))
            if lexical in {'core','mention'}:
                candidates.append({'article_id':article.id,'title':article.title,'source_id':article.source_id,
                                   'outcome':'unknown','evidence':evidence})
    return {'items':result[:20], 'lexical_candidates':candidates[:20],
            'status':'semantic_verification_required' if condition.semantic_intent is not None else 'completed',
            'scanned':len(rows), 'scan_limit':min(limit,MAX_PAGE), 'matcher_version':MATCHER_VERSION}


def _semantic_allowed(session, username, source_id):
    from services import accounts
    user = session.get(UserRecord, username)
    return bool(user and user.is_active and user.ai_beta_enabled
                and accounts.ai_beta_global_enabled(session)
                and source_id in readable_source_ids(session, username, [source_id])
                and source_id in user_sources.external_ai_allowed_source_ids(session, [source_id]))


def _run_lexical_cycle(engine, *, limit=MAX_PAGE, now=None, candidate_pairs=None, scoped_batch=None):
    """Local fallback preserves lexical behavior without provider calls."""
    started = time.monotonic()
    now = now or _now()
    clock = lambda: now + dt.timedelta(seconds=time.monotonic()-started)
    stamp = now.isoformat()
    token = uuid.uuid4().hex
    with Session(engine) as session:
        enabled = session.get(AppSettingRecord, MATCHING_ENABLED_KEY)
        if enabled and enabled.value.strip().casefold() in {'0','false','off','no'}:
            return (match_scope.batch_report(engine, scoped_batch, processed=set(), calls=0, status='disabled', error_code='matching_disabled')
                    if scoped_batch else {'status':'disabled','processed':0})
        if scoped_batch is not None:
            lock_mutation(session)
            scoped_batch.check_all(session, clock())
        due = session.exec(select(Job).join(Condition,Condition.id==Job.condition_id).join(
            UserRecord,UserRecord.username==Condition.owner_username).where(
            Condition.status=='active',UserRecord.is_active.is_(True),
            Job.next_attempt_at<=stamp if scoped_batch is None else True,
            or_(Job.lease_token=='',Job.lease_expires_at<=stamp),
        ).where(Job.condition_id.in_(candidate_pairs) if candidate_pairs is not None else True)
        .order_by(Job.next_attempt_at,Job.condition_id).limit(1)).first()
        if due is None:
            return (match_scope.batch_report(engine, scoped_batch, processed=set(), calls=0, status='busy', error_code='job_busy')
                    if scoped_batch else {'status':'idle','processed':0})
        result = session.exec(update(Job).where(Job.condition_id==due.condition_id,
            or_(Job.lease_token=='',Job.lease_expires_at<=stamp)).values(
                lease_token=token,lease_expires_at=(now+dt.timedelta(minutes=5)).isoformat()))
        if result.rowcount!=1:
            session.rollback()
            return (match_scope.batch_report(engine, scoped_batch, processed=set(), calls=0, status='busy', error_code='job_busy')
                    if scoped_batch else {'status':'busy','processed':0})
        condition_id=due.condition_id
        session.commit()
    try:
        evaluated=[]
        with Session(engine) as session:
            job=session.get(Job,condition_id)
            condition=session.get(Condition,condition_id)
            if not job or job.lease_token!=token or not condition or condition.status!='active' or job.condition_revision!=condition.revision:
                return (match_scope.batch_report(engine, scoped_batch, processed=set(), calls=0, status='superseded', error_code='job_superseded')
                        if scoped_batch else {'status':'superseded','processed':0})
            revision, username = condition.revision, condition.owner_username
            ast=ConditionAST.model_validate_json(condition.condition_json)
            cutoff = scoped_batch.scope.publish_before if scoped_batch else dt.datetime.fromisoformat(job.cycle_cutoff_at)
            if scoped_batch is not None:
                rows = scoped_batch.check_all(session, clock())
            else:
                rows=session.exec(select(ArticleRecord).where(
                    ArticleRecord.source_id.in_(all_readable_source_ids(session, username)),
                    ArticleRecord.id>job.cursor,
                    ArticleRecord.id.in_(candidate_pairs[condition_id]) if candidate_pairs is not None else True,
                    ArticleRecord.publish_date>=(cutoff-dt.timedelta(days=8)).date().isoformat(),
                    ArticleRecord.publish_date<(cutoff+dt.timedelta(days=1)).date().isoformat(),
                ).order_by(ArticleRecord.id).limit(min(max(1,limit),MAX_PAGE))).all()
            row_count, last_id = len(rows), rows[-1].id if rows else ''
            allowed=readable_source_ids(session,username,[a.source_id for a in rows])
            for article in rows:
                if article.source_id not in allowed or not in_time_window(article.publish_date,start=scoped_batch.scope.publish_after if scoped_batch else cutoff-dt.timedelta(days=7),end=cutoff):
                    continue
                fingerprint,text,analysis=evidence_snapshot(session,article,binding_version=EVIDENCE_BINDING_VERSION)
                old=session.exec(select(Match).where(Match.condition_id==condition_id,Match.article_id==article.id)).first()
                if old and old.condition_revision == revision and old.outcome == 'unknown' and _decode(old.evidence_json).get('method') == 'semantic' and not _decode(old.evidence_json).get('scan_id'):
                    # Preserve unidentifiable pre-upgrade paid unknowns until anchored.
                    continue
                if old and old.condition_revision==revision and old.fingerprint==fingerprint and old.matcher_version==MATCHER_VERSION and _decode(old.evidence_json).get('method') == 'deterministic_title':
                    continue
                outcome,evidence=evaluate(ast,article.title,text,usable=bool(text.strip()) and bool(analysis))
                evidence['method']='deterministic_title'
                evidence['evidence_binding_version']=EVIDENCE_BINDING_VERSION
                evaluated.append({'article_id':article.id,'source_id':article.source_id,'title':article.title,
                    'fingerprint':fingerprint,'outcome':outcome,'evidence':evidence})
        with Session(engine) as session:
            lock_mutation(session)
            job=session.get(Job,condition_id)
            condition=session.get(Condition,condition_id)
            user=session.get(UserRecord,username)
            if not job or job.lease_token!=token or not condition or condition.status!='active' or condition.revision!=revision or not user or not user.is_active:
                session.rollback()
                return (match_scope.batch_report(engine, scoped_batch, processed=set(), calls=0, status='superseded', error_code='job_superseded')
                        if scoped_batch else {'status':'superseded','processed':0})
            if scoped_batch is not None:
                scoped_batch.check_all(session, clock())
            processed=0
            processed_ids=set()
            for item in evaluated:
                article=session.get(ArticleRecord,item['article_id'])
                if not article or article.source_id not in readable_source_ids(session,username,[article.source_id]):
                    continue
                if evidence_snapshot(session,article,binding_version=EVIDENCE_BINDING_VERSION)[0]!=item['fingerprint']:
                    continue
                old=session.exec(select(Match).where(Match.condition_id==condition_id,Match.article_id==article.id)).first()
                match=old or Match(owner_username=username,condition_id=condition_id,article_id=article.id,
                    condition_revision=revision,fingerprint=item['fingerprint'],matcher_version=MATCHER_VERSION,
                    outcome=item['outcome'],updated_at=stamp)
                match.condition_revision=revision
                match.fingerprint=item['fingerprint']
                match.matcher_version=MATCHER_VERSION
                match.outcome=item['outcome']
                match.evidence_json=_json(item['evidence'])
                match.updated_at=stamp
                session.add(match)
                processed+=1
                processed_ids.add(article.id)
            if scoped_batch is not None:
                # Never overwrite the normal scheduler's cursor, cutoff, due/error or updated_at.
                job.lease_token=job.lease_expires_at=''
                session.add(job); session.commit()
                return match_scope.batch_report(engine, scoped_batch, processed=processed_ids, calls=0)
            if row_count<min(max(1,limit),MAX_PAGE):
                job.cursor=''
                job.cycle_cutoff_at=stamp
                job.next_attempt_at=(now+dt.timedelta(minutes=5)).isoformat()
            else:
                job.cursor=last_id
                job.next_attempt_at=stamp
            job.lease_token=''
            job.lease_expires_at=''
            job.last_error=''
            job.updated_at=stamp
            session.add(job)
            session.commit()
            return {'status':'processed','processed':processed,'condition_id':condition_id,'semantic_calls':0}
    except Exception as exc:
        if scoped_batch is not None:
            match_scope.release_lease(engine, condition_id, token)
            return match_scope.batch_report(engine, scoped_batch, processed=set(), calls=0,
                status='expired' if getattr(exc, 'code', '') == 'scope_expired' else 'failed',
                error_code=getattr(exc, 'code', 'matching_failed'))
        with Session(engine) as session:
            session.exec(update(Job).where(Job.condition_id==condition_id,Job.lease_token==token).values(
                lease_token='',lease_expires_at='',last_error='matching_failed',
                next_attempt_at=(now+dt.timedelta(minutes=5)).isoformat(),updated_at=stamp))
            session.commit()
        return {'status':'failed','processed':0,'condition_id':condition_id}

    finally:
        if scoped_batch is not None:
            match_scope.release_lease(engine, condition_id, token)


def _decode(value, default=None):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {} if default is None else default


def _source_metadata(session, article):
    from services.interest_governance_llm import trusted_source_metadata
    return trusted_source_metadata(session, article)


def _paid_identity(condition, article, text, source):
    """No analysis timestamps, matcher/contract version or model configuration."""
    return hashlib.sha256(_json({
        'owner': condition.owner_username, 'condition_id': condition.id,
        'revision': condition.revision, 'ast': _decode(condition.condition_json),
        'name': condition.name, 'description': condition.description,
        'article_id': article.id, 'title': article.title, 'content_type': article.content_type,
        'source_id': article.source_id,
        'projection_hash': hashlib.sha256(text.encode()).hexdigest(), 'source': source,
    }).encode()).hexdigest()


def _available(verifier):
    if verifier is None:
        return False
    try:
        return bool(verifier.available()) if hasattr(verifier, 'available') else True
    except Exception:
        return False


def _union_ranges(ranges, full_chars):
    merged = []
    for start, end in sorted(ranges):
        if not (type(start) is int and type(end) is int and 0 <= start < end <= full_chars):
            raise ValueError('invalid coverage range')
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _range_chars(ranges):
    return sum(end-start for start,end in ranges)


def _put_match(session, condition, article, fingerprint, outcome, evidence, stamp):
    match = session.exec(select(Match).where(
        Match.condition_id == condition.id, Match.article_id == article.id)).first()
    if match is None:
        match = Match(owner_username=condition.owner_username, condition_id=condition.id,
            condition_revision=condition.revision, article_id=article.id,
            fingerprint=fingerprint, matcher_version=MATCHER_VERSION,
            outcome=outcome, updated_at=stamp)
    match.condition_revision = condition.revision
    match.fingerprint = fingerprint
    match.matcher_version = MATCHER_VERSION
    match.outcome = outcome
    match.evidence_json = _json({**evidence, 'evidence_binding_version': EVIDENCE_BINDING_VERSION})
    match.updated_at = stamp
    session.add(match)


def _scan_evidence(scan):
    evidence = _decode(scan.result_json)
    evidence.update(method='semantic', scan_id=scan.id, content_identity=scan.content_identity,
                    contract_version=scan.contract_version, scan_status=scan.status)
    ranges = _decode(scan.reviewed_ranges_json, [])
    evidence['coverage'] = {
        **evidence.get('coverage', {}), 'full_chars':scan.full_chars,
        'reviewed_unique_chars':_range_chars(ranges),
        'remaining_chars':scan.full_chars-_range_chars(ranges),
        'complete':ranges == [[0, scan.full_chars]],
        'complete_after_this_page':ranges == [[0, scan.full_chars]],
    }
    if scan.status != 'succeeded':
        evidence['outcome'] = 'unknown'
        evidence['reason'] = scan.error_code or '语义评估待完成，部分覆盖不能代表全文'
    return evidence


def _unresolved(session, identity):
    return bool(session.exec(select(Request.id).where(
        Request.content_identity == identity, Request.status.in_(['authorized','unknown'])).limit(1)).first()
        or session.exec(select(Scan.id).where(
            Scan.content_identity == identity, Scan.status == 'unknown').limit(1)).first())


def _recover_unknown_requests(session, identity, stamp):
    # Only called while this condition's lease is owned. A previous authorized
    # HTTP may have happened; keeping its hold is safer than guessing/retrying.
    session.exec(update(Request).where(Request.content_identity == identity,
        Request.status == 'authorized').values(status='unknown',
            error_code='authorization_recovered', completed_at=stamp))


def _ensure_scan(session, condition, article, text, source, old, contract, stamp):
    identity = _paid_identity(condition, article, text, source)
    sid = hashlib.sha256(_json([identity, contract]).encode()).hexdigest()
    scan = session.get(Scan, sid)
    if scan is None:
        scan = Scan(id=sid, content_identity=identity, contract_version=contract,
            owner_username=condition.owner_username, condition_id=condition.id,
            condition_revision=condition.revision, article_id=article.id,
            projection_hash=hashlib.sha256(text.encode()).hexdigest(),
            source_metadata_hash=source['metadata_hash'], full_chars=len(text),
            created_at=stamp, updated_at=stamp)
    old_evidence = _decode(old.evidence_json) if old else {}
    # Legacy records cannot prove their original paid body. Anchor once, fail
    # closed on the current content, and never let analysis bookkeeping unlock it.
    legacy = bool(old and old.condition_revision == condition.revision
                  and old.outcome == 'unknown' and old_evidence.get('method') == 'semantic'
                  and not old_evidence.get('scan_id'))
    anchored = session.exec(select(Scan.id).where(
        Scan.condition_id == condition.id, Scan.condition_revision == condition.revision,
        Scan.article_id == article.id, Scan.error_code == 'legacy_semantic_unknown').limit(1)).first()
    if legacy and not anchored:
        scan.status, scan.error_code = 'unknown', 'legacy_semantic_unknown'
    elif _unresolved(session, identity):
        _recover_unknown_requests(session, identity, stamp)
        scan.status, scan.error_code = 'unknown', scan.error_code or 'unresolved_paid_request'
    elif scan.status == 'superseded':
        scan.status = 'partial' if scan.page_count else 'deferred'
        scan.error_code = ''
    session.add(scan)
    session.flush()
    return scan


def _context(session, condition_id, revision, token, stamp):
    enabled = session.get(AppSettingRecord, MATCHING_ENABLED_KEY)
    if enabled and enabled.value.strip().casefold() in {'0','false','off','no'}:
        return None
    job = session.get(Job, condition_id)
    condition = session.get(Condition, condition_id)
    if not (job and job.lease_token == token and job.lease_expires_at > stamp
            and condition and condition.status == 'active' and condition.revision == revision):
        return None
    user = session.get(UserRecord, condition.owner_username)
    if not user or not user.is_active:
        return None
    return job, condition


def _authorize_page(engine, scan_id, condition_id, revision, token, cycle_id, verifier, clock, cap, scoped_batch=None):
    from services.interest_evidence_windows import (build_evidence_page, PROJECTION_VERSION,
        to_span_evidence_payload, encode_evidence_payload)
    if not _available(verifier):
        return None
    from services.interest_governance_llm import valid_verifier_policy, SemanticPolicyPrecheckError
    timeout = min(120, max(1, int(getattr(verifier, 'timeout_seconds', 120))))
    output_limit = getattr(verifier, 'max_output_tokens', None)
    policy = getattr(verifier, 'verifier_policy', None)
    if (type(output_limit) is not int or not 1 <= output_limit <= 8192
            or not valid_verifier_policy(policy) or policy['max_output_tokens'] != output_limit):
        raise SemanticPolicyPrecheckError()
    policy = dict(policy)
    now = clock()
    stamp = now.isoformat()
    with Session(engine) as session:
        lock_mutation(session)
        ctx = _context(session, condition_id, revision, token, stamp)
        scan = session.get(Scan, scan_id)
        if not ctx or not scan or scan.status not in {'deferred','partial'}:
            return None
        job, condition = ctx
        if scoped_batch is not None:
            scoped_batch.check_one(session, scan.article_id, clock(), paid=True)
        article = session.get(ArticleRecord, scan.article_id)
        if not article or not _semantic_allowed(session, condition.owner_username, article.source_id):
            scan.status = 'superseded'; session.add(scan); session.commit()
            return None
        fingerprint, text, _ = evidence_snapshot(session, article, binding_version=EVIDENCE_BINDING_VERSION)
        source = _source_metadata(session, article)
        if (_paid_identity(condition, article, text, source) != scan.content_identity
            or not in_time_window(article.publish_date,
                start=scoped_batch.scope.publish_after if scoped_batch else now-dt.timedelta(days=7),
                end=scoped_batch.scope.publish_before if scoped_batch else now)):
            scan.status = 'superseded'; session.add(scan); session.commit()
            return None
        if _unresolved(session, scan.content_identity):
            _recover_unknown_requests(session, scan.content_identity, stamp)
            scan.status, scan.error_code = 'unknown', 'unresolved_paid_request'
            evidence = _scan_evidence(scan)
            _put_match(session,condition,article,fingerprint,'unknown',evidence,stamp)
            session.add(scan); session.commit()
            return None
        # Never combine coverage obtained with different prompts/models into a
        # complete negative. Leave the paid page and scan history unchanged.
        prior_policies = session.exec(select(Request.result_json).where(
            Request.scan_id == scan.id, Request.status == 'succeeded')).all()
        if any(_decode(value).get('authorization') != {'verifier_policy': policy}
               for value in prior_policies):
            return {'status': 'deferred', 'error_code': 'verifier_policy_changed', 'scan_id': scan.id}
        count = session.exec(select(func.count()).select_from(Request).where(Request.cycle_id == cycle_id)).one()
        if count >= cap:
            return None
        page = build_evidence_page(text, _decode(condition.condition_json),
                                   reviewed_chunk_ids=_decode(scan.completed_chunk_ids_json, []))
        windows = page['evidence_windows']
        prior = _union_ranges(_decode(scan.reviewed_ranges_json, []), len(text))
        ranges = []
        for window in windows:
            start, end = window['start'], window['end']
            if text[start:end] != window['text']:
                raise ValueError('window projection mismatch')
            ranges.append([start,end])
        supplied = _union_ranges(ranges, len(text))
        combined = _union_ranges(prior + supplied, len(text))
        if not windows:
            scan.status, scan.error_code = 'unknown', 'coverage_gap'
            _put_match(session,condition,article,fingerprint,'unknown',_scan_evidence(scan),stamp)
            session.add(scan); session.commit()
            return None
        if len(windows) > 6 or sum(len(w['text']) for w in windows) > 21000:
            raise ValueError('semantic payload exceeds window bounds')
        coverage = dict(page['coverage'])
        coverage.update(full_chars=len(text), projection_hash=scan.projection_hash,
                        projection_version=PROJECTION_VERSION,
                        supplied_unique_chars=_range_chars(supplied),
                        previously_reviewed_unique_chars=_range_chars(prior),
                        complete_after_this_page=combined == [[0,len(text)]])
        payload = {
            'contract_version':scan.contract_version, 'condition_id':condition.id,
            'condition_revision':condition.revision,
            'intent':{'condition':_decode(condition.condition_json)},
            'article':{'id':article.id,'title':article.title[:500],'content_type':article.content_type},
            'source':source, 'evidence_windows':windows, 'coverage':coverage,
        }
        # The verified projection is transformed inside this authorization
        # transaction; the same canonical bytes are stored and sent to the model.
        payload = to_span_evidence_payload(payload)
        encoded = encode_evidence_payload(payload)
        if len(encoded.encode()) > 128000:
            raise ValueError('semantic payload byte limit')
        rid = hashlib.sha256(_json([scan.content_identity,scan.contract_version,encoded]).encode()).hexdigest()
        if session.exec(select(Request.id).where(Request.request_identity == rid)).first():
            scan.status, scan.error_code = 'unknown', 'paid_page_already_authorized'
            _put_match(session,condition,article,fingerprint,'unknown',_scan_evidence(scan),stamp)
            session.add(scan); session.commit()
            return None
        if scoped_batch is not None:
            scoped_batch.check_article(session, condition, article, text, source, clock())
        request = Request(id=rid, request_identity=rid, scan_id=scan.id,
            content_identity=scan.content_identity, cycle_id=cycle_id, lease_token=token,
            input_bytes=len(encoded.encode()), output_token_limit=output_limit,
            payload_json=encoded, result_json=_json({'authorization': {'verifier_policy': policy}}),
            authorized_at=stamp)
        job.lease_expires_at = (now+dt.timedelta(seconds=timeout+60)).isoformat()
        session.add(request); session.add(job); session.commit()
        return {'request_id':rid, 'scan_id':scan_id, 'payload':payload,
                'chunk_ids':page['chunk_ids'], 'ranges':combined, 'fingerprint':fingerprint,
                'username':condition.owner_username, 'output_token_limit':output_limit,
                'verifier_policy':policy}


def _scan_ambiguity(session, scan):
    """Only received, validated pages count; historical paid unknowns stay fenced."""
    first, pages, count = None, [], 0
    # One succeeded request per reviewed page bounds this scan-local read.
    rows = session.exec(select(Request.id, Request.result_json).where(
        Request.scan_id == scan.id, Request.status == 'succeeded')
        .order_by(Request.completed_at, Request.id).limit(scan.page_count)).all()
    for request_id, result_json in rows:
        result = _decode(result_json)
        if result.get('outcome') != 'unknown':
            continue
        count += 1
        if first is None:
            first = {**result, 'reason': str(result.get('reason', ''))[:240]}
        if len(pages) < 8:
            pages.append({'request_id': request_id, 'reason': str(result.get('reason', ''))[:240]})
    return ({**first, 'ambiguous_page_count': count, 'ambiguous_pages': pages}
            if first is not None else None)


def _settle_page(engine, attempt, verdict, error, condition_id, revision, token, clock):
    stamp = clock().isoformat()
    with Session(engine) as session:
        lock_mutation(session)
        request = session.get(Request, attempt['request_id'])
        scan = session.get(Scan, attempt['scan_id'])
        if not request or not scan:
            return False
        # The pre-HTTP journal, not a response or a mutable wrapper, owns provenance.
        authorization = _decode(request.result_json).get('authorization')
        if (authorization != {'verifier_policy': attempt['verifier_policy']}
                or request.output_token_limit != attempt['output_token_limit']):
            error = 'policy_precheck_mismatch'
            verdict = {'outcome': 'unknown', 'reason': '语义请求授权策略不一致'}
        elif not error:
            verdict = {**verdict, 'verifier_policy': authorization['verifier_policy']}
        request.status = 'unknown' if error else 'succeeded'
        request.result_json = _json({**verdict, 'authorization': authorization,
                                    **({'error_code': error} if error else {})})
        request.error_code = error
        request.completed_at = stamp
        session.add(request)
        ctx = _context(session, condition_id, revision, token, stamp)
        article = session.get(ArticleRecord, scan.article_id)
        valid = bool(ctx and article and _semantic_allowed(session,scan.owner_username,article.source_id))
        fingerprint = ''
        if valid:
            _, condition = ctx
            fingerprint,text,_ = evidence_snapshot(session, article, binding_version=EVIDENCE_BINDING_VERSION)
            valid = _paid_identity(condition,article,text,_source_metadata(session,article)) == scan.content_identity
        if not valid:
            scan.status, scan.error_code = 'unknown', 'stale_or_revoked_result'
        elif request.status == 'unknown':
            scan.status, scan.error_code = 'unknown', request.error_code
        else:
            scan.page_count += 1
            scan.completed_chunk_ids_json = _json(sorted(set(
                _decode(scan.completed_chunk_ids_json, []) + attempt['chunk_ids'])))
            scan.reviewed_ranges_json = _json(attempt['ranges'])
            scan.has_mention = scan.has_mention or verdict['outcome'] == 'mention'
            complete = attempt['ranges'] == [[0,scan.full_chars]]
            if verdict['outcome'] == 'core' or complete:
                scan.status = 'succeeded'
                session.flush()  # Include this validated page in ambiguity aggregation.
                ambiguous = _scan_ambiguity(session, scan) if verdict['outcome'] != 'core' else None
                if ambiguous:
                    scan.result_json = _json(ambiguous)
                else:
                    outcome = 'core' if verdict['outcome'] == 'core' else ('mention' if scan.has_mention else 'no_match')
                    previous = _decode(scan.result_json)
                    chosen = previous if outcome == 'mention' and previous.get('outcome') == 'mention' else verdict
                    scan.result_json = _json({**chosen,'outcome':outcome})
            else:
                scan.status = 'partial'
                if verdict['outcome'] == 'mention' or not scan.has_mention:
                    scan.result_json = _json(verdict)
        scan.updated_at = stamp
        session.add(scan)
        # A paid result survives analysis-only CAS churn in the scan cache,
        # but is not installed until a later read of the new match fingerprint.
        published = valid and fingerprint == attempt['fingerprint']
        if published:
            evidence = _scan_evidence(scan)
            _put_match(session,ctx[1],article,fingerprint,evidence['outcome'],evidence,stamp)
        session.commit()
        return published


def run_match_cycle(engine, *, limit=MAX_PAGE, now=None, semantic_verifier=None, semantic_limit=6,
                    candidate_pairs=None, eligibility_scope=None):
    """At most six HTTP holds, one page per pair; optional operator-only narrowing.

    candidate_pairs never widens eligibility and is intentionally not an API
    parameter. It selects validation cohorts without changing paid identities.
    """
    scoped_batch = (match_scope.prepare_batch(engine, eligibility_scope, candidate_pairs, limit, now, _now())
                    if eligibility_scope is not None else None)
    if candidate_pairs is not None:
        if not isinstance(candidate_pairs, dict) or any(type(cid) is not int or cid < 1
                or not isinstance(ids, (list, tuple, set, frozenset))
                or any(not isinstance(aid, str) or not aid for aid in ids)
                for cid,ids in candidate_pairs.items()):
            raise ValueError('invalid semantic candidate filter')
        candidate_pairs = {cid:sorted(set(ids)) for cid,ids in candidate_pairs.items() if ids}
        if not candidate_pairs:
            return {'status':'idle','processed':0,'semantic_calls':0}
    if not _available(semantic_verifier):
        return _run_lexical_cycle(engine, limit=limit, now=now, candidate_pairs=candidate_pairs, scoped_batch=scoped_batch)
    from services.interest_evidence_windows import CONTRACT_VERSION, validate_semantic_verdict
    started = time.monotonic()
    base = now or _now()
    clock = lambda: base + dt.timedelta(seconds=time.monotonic()-started)
    stamp = base.isoformat()
    token, cycle_id = uuid.uuid4().hex, uuid.uuid4().hex
    cap = min(6,max(0,int(semantic_limit)))
    batch_limit = min(MAX_PAGE,max(1,int(limit)))
    with Session(engine) as session:
        lock_mutation(session)
        enabled = session.get(AppSettingRecord,MATCHING_ENABLED_KEY)
        if enabled and enabled.value.strip().casefold() in {'0','false','off','no'}:
            return (match_scope.batch_report(engine, scoped_batch, processed=set(), calls=0, status='disabled', error_code='matching_disabled')
                    if scoped_batch else {'status':'disabled','processed':0})
        if scoped_batch is not None:
            scoped_batch.check_all(session, clock())
        else:
            active = select(Condition.id).join(UserRecord,UserRecord.username==Condition.owner_username).where(
                Condition.status=='active',UserRecord.is_active.is_(True))
            session.exec(update(Scan).where(Scan.condition_id.not_in(active),
                Scan.status.in_(['deferred','partial'])).values(status='superseded',updated_at=stamp))
        job = session.exec(select(Job).join(Condition,Condition.id==Job.condition_id).join(
            UserRecord,UserRecord.username==Condition.owner_username).where(
                Condition.status=='active',UserRecord.is_active.is_(True),
                Job.next_attempt_at<=stamp if scoped_batch is None else True,
                or_(Job.lease_token=='',Job.lease_expires_at<=stamp),
                Job.condition_id.in_(candidate_pairs) if candidate_pairs is not None else True)
                .order_by(Job.next_attempt_at,Job.updated_at,Job.condition_id).limit(1)).first()
        if not job:
            session.commit()
            return (match_scope.batch_report(engine, scoped_batch, processed=set(), calls=0, status='busy', error_code='job_busy')
                    if scoped_batch else {'status':'idle','processed':0})
        condition_id, revision = job.condition_id, job.condition_revision
        job.lease_token, job.lease_expires_at = token,(base+dt.timedelta(minutes=5)).isoformat()
        session.add(job);session.commit()
    processed, calls, invocations = set(), 0, 0
    paid_articles = set()
    try:
        with Session(engine) as session:
            lock_mutation(session)
            ctx = _context(session,condition_id,revision,token,clock().isoformat())
            if not ctx:
                return (match_scope.batch_report(engine, scoped_batch, processed=set(), calls=0, status='superseded', error_code='job_superseded')
                        if scoped_batch else {'status':'superseded','processed':0})
            job,condition = ctx
            ast = ConditionAST.model_validate_json(condition.condition_json)
            cutoff = scoped_batch.scope.publish_before if scoped_batch else dt.datetime.fromisoformat(job.cycle_cutoff_at)
            if scoped_batch is not None:
                rows = scoped_batch.check_all(session, clock())
                exhausted = True  # The full explicit batch, including exactly 200, is the discovery universe.
            else:
                rows = session.exec(select(ArticleRecord).where(
                    ArticleRecord.source_id.in_(all_readable_source_ids(session,condition.owner_username)),
                    ArticleRecord.id>job.cursor,
                    ArticleRecord.id.in_(candidate_pairs[condition_id]) if candidate_pairs is not None else True,
                    ArticleRecord.publish_date>=(cutoff-dt.timedelta(days=8)).date().isoformat(),
                    ArticleRecord.publish_date<(cutoff+dt.timedelta(days=1)).date().isoformat(),
                ).order_by(ArticleRecord.id).limit(batch_limit)).all()
                exhausted = len(rows)<batch_limit
            last_id = rows[-1].id if rows else ''
            for article in rows:
                if not in_time_window(article.publish_date,start=scoped_batch.scope.publish_after if scoped_batch else cutoff-dt.timedelta(days=7),end=cutoff):
                    continue
                fingerprint,text,analysis = evidence_snapshot(session,article,binding_version=EVIDENCE_BINDING_VERSION)
                if scoped_batch is not None:
                    scoped_batch.check_article(session, condition, article, text, _source_metadata(session,article), clock())
                outcome,evidence = evaluate(ast,article.title,text,usable=bool(text.strip()) and bool(analysis))
                evidence['method'] = 'deterministic_title'
                if (text.strip() and not evidence.get('excluded')
                    and _semantic_allowed(session,condition.owner_username,article.source_id)):
                    # Exclusions always inspect the entire projected body, even
                    # when no analysis record exists or none of the groups hit.
                    lexical,evidence_full = evaluate(ast,article.title,text,usable=True)
                    if not evidence_full.get('excluded'):
                        old = session.exec(select(Match).where(Match.condition_id==condition.id,
                            Match.article_id==article.id)).first()
                        scan = _ensure_scan(session,condition,article,text,_source_metadata(session,article),
                                            old,CONTRACT_VERSION,stamp)
                        evidence = _scan_evidence(scan);outcome=evidence['outcome']
                    else:
                        outcome,evidence = lexical,{**evidence_full,'method':'deterministic_title'}
                _put_match(session,condition,article,fingerprint,outcome,evidence,stamp)
                processed.add(article.id)
            # One page per pair per pass. Unserved pairs precede paged pairs;
            # don't revisit partials until keyset discovery has reached the end.
            query = select(Scan).where(Scan.condition_id==condition_id,Scan.condition_revision==revision,
                Scan.contract_version==CONTRACT_VERSION,Scan.status.in_(['deferred','partial']))
            if candidate_pairs is not None:
                query = query.where(Scan.article_id.in_(candidate_pairs[condition_id]))
            if scoped_batch is not None:
                query = query.where(Scan.content_identity.in_([
                    scoped_batch.grant(aid).paid_content_identity for aid in scoped_batch.article_ids]))
            if not exhausted:
                query = query.where(Scan.page_count==0)
            queued = session.exec(query.order_by(Scan.page_count,Scan.updated_at,Scan.article_id).limit(MAX_PAGE)).all()
            scan_ids = [scan.id for scan in queued]
            session.commit()
        blocked_scans = []
        for sid in scan_ids:
            if calls >= cap:
                break
            attempt = _authorize_page(engine,sid,condition_id,revision,token,cycle_id,semantic_verifier,clock,cap,scoped_batch=scoped_batch)
            if attempt is None:
                continue
            if attempt.get('status') == 'deferred':
                blocked_scans.append(sid)
                continue
            calls += 1
            error = ''
            try:
                if scoped_batch is not None:
                    # No scope bypasses the actual POST guard owned by the private harness.
                    try:
                        with Session(engine) as session:
                            if _context(session,condition_id,revision,token,clock().isoformat()) is None:
                                raise match_scope.MatchScopeError('scope_lease_or_permission_revoked')
                            scoped_batch.check_one(session, attempt['payload']['article']['id'], clock(), paid=True)
                    except match_scope.MatchScopeError as exc:
                        if exc.code == 'scope_expired':
                            raise match_scope.MatchScopeError('scope_expired_preinvoke') from exc
                        raise
                invocations += 1
                paid_articles.add(attempt['payload']['article']['id'])
                raw = semantic_verifier(username=attempt['username'],payload=attempt['payload'],
                    reserved_output_token_limit=attempt['output_token_limit'],
                    expected_verifier_policy=dict(attempt['verifier_policy']))
                verdict = validate_semantic_verdict(attempt['payload'],raw,normalized=True)
            except Exception as exc:
                from services.interest_governance_llm import SemanticPolicyPrecheckError
                error = exc.code if isinstance(exc, (SemanticPolicyPrecheckError, match_scope.MatchScopeError)) else 'semantic_request_unknown'
                verdict = {'outcome':'unknown','reason':'语义请求未获得可验证结果'}
            if _settle_page(engine,attempt,verdict,error,condition_id,revision,token,clock):
                processed.add(attempt['payload']['article']['id'])
        if scoped_batch is not None:
            match_scope.release_lease(engine, condition_id, token)
            report = match_scope.batch_report(engine, scoped_batch, processed=processed, calls=calls,
                invocations=invocations, paid_articles=paid_articles)
            if blocked_scans:
                report.update(deferred_policy_scans=len(blocked_scans), error_code='verifier_policy_changed')
            return report
        with Session(engine) as session:
            lock_mutation(session)
            job = session.get(Job,condition_id)
            if not job or job.lease_token!=token:
                return {'status':'superseded','processed':len(processed),'semantic_calls':calls}
            pending = session.exec(select(Scan.id).where(Scan.condition_id==condition_id,
                Scan.condition_revision==revision,Scan.contract_version==CONTRACT_VERSION,
                Scan.status.in_(['deferred','partial']), Scan.id.not_in(blocked_scans),
                Scan.article_id.in_(candidate_pairs[condition_id]) if candidate_pairs is not None else True).limit(1)).first()
            stamp = clock().isoformat()
            job.cursor = '' if exhausted else last_id
            if exhausted:job.cycle_cutoff_at=stamp
            job.next_attempt_at = (clock()+dt.timedelta(seconds=1 if pending or not exhausted else 300)).isoformat()
            job.lease_token=job.lease_expires_at=job.last_error=''
            job.updated_at=stamp;session.add(job);session.commit()
        return {'status':'processed','processed':len(processed),'condition_id':condition_id,'semantic_calls':calls,
                **({'deferred_policy_scans':len(blocked_scans), 'error_code':'verifier_policy_changed'} if blocked_scans else {})}
    except Exception as exc:
        if scoped_batch is not None:
            match_scope.release_lease(engine, condition_id, token)
            return match_scope.batch_report(engine, scoped_batch, processed=processed, calls=calls,
                invocations=invocations, paid_articles=paid_articles, status='expired' if getattr(exc, 'code', '') == 'scope_expired' else 'failed',
                error_code=getattr(exc, 'code', 'matching_failed'))
        with Session(engine) as session:
            session.exec(update(Job).where(Job.condition_id==condition_id,Job.lease_token==token).values(
                lease_token='',lease_expires_at='',last_error='matching_failed',
                next_attempt_at=(clock()+dt.timedelta(minutes=5)).isoformat(),updated_at=clock().isoformat()))
            session.commit()
        return {'status':'failed','processed':len(processed),'condition_id':condition_id,'semantic_calls':calls}
    finally:
        if scoped_batch is not None:
            match_scope.release_lease(engine, condition_id, token)

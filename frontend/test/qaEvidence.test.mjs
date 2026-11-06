import assert from 'node:assert/strict';
import test from 'node:test';
import { qaBasisLabel, qaEvidence, qaExcerptPreview, qaHistory, qaReferenceGroups } from '../src/utils/qaEvidence.js';

test('old article answers and empty or malformed evidence never invent an excerpt', () => {
  for (const source of [null, undefined, {}, { evidence: {} }, { evidence: [null, {}, { text: 42 }, { text: '  ' }] }]) {
    assert.deepEqual(qaEvidence(source), []);
    assert.equal(qaBasisLabel(source), '');
  }
  const notesOnly = { basis_label: '节目简介', evidence: [] };
  assert.deepEqual(qaEvidence(notesOnly), []);
  assert.equal(qaBasisLabel(notesOnly), '节目简介');
});

test('excerpts keep their exact source text and positions without fabricating time offsets', () => {
  const excerpt = {
    kind: 'publisher_transcript', artifact_id: 12, content_hash: 'sha256-value',
    range_start: 1500, range_end: 1550,
    text: '  Host: Can we generalize?\nGuest: Only under this assumption.  ',
  };
  const [display] = qaEvidence({ evidence: [excerpt] });
  assert.deepEqual(display, { ...excerpt, label: '来源逐字稿' });
  assert.equal(display.text, excerpt.text);
  assert.equal('start_seconds' in display, false);
  assert.equal('label' in excerpt, false);
});

test('mixed evidence identifies transcript and derived text independently of source basis', () => {
  const source = {
    basis_label: 'ASR逐字稿',
    evidence: [
      { kind: 'normalized_transcript', text: 'ASR words' },
      { kind: 'transcript_zh', text: '翻译文字' },
      { kind: 'digest_blog_zh', text: '导读文字' },
      { kind: 'show_notes', text: '节目简介文字' },
    ],
  };
  assert.deepEqual(qaEvidence(source).map((item) => item.label), ['语音识别逐字稿', '中文逐字稿', '中文导读', '节目简介']);
  assert.equal(qaBasisLabel(source), 'ASR逐字稿');
});

test('future evidence kinds use a supplied label or neutral wording', () => {
  assert.equal(qaEvidence({ evidence: [{ kind: 'future', text: 'original' }] })[0].label, '参考原文');
  assert.equal(qaEvidence({ basis_label: {}, evidence: [{ text: 'original' }] })[0].label, '参考原文');
  assert.equal(qaEvidence({ basis_label: '其他原文', evidence: [{ kind: 'future', text: 'original' }] })[0].label, '其他原文');
  assert.equal(qaBasisLabel({ evidence: [
    { kind: 'publisher_transcript', text: 'a' },
    { kind: 'publisher_transcript', text: 'b' },
    { kind: 'normalized_transcript', text: 'c' },
  ] }), '来源逐字稿、语音识别逐字稿');
});

test('uncited references remain available without changing citation numbers or calling them proof', () => {
  const sources = [{ id: 'first' }, { id: 'second' }, { id: 'third' }];
  const groups = qaReferenceGroups('见[3]和[1]，[3]重复，[99]无效。', sources);
  assert.deepEqual(groups.primary, [{ source: sources[0], num: 1 }, { source: sources[2], num: 3 }]);
  assert.deepEqual(groups.more, [{ source: sources[1], num: 2 }]);
  assert.equal(groups.cited, true);
  const fallback = qaReferenceGroups('请查看参考资料。', sources);
  assert.equal(fallback.primary.length, 3);
  assert.equal(fallback.cited, false);
  assert.deepEqual(fallback.more, []);
  assert.deepEqual(qaReferenceGroups('你好'), { primary: [], more: [], cited: false });
});

test('references-only responses do not contaminate the next model conversation', () => {
  assert.deepEqual(qaHistory([
    { q: 'Q1', a: 'A1' }, { q: 'Q2', a: '部分可确定', answerMode: 'partial' },
    { q: 'Q3', a: '生成失败，请查看原文', answerMode: 'references_only' },
    { q: 'Q4', a: 'bad', error: 'error' }, { q: 'Q5', a: 'waiting', pending: true },
  ]), [
    { role: 'user', content: 'Q1' }, { role: 'assistant', content: 'A1' },
    { role: 'user', content: 'Q2' }, { role: 'assistant', content: '部分可确定' },
  ]);
});

test('mixed Markdown and plain references retain both sources with the existing display parser', () => {
  const sources = [{ id: 'one' }, { id: 'two' }];
  const groups = qaReferenceGroups('参考[1](https://example.test)，另见[2]。', sources);
  assert.deepEqual(groups.primary, [{ source: sources[1], num: 2 }]);
  assert.deepEqual(groups.more, [{ source: sources[0], num: 1 }]);
  assert.equal(groups.cited, true);
});

test('long reference previews are explicit excerpts and retain access to the exact original', () => {
  const text = '前文'.repeat(280) + '\nSPEAKER_2: 不能保证收益。\n' + '后文'.repeat(500);
  const result = qaExcerptPreview(text);
  assert.equal(result.truncated, true);
  assert.ok(text.startsWith(result.text));
  assert.ok(result.text.includes('不能保证收益'));
  assert.ok(result.text.length <= 800);
  assert.deepEqual(qaExcerptPreview('  原文\n未修改。  '), { text: '  原文\n未修改。  ', truncated: false });
});

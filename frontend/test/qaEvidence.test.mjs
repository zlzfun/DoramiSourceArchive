import assert from 'node:assert/strict';
import test from 'node:test';
import { qaBasisLabel, qaEvidence } from '../src/utils/qaEvidence.js';

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

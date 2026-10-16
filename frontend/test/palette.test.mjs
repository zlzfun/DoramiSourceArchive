import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_PALETTE, PALETTES, normalizePalette, readPalettePref } from '../src/palette.js';

test('三套中性色,默认瓷', () => {
  assert.deepEqual(PALETTES, ['porcelain', 'graphite', 'paper']);
  assert.equal(DEFAULT_PALETTE, 'porcelain');
});

test('未知值回落默认,已知值原样保留', () => {
  assert.equal(normalizePalette('graphite'), 'graphite');
  assert.equal(normalizePalette('paper'), 'paper');
  assert.equal(normalizePalette('neon'), 'porcelain');
  assert.equal(normalizePalette(null), 'porcelain');
});

test('没有 localStorage 的环境读到默认值,不抛错', () => {
  assert.equal(readPalettePref(), 'porcelain');
});

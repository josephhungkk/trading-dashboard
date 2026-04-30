/**
 * Phase 6 — CJK visual-diff stories (M6).
 *
 * Renders the same kanji string under each `lang=` so the operator can compare
 * the per-locale glyph variants side-by-side. Once `frontend/public/fonts/
 * NotoSansJP-{kana,kanji}-400.woff2` are generated (see fonts README), the
 * Japanese row should pick up the JP-specific kanji shapes via the
 * `[lang|="ja"]` selector in global.css.
 */
import type { Meta, StoryObj } from '@storybook/react-vite';

const meta: Meta = { title: 'Primitives/Text/CJK' };
export default meta;

const KANJI_SAMPLE = '腾讯控股 (00700) — 騰訊控股 — 텐센트홀딩스';

export const TraditionalChinese: StoryObj = {
  render: () => (
    <p lang="zh-TW" style={{ fontSize: '2rem' }}>
      {KANJI_SAMPLE}
    </p>
  ),
};

export const SimplifiedChinese: StoryObj = {
  render: () => (
    <p lang="zh-CN" style={{ fontSize: '2rem' }}>
      {KANJI_SAMPLE}
    </p>
  ),
};

export const Japanese: StoryObj = {
  render: () => (
    <p lang="ja" style={{ fontSize: '2rem' }}>
      {KANJI_SAMPLE}
    </p>
  ),
};

export const Korean: StoryObj = {
  render: () => (
    <p lang="ko" style={{ fontSize: '2rem' }}>
      {KANJI_SAMPLE}
    </p>
  ),
};

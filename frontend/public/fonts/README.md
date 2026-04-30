# Fonts — Subsetting Pipeline

Self-hosted Noto Sans woff2 subsets. CJK split per language so each
`@font-face` declares a tight `unicode-range` and the browser only fetches
the file whose codepoints actually appear on the page (per spec sec 7.1, M6).

The TC/SC/HK/KR subsets shipped in Phase 3 already follow this pattern. Phase 6
splits JP into two faces (kana ~50KB, kanji ~1-2MB) so the kanji file is only
fetched when a Japanese ticker is rendered — see the `[lang|="ja"]` selector
in `frontend/src/styles/global.css`.

## Regenerate JP kana + kanji

Source: `NotoSansJP-Regular.otf` from
`https://github.com/notofonts/noto-cjk/blob/main/Sans/OTF/Japanese/NotoSansJP-Regular.otf`
(open the page, click "Download raw file"; the `raw.githubusercontent.com`
mirror redirects to a CDN that 404s for unauthenticated curl).

```bash
# Tooling
uv tool install --with brotli --with zopfli fonttools
# (or: pip install fonttools brotli zopfli)

# Kana-only (~50KB)
pyftsubset NotoSansJP-Regular.otf \
  --output-file=NotoSansJP-kana-400.woff2 \
  --flavor=woff2 \
  --unicodes=U+3040-309F,U+30A0-30FF,U+31F0-31FF

# Kanji-only (~1-2MB, lazy-loaded behind unicode-range)
pyftsubset NotoSansJP-Regular.otf \
  --output-file=NotoSansJP-kanji-400.woff2 \
  --flavor=woff2 \
  --unicodes=U+4E00-9FFF,U+3400-4DBF,U+F900-FAFF
```

The two woff2 files share family name `Noto Sans JP` in CSS; the browser loads
only the file whose `unicode-range` matches rendered content. The kanji block
overlaps the TC subset — CJK unification means most glyphs coincide, but the
JP face wins under `[lang|="ja"]` due to the cascade-explicit `font-family`
override.

## Drop the legacy combined JP subset

Once the two files above land, delete the old combined subset:

```bash
git rm frontend/public/fonts/NotoSansCJK-JP-400.subset.woff2
```

## Provenance

Whitelist for the per-locale subsetting (TC/SC/HK/KR) is documented in
`subset-sources.txt`. JP whitelist applies the full kana + CJK Unified
Ideographs ranges (since Japanese tickers may use any kanji), not the
ticker-name whitelist used for the smaller TC/SC subsets.

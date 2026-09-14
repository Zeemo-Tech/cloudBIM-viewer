# ZEEMO VI source record

## Scope and source integrity

This directory is the versioned reference record for the supplied **ZEEMO Brand Guidelines / 品牌VI识别系统**. The manual is a 36-page, landscape PDF created 2025-09-12 (metadata), copied here unchanged as `vi-manual-2025-09-12.pdf` (2,370,150 bytes; SHA-256 `bcca4c16d748399cd14664d4192bbf1eecc96c935679dc1b81f8c942ce5e46c4`). `vi-manual-2025-09-12.txt` is a Poppler layout-text extraction for search only; several embedded glyphs are not extractable, so visual pages take precedence.

This README records document-authored brand guidance. It does not grant font licences, supply editable logo artwork, or change repository/UI behaviour.

## Verified normative values

- **Name/tagline:** 宏观智眸 / ZEEMO; `See More Know More` (pp. 3, 9).
- **Logo:** Chinese/English horizontal and vertical lockups are shown on p. 9. Preserve proportions, original solid colour/outline and text; do not rotate, recolour, alter spacing, redraw the mark, or place it on a low-contrast background (p. 15). Black and reversed-white forms are supplied as examples (p. 14).
- **Clear space:** define symbol height as `X`; reserve `0.5X` equally on all sides (pp. 10-13).
- **Minimum sizes (print / screen):** symbol `5 mm / 16 px`; Chinese horizontal `6 mm / 18 px`; Chinese vertical `12 mm / 32 px`; English horizontal `7 mm / 24 px`; English vertical `12 mm / 32 px` (pp. 10-13, visually verified against the diagrams).
- **Colour palette:** exact printed RGB/CMYK/HEX values and displayed palette shares are in `tokens.json`, transcribed from p. 17.
- **Typography:** Chinese: Source Han Sans (思源黑体), ExtraLight through Heavy; English: HarmonyOS Sans, Thin through Black. Fallbacks: Microsoft YaHei (Light/Regular/Bold) and Arial (Regular/Bold/Black) (pp. 19-22). The manual gives no font files or licence terms.
- **Pattern/visual direction:** repeating symbol pattern (p. 25), architectural/technology photography examples (pp. 27-28), and stationery examples (pp. 30-36) are reference examples, not reusable source assets.

## Screen-colour treatment and CloudBIM fit

The screen tokens use the manual's explicitly printed `RGB` and `HEX` columns directly; no CMYK-to-sRGB conversion was made. CMYK values remain print specifications. A future conversion from CMYK alone must name the ICC profile and rendering intent; neither is supplied.

For CloudBIM, retain the existing blue/white engineering shell from `DESIGN.md`: use Sapphire Blue for primary brand moments or selected states, the lighter blues for restrained highlights/data surfaces, and retain existing semantic danger/error colours. Add a logo only from approved vector/raster artwork (none is included here), respect its clear space/minimum size, and keep the current Chinese UI font stack when the licensed primary faces are unavailable.

## Open items

The PDF has no editable logo assets, ICC profile, accessibility contrast specification, font licence/distribution terms, or web component rules. Obtain those from the brand owner before production brand rollout.

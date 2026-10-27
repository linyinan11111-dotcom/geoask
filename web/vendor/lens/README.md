# Lens optical core

Vendored unchanged from [ruiqichenbiec/design-systems](https://github.com/ruiqichenbiec/design-systems),
commit `7a424c93b9be13cf835b91ea67495fec2ec0584e`, Lens `1.1.0-showcase.1`.

Source: `lens/lab/design-system/tokens.js` and the five files in `internal/`.
MIT licensed; see `LICENSE.txt`. Copyright (c) 2026 DayDreamInAReverie.

`web/glass.js` integrates the original WebGL2 compositor and spring controller
with geoask's existing semantic HTML. The input refracts a custom aurora canvas.
Map tiles remain live DOM: floating panels use CSS backdrop blur and locally
generated SVG displacement bevels. Lens does not sample arbitrary DOM. No third-party
scripts, runtime downloads, sound, or perpetual background animation are enabled.
When WebGL2 is unavailable or reduced transparency is requested, CSS supplies
the readable material. Original files are kept intact to make updates reviewable.

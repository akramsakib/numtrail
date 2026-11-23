# CREDITS

numtrail is a derivative work. The `instagram`, `snapchat` and `amazon` modules
are ports of code from **ignorant** by megadose (Twitter @palenath), originally
released under GPL-3.0:

- <https://github.com/megadose/ignorant>

Those modules were rewritten around this project's status model, and the
endpoints were re-aimed at each site's current flows, but the technique and the
protocol details (Instagram's HMAC-signed body, Snapchat's territory mapping,
Amazon's sign-in dance) come from that work.

Upstream also credits:
- [yazeed44](https://github.com/yazeed44) — social-media-detector lineage for the
  Instagram lookup.

Because it derives from GPL-3.0 code, numtrail is GPL-3.0-or-later. See LICENSE.

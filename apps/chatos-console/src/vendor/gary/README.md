# Gary-UI vendor record

- Source: `C:\AI\UI\gary-ui`
- Source commit: `05b4693459d9bc17fc1d254379ea1b4f99f94931`
- Token version: `1.3.0`
- Vendored on: `2026-09-16`
- Files copied: `tokens/base.css`, `components/components.css`, and the referenced ridge texture SVG.
- Vite compatibility: the nested `@import` at the top of `tokens/base.css` is removed and both Gary CSS files are imported explicitly, before Tailwind, from `src/index.css`; token and component rules are unchanged.

The Gary-UI mother source was not modified. ChatOS-specific composition stays in `src/index.css`.

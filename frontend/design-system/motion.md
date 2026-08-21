# Motion

Extracted directly from transition-related CSS rules in
`wp-content/themes/gnws/style.css`.

## Easing

Every observed transition uses the **same cubic-bezier curve**:

```css
transition-timing-function: cubic-bezier(.4, 0, .2, 1);
```

This is Tailwind's default "ease" curve (a fast-start, gentle-finish
standard easing). There is no evidence of a custom/bespoke easing curve —
the motion language relies on consistency of a single curve applied
everywhere, not on unique per-component easing.

## Durations

| Duration | Where used |
|---|---|
| `150ms` | Base `.transition-colors` / `.transition-all` utility default (small state changes: link color, simple hovers) |
| `300ms` | Icon button hover scale, search icon interactions |
| `500ms` | Header background/logo crossfade, mobile menu slide, pill button hover-fill inversion, tab/segmented-control active state change, image-panel content transitions |

Reading: **quick (150ms)** for micro color changes, **medium (300ms)** for
small icon feedback, **slow (500ms)** for structural/layout transitions
(menus sliding, headers changing state, buttons inverting fill). Nothing
observed above 500ms.

## Hover effects

- Icon buttons (search, hamburger): `hover:scale-110` — 10% scale-up,
  paired with the 300ms duration.
- Pill/CTA buttons: full background/text color inversion on hover (not a
  simple opacity or scale change) — see `components.md`.
- Nav links: underline appears on hover (footer), or a persistent bottom
  border for the active/current item (main nav) rather than a hover
  underline.

## Structural transitions

- **Header**: `bg-transparent → bg-white`, plus logo crossfade
  (opacity 0↔1) and bottom hairline fade-in, all at `500ms`, triggered by
  scroll state (`.active`/`.active-item` classes toggled via JS).
- **Mobile menu**: `translate-x-full → translate-x-0`, `500ms`.
- **Search panel**: implied slide/height transition (part of the mobile
  menu drawer), same `500ms` family — exact trigger CSS not fully isolated
  from minified output; **treat duration as confirmed, exact
  transform property as estimate**.
- **Hero/industry slider**: Swiper-driven slide transitions; per-slide
  `data-time` attributes are set dynamically from video duration in JS
  (autoplay delay matches the actual video length) — a content-aware
  timing detail rather than a fixed animation duration.

## Scroll animations

No dedicated scroll-triggered reveal/fade-in library (e.g. AOS, GSAP
ScrollTrigger) was detected in the fetched markup or stylesheet — the
`scroll-smooth` and `scroll-mt-[150px]` utility classes present are for
**anchor-link scroll offset/smoothness**, not entrance animations.
**This is a negative finding, not an estimate**: treat "no scroll-reveal
animation" as accurate unless a deeper page-level check says otherwise.

## Recommended motion system for AI Carbon Analyst

- Standardize on a single easing curve (`cubic-bezier(.4,0,.2,1)` /
  Tailwind `ease` is a safe, proven default) applied uniformly.
- Three-tier duration scale: `150ms` micro-interactions, `300ms` small
  component feedback, `500ms` structural/layout transitions. Avoid
  introducing a fourth tier — the restraint is part of why Stavian's
  motion feels cohesive.
- Prefer state-inversion (color swap) over scale/opacity for primary CTA
  hover states if a similarly confident, corporate feel is desired.

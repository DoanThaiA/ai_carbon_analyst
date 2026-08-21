# Layout

Extracted from `.container` rules and breakpoint usage in
`wp-content/themes/gnws/style.css`.

## Container / max-width

Actual CSS found:

```css
.container { width: 100%; margin-inline: auto; padding-inline: 1rem; }
.container { max-width: 600px }   /* small */
.container { max-width: 728px }
.container { max-width: 984px }
.container { max-width: 1240px }
.container { max-width: 1460px }
.container { max-width: 1732px }  /* largest — content never exceeds this */
```

The site's max content width (`1732px`) is notably wider than the common
`1200`/`1280px` convention — the design is built for large desktop
monitors, with the header sometimes explicitly breaking out to
`max-w-full` below `1900px` (`max-[1900px]:mx-0 max-[1900px]:max-w-full`)
so the header bar can span edge-to-edge while inner content stays capped.

## Breakpoints

Two overlapping sets are in play:

**Tailwind's standard breakpoints** (used for most utility responsiveness):
`sm 640px · md 768px · lg 1024px · xl 1280px · 2xl 1536px`

**Custom container breakpoints** (control the `.container` max-width steps
above): `600 · 728 · 984 · 1240 · 1460 · 1732px`, plus extra large
custom breakpoints seen in header rules: `1700px`, `1760px`, `1900px`.

Notably, the **primary nav collapses to the hamburger/mobile menu at `xl`
(1280px)**, not the more common `lg` (1024px) — desktop nav needs real
estate up to laptop-width screens before it's shown.

## Grid / Composition patterns

- **Full-bleed sections**: hero banner, industry/business tab imagery —
  background media spans the viewport edge-to-edge; text content is
  constrained inside `.container`.
- **Contained sections**: stats, quote, global-presence — centered text
  block (`max-w-[743px]`–`max-w-[1174px]`) inside the container, often
  center-aligned on desktop and left-aligned on mobile.
- **Stat/feature grid**: `grid-cols-5` desktop → `grid-cols-1` mobile,
  each cell separated by a **border divider** (right border desktop,
  bottom border mobile) rather than a gap-driven card look.
- **Card carousel**: horizontally scrolling `swiper` track of white cards,
  each roughly `45%` viewport width desktop / `90%` mobile (peeking-next-
  card pattern, not a fixed grid).
- **Split panel**: `max-w-[575px]` two-column-feel blocks (media + text)
  divided by a vertical `border-r` on desktop, stacking to
  `flex-col-reverse` on mobile (media appears before text visually is
  reversed — text stacks under image).

## Alignment

- Headings inside "quote"/stat/global-presence sections are
  **center-aligned on desktop, left-aligned on mobile**
  (`lg:text-center`, no center class at mobile size) — a consistent
  pattern of "center on desktop, left on mobile" throughout.
- Hero heading is always left-aligned regardless of breakpoint.

## Desktop / Tablet / Mobile behavior summary

| Aspect | Desktop (≥1280px) | Tablet (768–1279px) | Mobile (<768px) |
|---|---|---|---|
| Nav | Full horizontal menu + language switch inline | Hamburger (collapses at `xl`) | Hamburger, slide-in panel from right (`400px` wide) |
| Section padding | Very large (up to `244px` between sections) | Reduced, Tailwind default steps | Compact (`48–56px`) |
| Stat grid | 5 columns, divider borders | Responsive reflow (estimate) | 1 column, horizontal row layout with icon+number+label inline |
| Section headings | `60–80px`, centered | Scales down via `lg:` steps | `24–36px`, left-aligned |
| Cards | Peeking swiper, ~45% width | Similar, adjusted % | ~90% width, edge-peeking swipe |

## Recommended layout approach for AI Carbon Analyst

- A capped, generous max-width (~1400–1600px) with full-bleed hero/banner
  moments is a reasonable analog — don't feel bound to Stavian's exact
  1732px, which is unusually wide.
- Keep the "center-align headings on desktop, left-align on mobile"
  convention if a similarly corporate/editorial tone is wanted.
- A dashboard/analyst product will diverge from Stavian's marketing-site
  composition (full-bleed imagery, swiper carousels) — reuse the
  *spacing rhythm and container discipline*, not the literal section
  patterns, per the skill's core rule.

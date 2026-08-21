# Components

Visual patterns extracted from the rendered homepage. Descriptions capture
**style only** — colors, shape, spacing, interaction — not Stavian's actual
copy, imagery, or information architecture.

## Navbar / Header

- **Position**: fixed to viewport top, full width, `z-50`.
- **Home-page state**: transparent background over the hero, text/icons in
  white.
- **Scrolled/interior-page state**: solid white background
  (`bg-white`), a `1px` bottom hairline (`#E6E6E6`) fades in.
- **Transition**: `all 500ms`, `cubic-bezier(.4,0,.2,1)` — background,
  text color, and logo swap crossfade together, not an abrupt cut.
- **Logo swap**: two logo images layered, opacity-crossfaded between a
  "light" and "dark" variant depending on header state (0 ↔ 1 opacity).
- **Nav items**: top-level links with dropdown "mega" sub-menus; active/
  current item gets a **5px solid bottom border in primary teal**.
- **Language switcher**: simple `EN | VI` text toggle, muted-gray divider
  glyph between, inline before the mobile menu button.
- **Search**: icon-triggered, expands to a rounded-full search input in a
  right-side slide-out panel (part of the mobile menu drawer), not an
  inline navbar search box.
- **Collapse breakpoint**: `xl` (1280px) — desktop nav is available further
  down than typical `lg` breakpoints.

## Mobile menu

- Slides in from the right, fixed width `400px` (full width on small
  phones), full viewport height, white background.
- `translate-x-full → 0`, `500ms` transition.
- Header row: back/close icon, language switch, close (×) button in
  primary teal.
- Search field directly below header row.
- Scrollable nav list fills remaining height; social icons and a
  "Contact Us" CTA pinned near the bottom.

## Buttons

Two consistent variants, both **pill-shaped** (`rounded-full`):

**Primary (solid) button**
```
background: primary teal · text: near-white
border: 1px solid primary teal
hover → background: white · text: primary teal   (fill inverts)
padding: 15px 30px desktop / ~10px 16–24px mobile (asymmetric, more left than right to balance trailing icon)
font: semibold
icon: trailing chevron/arrow, inherits currentColor
transition: all 500ms, cubic-bezier(.4,0,.2,1)
```

**Ghost/outline button** (used over photography)
```
background: transparent · text: near-white
border: 1px solid white
hover → background: white · text: primary teal
```

Both variants share the same shape, padding, icon placement, and hover
inversion logic — only the resting fill differs. This "solid ↔ inverse on
hover" pattern is the signature button interaction across the site.

## Pill tabs / filters

- Rounded-full segmented control, `min-w-[100px]`, `py-2.5` desktop /
  `py-2` mobile, `px-[18px]`.
- Inactive: transparent background, `1px` gray border (`#B0B0B0`), dark
  text.
- Active: solid primary teal background, no visible border, white text.
- `all 500ms` transition between states.
- Used both as a large content-switching tab bar (`tab-btn-item`, full
  width, no rounding — acts more like a segmented nav strip with a
  right-hand border between items) and as a compact pill filter row.

## Cards

**Media card** (e.g. industry/news card in a horizontal swiper)
```
background: white · radius: 16px (rounded-2xl) · padding: 16px
layout: flex column, gap 16px
header row: bold 18px title (dark gray #333) + small circular icon-button
   (28px, teal border, teal arrow icon) right-aligned
image: fills remaining space, ~3:2 aspect ratio, radius 8px (rounded-lg), object-cover
```

**Split panel card** (media + copy)
```
background: off-white (#F6F6F6) on desktop, transparent on mobile
radius: 16px desktop only
padding: 40px vertical / 48px horizontal desktop
layout: row (image right, text left) desktop → column-reverse mobile
divider: right border between adjacent panels, none on mobile
```

## Stat / achievement blocks

```
layout: icon circle → big number → label
icon circle: 150px, rounded-full, solid primary teal background, white icon, centered (desktop);
             shrinks to 40px/24px on tablet/mobile and drops its teal fill
number: 48px bold, primary teal or dark gray, tight leading (1.3)
label: 18–22px regular, muted gray
desktop: vertical stack, center-aligned, separated by a right border between cells
mobile: horizontal row (icon — number — label), separated by a bottom border
```

## Full-bleed image/content section (hero & industry panels)

```
background: full-bleed photo or video, object-cover
overlay: linear-gradient(0deg, black-60%-alpha 90%, transparent) from bottom
   — ensures light text stays legible without fully darkening the image
content: constrained to .container, anchored bottom-left (industry panels)
   or vertically centered-left (hero)
text: near-white (#F6F6F6), large bold/extrabold headline + one-line
   description + ghost CTA button
```

## Section heading pattern

```
size: 60px desktop / 24px mobile, bold, tight leading (1.2–1.3)
alignment: centered on desktop, left on mobile
supporting text: 18px desktop / 14px mobile, semibold on desktop,
   muted gray (#5D5D5D), centered under heading, max-width constrained
   for readable line length
```

## Footer

```
background: dark teal (#0F5F5A) · text: white
layout: logo + contact block | two link columns | social icons + CTA
divider: 1px white-at-20%-opacity lines separate stacked mobile sections
social icons: 52px circle, teal-tinted translucent fill (#F0FDFA at 10%
   opacity), 1px gray border
CTA: same pill button pattern as elsewhere, adapted per breakpoint
   (rounded-full desktop / rounded-lg mobile)
link style: plain white text, underline on hover
```

## Motion on interactive elements

See `motion.md` for the full easing/duration language — every component
above shares the same `cubic-bezier(.4,0,.2,1)` curve and a small set of
durations (150/300/500ms), which is what makes the site feel cohesive
despite having many distinct component types.

## Reuse guidance for AI Carbon Analyst

Directly reusable *patterns* (not literal styling):
- Pill button with hover-inverted fill.
- Stat block: icon-circle + big-number + label, with a divider-based grid
  instead of card shadows.
- Section heading: large centered headline + centered muted subhead,
  collapsing to left-aligned on mobile.
- Header that starts transparent over a hero and solidifies on scroll.

Not applicable to a dashboard/analyst product: full-bleed video hero,
swiper image carousels, mega-menu navigation — these are marketing-site
idioms, not analyst-tool UI, and should not be copied structurally.

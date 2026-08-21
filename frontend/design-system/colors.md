# Colors

Extracted from `https://stavian.com/` — live theme stylesheet
(`wp-content/themes/gnws/style.css`) and rendered homepage markup, 2026-08-17.
Values below are **actual hex/rgb values found in the site's CSS**, not
guesses, unless explicitly marked "estimate."

## Brand / Primary

| Token | Value | Source | Usage on Stavian |
|---|---|---|---|
| `primary` | `#0D7870` (rgb 13 120 112) | `.bg-primary`, `.text-primary`, `.border-primary` utility classes | Active nav underline, CTA button fill, icon circles, link/active accents |
| `primary-dark` | `#0F5F5A` | `footer` background, deep-teal section backgrounds | Footer, dark contrast sections |
| `accent-mint` | `#5BEDD6` | found alongside primary in the theme's teal color group | Bright accent, likely used sparingly for highlights/gradients |
| `tint` | `#F0FDFA` | text/background tint paired with primary | Very light mint tint — text on dark teal, subtle tinted backgrounds |
| `tint-wash` | `#F0FDFA1A` (10% opacity) | footer social icon circle background | Translucent tint fill over dark backgrounds |

Primary is a **deep teal**, not a bright corporate blue — this is the single
most identity-defining color on the site. It reads as confident, sustainable,
and industrial rather than "startup SaaS blue."

## Neutrals — Text

| Token | Value | Usage |
|---|---|---|
| `text-strongest` | `#0D0D0D` / `#1A1A1A` | Near-black, darkest body copy |
| `text-heading` | `#3D3D3D` | Section headings on light backgrounds |
| `text-body-muted` | `#5D5D5D` | Body copy under headings, descriptions |
| `text-label` | `#333333` | Card titles/labels |
| `text-inverse` | `#F6F6F6` / `#FFFFFF` | Text over dark/hero imagery |

## Neutrals — Muted / Placeholder

| Token | Value | Usage |
|---|---|---|
| `muted-1` | `#999999` | Placeholder text |
| `muted-2` | `#A8ABB3` | Inactive language switch label |
| `muted-3` | `#8F9095` | Separator glyphs (e.g. `EN \| VI`) |
| `muted-4` | `#B0B0B0` / `#B3B3B3` | Dividers between stat blocks, inactive pill borders |

## Borders / Dividers

| Token | Value | Usage |
|---|---|---|
| `border-light` | `#E6E6E6` / `#E7E7E7` | Header bottom hairline on scroll, card borders |
| `border-soft` | `#D1D1D1` / `#DBDBDB` | Secondary dividers |
| `border-on-dark` | `#FFFFFF33` (white, 20% opacity) | Divider lines over dark/teal sections (e.g. footer) |

## Surfaces

| Token | Value | Usage |
|---|---|---|
| `surface-white` | `#FFFFFF` | Header bar, content cards |
| `surface-alt` | `#F6F6F6` | Alternating section background, split-card background |
| `surface-alt-2` | `#F2F2F2` | Secondary light surface |

## Overlay / Imagery Treatment

| Token | Value | Usage |
|---|---|---|
| `image-overlay` | `linear-gradient(0deg, #0009 90%, #0000)` | Bottom-to-transparent black gradient over full-bleed photography so white text stays legible |

## Notes on discarded/ambiguous values

- The stylesheet also ships the **stock Tailwind gray/neutral palette**
  (`#111827`, `#374151`, `#6b7280`, `#9ca3af`, `#e5e7eb`, `#171717`,
  `#404040`, `#737373`, etc.) as CSS custom properties. No homepage markup
  observed actually applies these as brand colors — they appear to be
  leftover framework defaults from the `_tw` Tailwind starter theme, not
  intentional design choices. **Not included in the extracted palette.**
- A `--primary-color: #0011af` (blue) custom property exists in the
  stylesheet but was not found applied anywhere in the rendered homepage.
  Likely a legacy/unused variable from an earlier theme iteration.
  **Estimate/low-confidence — excluded from tokens.**
- `#ff5912` (orange) and `#6254e7` (purple) appear only as default link
  colors inside `.the_content` (WYSIWYG blog/news article body content),
  not on the marketing homepage itself. Flagged as **possible legacy
  defaults**, not verified brand accents — excluded from the core palette.

## Recommended palette for AI Carbon Analyst

Reuse the *language*, not the literal brand: a deep teal/green primary
paired with warm near-black text and soft off-white surfaces reads as
credible for a sustainability/carbon product too. Do not reuse Stavian's
exact hex values verbatim if brand-distinctiveness matters — treat
`#0D7870` / `#0F5F5A` as a reference point for hue and depth, not a
mandatory value.

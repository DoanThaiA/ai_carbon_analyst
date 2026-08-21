# Typography

Extracted from `wp-content/themes/gnws/assets/fonts/font.css` and the
computed classes on homepage headings/body text.

## Font Family

```css
font-family: Averta, sans-serif;
```

- **Averta** is a licensed, self-hosted commercial typeface (served as
  `.otf` files directly from the theme, no Google Fonts). It is used for
  *both* headings and body copy — there is no separate display face.
- Fallback: generic `sans-serif` only (no fallback stack like
  `-apple-system`/`Helvetica` was found).
- **Estimate/reuse note:** Averta is a paid font Stavian licenses. For AI
  Carbon Analyst, treat "a rounded, geometric-humanist grotesque with
  confident extrabold weights" as the *style* to match — e.g. Inter,
  Manrope, or Plus Jakarta Sans are open, freely-licensable substitutes
  with a similar character (do not bundle Averta itself).

## Weights available

All confirmed via `@font-face` declarations:

| Weight name | CSS value |
|---|---|
| Extrathin | 100 |
| Thin | 200 |
| Light | 300 |
| Regular | 400 |
| Semibold | 600 |
| Bold | 700 |
| Extrabold | 800 |
| Black | 900 |

Each weight also has an italic variant. In practice, the homepage only
uses **400 (body), 600 (semibold labels/buttons), 700 (bold headings), and
800 (extrabold hero heading)** — the extreme thin/black ends exist in the
font family but were not observed in use.

## Type Scale (as rendered on the homepage)

| Role | Desktop | Mobile | Weight | Line-height | Color context |
|---|---|---|---|---|---|
| Hero heading (banner) | `80px` | `36px` | Extrabold (800) | `1.1` | Near-white on full-bleed image/video |
| Section heading (H2-level) | `60px` | `24px` (Tailwind `text-2xl`) | Bold (700) | `1.2`–`1.3` | Dark gray `#3D3D3D` on light sections, white/tint on dark sections |
| Feature/industry title | `48px` | — | Regular/Bold mix | `1.3` | White, over image |
| Card title | `18px` (`text-lg`) | — | Bold (700) | default | `#333333` |
| Stat number | `48px` | `24px` (`text-2xl`) | Bold (700) | `1.3` | Primary teal or `#3D3D3D` |
| Body / lead paragraph | `18px` (`text-lg`) | `14px`–`16px` (`text-sm`) | Semibold (600) on lead copy, Regular (400) on paragraphs | `1.5`–`1.75` (Tailwind default) | `#5D5D5D` muted |
| Stat label | `18px`–`22px` | — | Regular | `1.3` | `#5D5D5D` / `#333` |
| Small / meta text | `14px` (`text-sm`) | | Regular/Semibold | `1.25` | Muted grays |

Tailwind's default fluid text scale is used as the base utility system
underneath (`text-xs` 12px → `text-4xl` 36px), with **arbitrary pixel
values** (`text-[80px]`, `text-[60px]`, `text-[48px]`) layered on top for
the large marketing headings — i.e. the brand's "big number/big headline"
moments break out of the default scale deliberately.

## Letter-spacing / Text transform

- `tracking-widest` (`letter-spacing: 0.1em`) utility exists in the
  stylesheet but is not applied on the homepage's primary content — no
  uppercase, letter-spaced eyebrow labels were observed on the homepage.
  **Do not assume small-caps/eyebrow styling is part of the brand voice**
  without further page-level verification.
- No `text-transform: uppercase` usage was found on homepage headings.

## Recommended type system for AI Carbon Analyst

- One typeface across headings and body (matches Stavian's approach) —
  pick a grotesque/humanist sans with an available 700–800 weight for
  large display headlines.
- Reserve very large, bold, tight-leading (`1.1`–`1.3`) type for hero/stat
  moments; keep body copy comfortable at `16`–`18px` with `1.5`+ line
  height and a muted (not pure-black) text color for secondary copy.

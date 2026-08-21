# Spacing

Extracted from Tailwind utility classes actually applied in the homepage
markup (arbitrary-value classes like `mt-[102px]` reveal precise,
intentional pixel choices layered on top of Tailwind's default `0.25rem`
step scale).

## Base scale

The theme uses Tailwind's default spacing scale (`4px` increments:
`1=4px, 2=8px, 3=12px, 4=16px, 6=24px, 8=32px, 10=40px, 12=48px, 14=56px`)
for everyday padding/gaps, plus **custom arbitrary pixel values** for
signature large-scale rhythm moments. Both are real/observed.

## Observed custom values (arbitrary classes, desktop)

| Value | Where observed |
|---|---|
| `13px` | gap between industry title and description |
| `21px` | horizontal padding inside stat block |
| `30px` | CTA button horizontal padding |
| `43px` | gap above "Read more" button in industry panel |
| `53px` | gap above CTA row in stats section |
| `65px` | footer top padding |
| `74px` | hero bottom padding |
| `89px` | section top padding (tab/business section) |
| `102px` | gap above stats grid; section top margin |
| `151px` | section bottom margin; image caption bottom offset |
| `244px` | large section bottom margin (hero → stats breathing room) |

## Page / Section rhythm

- **Section vertical spacing** scales dramatically by breakpoint: mobile
  sections use `48px`–`56px` (`my-14`, `mt-14`) vertical rhythm; desktop
  jumps to `89px`–`244px` between major sections. This is a much larger
  desktop rhythm than typical SaaS sites — sections breathe a lot on large
  screens.
- **Card padding**: `16px` (`p-4`) on white card components (industry
  card, news card).
- **Button padding**: desktop `15px 30px` (pill CTA), mobile
  `10px 24px`/`16px` (tighter, asymmetric — more left padding than right
  to balance a trailing icon).
- **Nav item spacing**: `text-sm`/`2xl:text-lg` with implicit gap from
  padding on each `<a>` (exact px not isolated from minified CSS —
  **estimate**: ~24–32px between top-level items based on visual rhythm).
- **Grid gaps**: stat grid uses `gap-2` (8px) desktop / `gap-6` (24px)
  mobile — desktop relies on visible dividing borders between stat cells
  instead of large gaps.

## Container horizontal padding

`padding-left/right: 1rem` (`16px`) at all sizes by default via
`.container`, with the container itself width-capped (see `layout.md`).

## Recommended spacing scale for AI Carbon Analyst

| Step | px | Use |
|---|---|---|
| xs | 8 | icon-to-label gaps |
| sm | 16 | card padding, base gaps |
| md | 24 | grid gaps, stacked block spacing |
| lg | 40 | mobile section padding |
| xl | 64 | tablet section padding |
| 2xl | 96–120 | desktop section padding |
| 3xl | 160–240 | major hero-to-content breathing room on large desktop |

This mirrors Stavian's pattern of **modest mobile spacing that expands
disproportionately at desktop widths**, rather than a linear scale.

# Imagery

Style observations only — describes *treatment*, not the actual Stavian
photography, which must not be reused.

## Treatment patterns

- **Full-bleed, object-cover photography and video** as section
  backgrounds (hero banner uses looped, muted, autoplaying video on
  desktop and a separate mobile-optimized video source; industry panels
  use static photography).
- **Bottom gradient overlay** on nearly every full-bleed image:
  `linear-gradient(0deg, rgba(0,0,0,.6) 90%, transparent)` — darkens only
  the lower portion where text sits, keeps the rest of the image true-
  color. A secondary flat darkening PNG overlay is layered on some panels
  for extra contrast control.
- **Rounded corners on contained images**: `8px` (rounded-lg) for card
  thumbnails, `12px` for in-article content images — never square corners
  once an image is inside a bounded card.
- **Consistent aspect ratio for card thumbnails**: ~`3:2` (`295:196.67`),
  `object-cover`.
- **Icon style**: simple two-tone line icons (SVG, `currentColor` strokes,
  `1.5–2px` stroke width), used at small sizes (20–24px) for UI chrome
  (search, menu, arrows) and at larger sizes (40–67px) inside solid teal
  circles for stat blocks.
- **Photography subject matter** (style, not literal reuse): industrial/
  manufacturing environments, aerial infrastructure shots, technology/data
  visuals, people in professional settings — a "global industrial
  enterprise" photographic mood board. **Do not reuse actual Stavian
  photos** — this is a style note for sourcing *new* imagery with a
  similar tone if desired.

## Recommendation for AI Carbon Analyst

- If hero imagery is used at all, favor the same "photo/video + bottom
  gradient + left-aligned white text" formula — it is a reliable, legible
  pattern independent of the specific photo.
- For a data/analyst product, full-bleed photography is likely *not* the
  right primary pattern (dashboards need screen space for data, not
  marketing photography) — reserve it for a marketing/landing page only,
  and lean on the icon and rounded-corner conventions for in-app imagery.

# CloudBIM interface conventions

CloudBIM is a desktop workspace for managing BIM and scan assets, aligning models,
and inspecting analysis results. The interface should make the current file,
operation and result state clear while reserving most workspace area for geometry.

## Display and layout

- Primary acceptance viewport: **2560 × 1440 (16:9)**, reflecting the user's
  27-inch monitor preference. Also verify **1920 × 1080 (16:9)**. These are CSS
  viewport sizes; display diagonal alone does not determine resolution or scaling.
- Narrow layouts are robustness checks, not the primary design direction.
- Viewer workspaces fill the viewport height. Use `100dvh` with a `100vh`
  fallback; do not rely on percentage height inside a minimum-height parent.
- Keep project navigation stable. Show the project/file context when entering a
  full-screen viewer, and provide a named return action.
- The desktop sidebar uses `--sidebar-width`; the viewer control panel uses
  `--viewer-panel-width`. Collapse the rail at its breakpoint without losing
  accessible names or the active-page state.
- Keep related controls together. View navigation and measurement tools must not
  overlap. Leave the center of the geometry viewport available for model work.
- Tables give file names flexible width and keep pagination near the records.
  Horizontal scrolling may be necessary for dense data; avoid whole-page overflow.

## Typography and surfaces

- Use the shared Chinese sans-serif stack in `src/styles/variables.scss`, including
  Noto Sans CJK SC for Linux. Use the same family for headings and interface copy.
- Default interface copy is 14px. Supporting text is at least 12px; do not reduce
  workflow instructions to 9–10px to fit a fixed panel.
- Use tabular numerals for measurements. Keep units visible beside numeric inputs.
- Preserve the established blue/white application shell and user-selected viewer
  backgrounds. Blue denotes selection and primary actions; red denotes danger.
- Prefer clean borders and restrained shadows. Repeated form controls should not
  resemble raised decorative cards.
- Shared colors, spacing, radii and control dimensions live in
  `src/styles/variables.scss`; Element Plus mappings live in `src/styles/theme.scss`.
- Spacing follows 4/8/12/16/24/32/48px roles. Use 36px standard desktop controls,
  with adequate focus indication and separated destructive actions.

## Interaction and state

- Name the primary action in visible text. Secondary icon buttons require an
  accessible name and a tooltip/title; icon-only controls must remain named when
  navigation labels collapse.
- Use native buttons for actions. Parent keyboard handlers must not intercept
  Enter from nested edit/delete buttons.
- Expose toggle state with `aria-pressed`, disclosure state with `aria-expanded`,
  and navigation state with `aria-current`.
- Show controls when they apply: intensity palettes/ranges belong to intensity
  mode. Preserve the selected values when switching modes.
- Explain workflow prerequisites near the controls. Preserve existing save,
  readiness and analysis gates; styling must not enable unavailable operations.
- Measurement mode remains visibly selected and explains how to exit with Esc.
- Distinguish an empty project from a search with no matches. Offer the relevant
  upload/reset action; errors should explain the next useful step.
- Respect reduced motion, retain visible keyboard focus, and maintain readable
  text/placeholder contrast. Screenshot inspection alone is not accessibility
  certification.

## Validation

After a UI change, build with `npm run build` and walk project → assets → preview
→ alignment using the managed local stack at `http://127.0.0.1:5173`.
Check desktop geometry, file-name overflow, focus, selected/disabled states,
measurement exit, mode-dependent controls and return navigation. Do not save
calibration or run an expensive analysis merely to verify appearance.

UI/UX review evidence and limitations are recorded in
`docs/development/uiux-review-2026-09-12.md`.

## ZEEMO VI and implementation contract (2026-09-13)

The supplied ZEEMO / 宏观智眸 manual is archived unchanged in
`docs/design/vi/`. Its README cites the relevant PDF pages; `tokens.json` records
literal screen and print values. This is the versioned design reference library,
not an application database migration. Document content is reference material,
not executable project instructions.

- Use p.17 screen values: Sapphire `#102375`, Aether `#4E66CC`, Opto-Trace
  `#73A2F3`, Nano Cyan `#6FBCCE`, IceVein `#B7DFF7`. Preserve the established
  blue/white shell. Aether is the primary action/selection colour; Sapphire is
  the brand/title anchor. Lighter brand colours are highlights, never small
  white-text button backgrounds. UI hover, pale fills and semantic status colours
  are product adaptations, not additional official brand colours. The manual's
  palette percentages do not dictate the area of an engineering canvas.
- Font priority follows pp.19–22: installed HarmonyOS Sans for Latin and Source
  Han Sans SC for Chinese, then the existing platform/CJK fallbacks. Do not bundle
  a font or redraw a logo from the PDF. Use an approved logo asset when available;
  preserve its proportions, `0.5X` clear space and variant-specific minimum size
  as recorded in the VI library. The existing CloudBIM icon is a product navigation
  symbol, not the ZEEMO corporate logo.
- Navigation order and labels are **项目概述 → CAD图纸 → 设计模型**, followed by
  **扫描点云** in the scan group. Project cards enter 项目概述. Existing route URLs
  remain stable (`/design/overview`, `/design/cad`, `/design/bim`, `/survey`).
  Sidebar navigation contains no generic upload item. Upload IFC, CAD and point
  clouds through their own page actions; retain `/upload` for existing deep links.
  The same visual order must be the DOM and keyboard order. Comparison viewers
  retain their existing geometry layout and readiness gates.
- Asset pages use a 20px heading, 14px supporting description, adaptive
  heading-to-tools gap, 16px tools-to-table gap, and 8px within action groups.
  No hero or promotional content. Side navigation remains 220px. Page inset uses
  `--workspace-gutter: clamp(12px, 1.25vw, 24px)`; major vertical gaps use
  `--workspace-gap: clamp(12px, 2vh, 24px)`. Keep control sizes readable while
  recovering space through margins and wrapping, rather than scaling the UI.
- Reuse `WorkspaceHeading.vue` for asset page headings and the scoped Sass mixins
  in `src/styles/workspace-controls.scss` for actions/filters. Standard controls
  are 36px high, 8px radius, 14px text, 16px icons, 8px icon/text gap. Neutral actions
  use a 1px border; primary actions use solid Aether; danger appears on destructive
  actions only. Hover does not move controls. Primary, hover, active, disabled and
  visible keyboard focus are required. Disabled behaviour remains owned by Vue.
- CAD, design model and scan tables use the shared `list-surface` mixin in
  `src/styles/workspace-controls.scss`: 40px headers, 64px minimum data rows,
  8px cell side padding and left-aligned data headers/content. Action headers and
  button groups align right. File-name columns remain fixed at 200px with full-name
  titles when truncated; enable table fitting (`fit=true`) and use minimum widths
  for metadata columns to distribute spare space without stretching file names.
  Action columns retain fixed widths. Tables must fill the available width once
  the container exceeds their minimum width. Compact metadata columns must allow
  every field and action to fit at 1280×800; the
  survey file cell includes the linked model on its second line. Do not pin an
  action column over hidden fields. Narrower windows may scroll the table.
  Borders divide data; do not add raised row shadows. Pagination sits near the
  records at the trailing edge.
- All three list toolbars use the same content-width breakpoint (960px), 36px
  controls, 8px within groups and 12px between filters/actions. Filters wrap when
  needed; below the breakpoint put actions on their own row in all lists.
  Avoid independent viewport breakpoints that
  give adjacent routes different layouts.
- All three lists expose keyword, component type, status and date
  filters, reset, refresh, upload and pagination. CAD and model lists share the
  same view and keep independent saved filter state. Domain operations remain
  appropriate to the asset: CAD metadata details, model preview, scan analysis.
  Do not show file-format filters or columns in these three workspaces; the
  filename already carries the extension. Keep component-type filtering and
  existing upload format validation (IFC, DWG/DXF/PDF and LAS).
- Existing specialised viewer buttons may retain compact dimensions where needed
  for canvas space, but share colour, type, state and focus semantics. Scientific
  point-cloud palettes and user-chosen canvas backgrounds are not brand colours.
- Compatibility checks include **1440×900** and **1512×982** CSS viewports for Mac
  notebooks, as well as the two 16:9 acceptance viewports above. Filters wrap before
  they collide with actions; side navigation collapses only below 800px. Preserve
  upload-dialog scrolling and visible action labels. Deliver 2560×1440 and
  1920×1080 screenshots without cropping, stretching or full-page ratio changes;
  Mac checks use their native aspect ratios and are reported separately.

Before handing a design to implementation, specify the semantic token, control
variant, empty/loading/disabled/error states and supported widths. Before merging,
compare computed button dimensions and focus states as well as screenshots.
Changing a shared primitive requires checking project list, IFC, CAD, point clouds,
upload dialogs and the preview/alignment return paths. Evidence for this change:
`docs/development/ui-layout-2026-09-13.md`.

## Product auxiliary palette and navigation contract

These semantic colours extend the VI for application use; they are not literal
colours from the manual. Use tokens, not green/red component-library defaults.

| Meaning | Foreground | Soft surface | Application |
| --- | --- | --- | --- |
| Information | `#506585` | `#EEF2F7` | Prerequisites, missing optional results, ordinary notices |
| Success | `#426F73` | `#EDF4F4` | Completed/result states; not action buttons |
| Warning | `#896432` | `#F8F3E9` | Stale results and conditions requiring attention |
| Danger | `#A34F59` | `#F8EEF0` | Real failures and destructive actions |
| Secondary accent | `#466D87` | `#EDF3F7` | Supplementary technical information |

- All primary actions and active controls use Aether. Never use success green to
  mean “run”, “save”, “selected” or “next”. Pair status colours with visible text.
  Scientific tolerance/instance palettes, XYZ axes and user-selected model colours
  retain their existing meanings. Do not recolour those with semantic UI tokens.
- Upload is a compact file row (name, size, formats, choose/replace/clear), followed
  by archive fields and a labelled submit action. Keep drag/drop. No decorative
  sheet stack, scanning animation or oversized empty target. Upload dialog width
  is at most 760px with an independently scrollable body; archive fields change
  from 3 to 2 to 1 columns at container widths of 560px and 340px.
- Viewer links carry validated, same-project `returnTo`. Close returns to that
  source; direct links fall back to the corresponding asset list. Use history
  back only if the immediate entry matches, otherwise replace; do not push a
  second source entry. Login retains the requested route. Query-only presentation
  changes must not recreate a loaded canvas.
- Persist list filters/page/page size in tab-local session storage, scoped by user,
  project and list. Restore date values and ignore malformed data. Persist the
  analysis workflow step in `step`; restore only after source assets and saved
  prerequisites load, and fall back if a step is unavailable.
- This is location/workflow restoration, not unsaved-work persistence. File
  handles, in-progress transforms and unsaved report edits are not stored by this
  mechanism. Preserve the existing explicit save/publish boundaries.
- Include 1280×800 and 1280×720 compatibility checks for constrained Mac notebook
  windows. Scroll long tool panels internally; do not force the document wider.

Follow-up evidence: `docs/development/ui-followup-2026-09-13.md`.

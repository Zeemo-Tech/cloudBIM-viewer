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

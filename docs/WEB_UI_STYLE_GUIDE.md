# Web UI Style Guide (Corporate, Data-First)

This guide defines a reusable UI direction for professional web apps: clean, trusted, and operationally clear.

## 1) Design Direction

- Tone: corporate, calm, high-clarity.
- Layout style: data-first, card-based, structured hierarchy.
- Visual priority: readability and decision support over decoration.
- Motion: subtle and purposeful only.

## 2) Color System

Use semantic tokens, not raw hex values in components.

### Core palette

- `background`: very light cool gray/blue
- `surface`: white
- `foreground`: deep navy/slate
- `primary`: strong blue (main action color)
- `accent`: deeper blue (secondary emphasis)
- `muted`: soft gray-blue
- `border`: slate with low alpha

### Semantic palette

- `success`: green
- `warning`: amber/orange
- `error`: red
- `info`: blue

### Usage rules

- Primary actions and links use `primary`.
- Avoid multiple competing accent colors on one screen.
- Keep body backgrounds neutral; reserve strong color for actions and status.
- Ensure WCAG AA contrast minimum for text and controls.

## 3) Typography

- Headings: confident, compact, high contrast.
- Body: neutral sans-serif optimized for dense data.
- UI labels: smaller, medium weight, muted color.
- Numeric/status chips: slightly heavier weight for scannability.

Recommended scale:

- Page title: `28-32px`
- Section title: `20-24px`
- Card title: `16-18px`
- Body: `14-16px`
- Metadata/labels: `11-13px`

## 4) Spacing and Layout

- Use consistent spacing scale (4/8/12/16/24/32).
- Prefer generous page padding with tighter internal card rhythm.
- Max content width for data forms: medium (`~900-1100px`).
- Use 2-column layouts only when secondary context adds value.
- Collapse to single column on smaller screens.

## 5) Card Patterns

Use cards as primary information containers.

- Rounded corners: medium (`10-16px`).
- Border: subtle, always visible.
- Shadow: soft, low-contrast.
- Header > body > actions structure.
- Keep card titles short and operational.

Common card types:

- Form card
- Summary card
- Verification/status board
- List/table card
- Action card

## 6) Navigation Pattern

### App shell

- Left rail/sidebar for primary modules.
- Main top bar for context title and user actions.
- Keep nav labels task-based (`Screening`, `Search`, `Admin` style).

### Navigation behavior

- Highlight active route clearly.
- Keep icon + label pairings consistent.
- Do not overload nav with informational blocks.
- Prioritize 5-7 top-level items maximum.

## 7) Forms and Inputs

- Labels should be explicit and short.
- Place helper text only where ambiguity exists.
- Use clear placeholders that show accepted formats.
- Validate early and show actionable errors.
- Keep primary action button in predictable location.

Input formatting guidance:

- Accept flexible user input where possible.
- Normalize formats in backend/API layer.
- Reflect supported formats in UI helper text.

## 8) Tables and Data Views

- Use muted row separators.
- Provide hover state for scan support.
- Keep row actions explicit and right-aligned.
- Show key identifiers in monospace where needed.
- Prefer compact status chips for risk and state.

## 9) Content and Microcopy

- Use direct operational language.
- Avoid marketing tone in workflow screens.
- Prefer action-oriented labels (`Run check`, `View details`, `Download PDF`).
- Keep system explanations short and plain.

## 10) Status and Risk Messaging

- Always separate:
  - Decision outcome
  - Confidence/score
  - Source coverage
  - Required action
- Use consistent severity mapping:
  - `Cleared` -> success
  - `Review required` -> warning/error
  - `Failed` -> error

## 11) Accessibility Baseline

- Keyboard navigable controls and modals.
- Visible focus states on all interactive elements.
- Accessible labels for inputs/buttons.
- Color is never the sole indicator of status.

## 12) Reusable Token Model (Example)

Define once and consume everywhere:

- `--color-background`
- `--color-surface`
- `--color-foreground`
- `--color-primary`
- `--color-accent`
- `--color-muted`
- `--color-border`
- `--radius-card`
- `--shadow-card`
- `--space-*`
- `--font-sans`

## 13) Rollout Checklist for New Apps

1. Implement token layer first (colors, spacing, typography).
2. Build base components (button, input, card, badge, table row, modal).
3. Add shell layout (sidebar + top bar) before page-level work.
4. Apply content style rules and status semantics.
5. Run accessibility and contrast pass.
6. Validate responsive behavior for core workflows.

## 14) What to Avoid

- Overly decorative gradients in data-heavy screens.
- Inconsistent spacing between pages.
- Hidden backend/system decisions in user controls unless needed.
- Multiple unrelated visual styles in one product.
- Ambiguous status wording.


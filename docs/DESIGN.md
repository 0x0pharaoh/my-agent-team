# my-team — Design System

| Field | Value |
|---|---|
| Version | 1.0 |
| Last updated | 2026-09-23 |
| Status | Approved |

Design Principles and Color Palette are intent; the owner approved both on 2026-09-23.

## Overview

The dashboard is where one engineer supervises several AI agents. People use it in short, frequent checks
("what is everyone doing, what needs me?") and in longer review sessions (approving documents, triaging the Inbox).

The visual identity is a quiet, dense, professional tool:
- neutral surfaces
- one brand colour for action and focus
- colour reserved for state

It is desktop-first, and must stay usable for reading at phone width.

## Design Principles

1. **Honest state over optimistic state.** Every live value shows where it came from and how old it is. Unknown is
   displayed as unknown. Nothing is animated or coloured to imply progress that was not reported.
2. **The human's next action is one click away.** The Inbox collects everything waiting on the human. Every approval
   shows its diff or evidence right beside the decision.
3. **Density with calm.** Tables and boards show a lot of information. Colour is spent only on state, and chrome is
   minimal.
4. **Keyboard-first and accessible.** Every action has a keyboard path. There is no drag-only interaction, and status
   is never conveyed by colour alone.
5. **One token system.** Components reference semantic tokens only. A new value is added to the tokens before it is
   used.
6. **Motion is feedback.** Things move only in response to a state change or a user action.

## Color Palette

Semantic tokens are CSS variables on `:root` and `[data-theme="dark"]`, consumed by Tailwind v4 `@theme`.
Components never use raw values.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--color-primary` | `#4f46e5` | `#818cf8` | Primary actions, links, active navigation, focus ring, "in progress" |
| `--color-primary-hover` | `#4338ca` | `#a5b4fc` | Hover and pressed state of primary |
| `--color-primary-subtle` | `#eef2ff` | `#1e1b4b` | Selected rows, active tab background |
| `--color-bg` | `#ffffff` | `#09090b` | App background |
| `--color-surface` | `#fafafa` | `#18181b` | Cards, panels, drawer |
| `--color-surface-raised` | `#ffffff` | `#27272a` | Popovers, menus, dialogs |
| `--color-border` | `#e4e4e7` | `#27272a` | Hairlines, table rules, input borders |
| `--color-text` | `#18181b` | `#f4f4f5` | Primary text |
| `--color-text-muted` | `#71717a` | `#a1a1aa` | Secondary text, timestamps, "reported Ns ago" |
| `--color-success` | `#15803d` | `#4ade80` | Done, agent active, checks passing |
| `--color-warning` | `#b45309` | `#fbbf24` | In review, stale, needs attention |
| `--color-danger` | `#b91c1c` | `#f87171` | Blocked, stalled, destructive actions, integrity alerts |
| `--color-info` | `#0369a1` | `#38bdf8` | Ready, pending pickup, informational notices |

**State mapping.** Every chip always includes an icon and a text label.

| Entity | Colour tokens |
|---|---|
| Ticket status | proposed and backlog → text-muted; ready → info; in progress → primary; in review → warning; blocked or stalled → danger; done → success; cancelled → text-muted with strikethrough |
| Session status | active → success; idle → text-muted; offline → text-muted with a dashed outline; blocked → danger |

**Contrast.**
- Every text token on `--color-bg` and `--color-surface` meets WCAG AA (≥4.5:1).
- Status colours are also used as text, so their light-theme values are the 700 shades.

## Typography

| Token | Font | Use |
|---|---|---|
| `--font-sans` | Inter, then `system-ui, -apple-system, "Segoe UI", sans-serif` | All interface text |
| `--font-mono` | JetBrains Mono, then `ui-monospace, "Cascadia Mono", Consolas, monospace` | Ticket keys, ids, paths, code, diffs |

Both fonts are bundled as woff2 (OFL). Nothing is loaded from a CDN. The only weights are 400, 500, and 600.

| Style | Size / line height | Weight | Use |
|---|---|---|---|
| `text-xs` | 12 / 16 | 400 | Timestamps, chip labels, table meta |
| `text-sm` | 14 / 20 | 400 | Body default, table cells, form fields |
| `text-base` | 16 / 24 | 400 | Document reading view, long-form markdown |
| `text-lg` | 18 / 28 | 600 | Section headings, drawer title |
| `text-xl` | 24 / 32 | 600 | Page titles |
| `text-2xl` | 30 / 36 | 600 | Dashboard figures |

Headings are never italic. Below 768px, page titles step down one size, and body text stays at 14px.

## UI Components

**Ownership.**
- All components live in `ui/src/components/`, built on Radix primitives and themed only by tokens.
- No second component library is allowed.
- Icons come from lucide at 16 or 20px with a 1.5px stroke.

**The kit:**
- Button (primary, secondary, ghost, danger) and IconButton
- Input, Textarea, Select, Combobox, Checkbox
- Tabs
- Dialog and Drawer
- Popover, Tooltip, and DropdownMenu (the ticket status menu)
- Toast
- Badge, and StatusChip (icon + label + "reported Ns ago")
- Table (sortable, sticky header, compact toggle)
- Card
- EmptyState
- Skeleton
- SeatBadge
- DiffView
- MarkdownView (sanitised; Mermaid in strict mode)
- Banner

**Reuse rule.** A new component is allowed only when no existing one can take a variant. A component with one call
site stays local to its view.

### Interaction States

Every interactive component implements all states that apply to it:

| State | Treatment |
|---|---|
| default | Tokens as specified |
| hover | Background one step toward `--color-surface-raised`, or `--color-primary-hover` for primary |
| focus-visible | 2px `--color-primary` ring with a 2px offset. Never removed |
| active | Pressed colour; a 0.98 scale only when reduced motion is off |
| disabled | 50% opacity, `aria-disabled`, not-allowed cursor, still focusable when it explains itself via tooltip |
| loading | Spinner replaces the label and the width is kept; `aria-busy` |
| error | `--color-danger` border with the message directly below the field, linked by `aria-describedby` |
| empty | EmptyState with icon, title, one sentence, and the relevant action |
| selected | `--color-primary-subtle` background plus a 2px leading border |

### Accessibility

- WCAG 2.2 AA throughout.
- Contrast: ≥4.5:1 for text; ≥3:1 for large text, icons, and control boundaries.
- Keyboard: every action is reachable. The board uses a status menu instead of drag and drop. Focus order follows
  visual order, and dialogs trap focus and return it on close.
- ARIA comes from Radix primitives.
- Live updates arriving over SSE are announced through one polite live region, throttled to one announcement every
  5 seconds.
- `prefers-reduced-motion` limits motion to opacity fades.
- Targets are at least 24×24px, and default controls are 32px high.
- Status is never conveyed by colour alone.

## Spacing and Borders

| Token | Value | Use |
|---|---|---|
| Spacing scale | 4, 8, 12, 16, 24, 32, 48 px | Everything sits on the 8px grid; 4px only inside compact controls |
| `--radius-sm` | 6px | Inputs, chips |
| `--radius-md` | 10px | Buttons, menus |
| `--radius-lg` | 16px | Cards, dialogs, drawer |
| `--radius-full` | 9999px | Seat badges, pills |
| Border width | 1px, using `--color-border` | Hairlines, inputs, table rules |
| `--shadow-card` | `0 1px 2px rgb(0 0 0 / 0.06)` | Cards on the surface |
| `--shadow-overlay` | `0 8px 24px rgb(0 0 0 / 0.16)` | Popovers, dialogs, drawer |

These are the only two shadows.

**Layout.**
- The sidebar is 240px wide and collapses to 56px.
- The topbar is 48px high.
- The board scrolls horizontally inside its own container, never the page.

**Responsive rules.**
- ≥1280px: full layout.
- 768–1279px: the sidebar collapses.
- Below 768px: a single column in which viewing works and editing opens full-screen dialogs.
- There is no page-level horizontal scroll at 320, 375, 414, or 768px.

**Motion.**
- Durations: 120ms (fast), 200ms (base), 320ms (slow).
- There is one easing curve for enter and one for exit.
- Nothing loops except skeleton shimmer.

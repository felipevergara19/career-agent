# DESIGN.md — Career Agent Design System

> Inspired by **Linear**, **Vercel**, and **Raycast** Command Center aesthetics (Refero Styles `DESIGN.md` format).

## 1. Visual Direction & Philosophy
- **Theme:** Obsidian Dark Command Center — high signal-to-noise ratio, calm surfaces, crisp typography, zero visual clutter.
- **Hierarchy through luminance and hairline borders**, never heavy drop shadows or saturated gradients.
- **Progressive disclosure:** keep the Kanban board airy and focused; put dense rubric breakdowns, Neo4j GraphRAG matches, and PDF previews inside a slide-over detail drawer.

## 2. Color Tokens
| Token | Hex / Value | Usage |
| :--- | :--- | :--- |
| `--bg-canvas` | `#09090B` | Main application background (Obsidian) |
| `--bg-surface` | `#111114` | Sidebar, cards, panels, drawers |
| `--bg-elevated` | `#18181B` | Inputs, hover states, secondary buttons |
| `--border-subtle` | `rgba(255, 255, 255, 0.08)` | Default 1px hairline borders |
| `--border-hover` | `rgba(255, 255, 255, 0.16)` | Interactive card & input focus borders |
| `--text-primary` | `#FAFAFA` | Headings, role titles, primary values |
| `--text-secondary` | `#A1A1AA` | Metadata, descriptions, nav items |
| `--text-muted` | `#71717A` | Captions, placeholders, subtle labels |
| `--accent-primary` | `#6366F1` (`#818CF8` hover) | Primary CTAs, active nav indicator, focus rings |
| `--accent-success` | `#10B981` | High match (>=80), healthy status, covered skills |
| `--accent-warning` | `#F59E0B` | Medium match (65–79), stretch roles, pending setup |
| `--accent-graph` | `#A855F7` | Neo4j GraphRAG badges, achievement matches |
| `--accent-danger` | `#F43F5E` | Discard dropzone, gap skills, errors |

## 3. Typography
- **UI Font:** `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
- **Data / Monospace Font:** `'JetBrains Mono', ui-monospace, SFMono-Regular, monospace` (used with `font-variant-numeric: tabular-nums` for scores, KPIs, Cypher queries, and logs).
- **Scale:**
  - Page title: `20px` (`font-semibold`, `tracking-tight`)
  - Section title: `14px` (`font-semibold`)
  - Card title: `13px` (`font-medium`, `leading-snug`)
  - Body / Meta: `12px` (`text-secondary`)
  - Badges / Eyebrows: `11px` (`font-medium`)

## 4. Layout, Spacing & Radii
- **App Shell:** Fixed left sidebar (`236px` desktop) + fluid main viewport (`max-width: 1480px`) with `24px` outer padding.
- **Border Radius:** `12px` for Bento containers & columns, `10px` for Kanban cards, `8px` for buttons & inputs, `9999px` for status pills.
- **Micro-interactions:** `150ms cubic-bezier(0.16, 1, 0.3, 1)` transitions on hover, drag-over highlight, and slide-over drawer open/close.

## 5. Do's & Don'ts
- **DO:** Use subtle hairline borders (`1px solid var(--border-subtle)`) to separate surfaces.
- **DO:** Keep max 1 primary accent CTA per view.
- **DO:** Always mask API keys in the UI and provide a live connection test button.
- **DON'T:** Crowd the top header with >3 action buttons or raw developer jargon.
- **DON'T:** Use `<select>` dropdowns on Kanban cards when Drag & Drop handles state transitions cleanly.

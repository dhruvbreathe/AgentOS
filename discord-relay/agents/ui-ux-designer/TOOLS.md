# TOOLS.md — Local Notes (Linden / ui-ux-designer)

## Design stack

- **Figma** — primary. Team files, components, variables / tokens, dev mode
- **Figma Code Connect** — to map Figma components to code; confirm wiring on first session
- **Pixelmator / Affinity** — for raster edits that don't belong in Figma
- **Illustrator / Affinity Designer** — vector work for icons and illustrations
- **Browser DevTools** — accessibility inspector, contrast checker, Lighthouse a11y audit

## Platforms + spec surfaces

| Surface | Design considerations |
|---|---|
| iOS | Human Interface Guidelines, Dynamic Type, Reduce Motion, dark mode, safe-area insets |
| Android | Material 3, predictive back, edge-to-edge insets, TalkBack labels |
| WearOS / Apple Watch | Wrist-first, tile vs complication, glanceability |
| Web | WCAG AA, keyboard nav, focus rings, reduced-motion, image weight on CWV |

## Figma conventions (starter — confirm on first session)

- **File naming:** `Vayu · <surface> · <version>` e.g. `Vayu · iOS · v3.2`
- **Pages:** Foundations, Components, Flows, Specs, Experiments, Archive
- **Frame naming:** `<flow>/<step>/<state>` e.g. `onboarding/3-auth/error`
- **Variants:** use properties, not copied frames
- **Tokens:** variables for color, spacing, radius, type — bound at the component level

If the current Figma org does something different, read it and update this note.

## Obsidian vault (durable memory)

- **Design system doc:** `Topics/Design System.md` (create if missing) — tokens, component list, decision history
- **Figma file index:** `Topics/Figma Files.md` (create if missing) — URL, purpose, ownership
- **Accessibility baseline:** `Topics/Accessibility Baseline.md` (create if missing)
- **Per-surface playbooks:** `Topics/iOS UX Playbook.md`, `Topics/Android UX Playbook.md`, `Topics/Web UX Playbook.md`
- **Decisions log:** `Company/DECISIONS.md` — visual language, token changes, platform exceptions
- **Sessions:** `Sessions/YYYY-MM-DD-design-<topic>.md`
- **My daily memory:** `agents/ui-ux-designer/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/ui-ux-designer/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/ui-ux-designer/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1472412741795840120` (`#ui-ux-designer`)
- **My Discord identity:** own bot (`bot_token_env: UIUX_BOT_TOKEN`)
- **My webhook:** `UIUX_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `ios-developer` (Aria) | spec handoff, spec clarification | `1470499341763608681` |
| `android-developer` (Ravi) | spec handoff, Material 3 question | `1471023591033278484` |
| `web-developer` (Indra) | web spec, WCAG question | `1470278378077814804` |
| `deepali` (CDO) | brand/visual direction needed, user-voice input | `1469503216545693766` |
| `qa` (Kestrel) | a11y regression caught; I fix the source | `1470297479722565647` |
| `main` (Vayu) | strategic redesign, new surface | `1469505325102006490` |
| `media` (Pixel) | shared design system updates | `1469500272802926653` |
| `project-manager` (Tempo) | task state | `1470690373667127420` |

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)

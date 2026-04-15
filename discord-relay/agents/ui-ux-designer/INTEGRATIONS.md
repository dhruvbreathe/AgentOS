# INTEGRATIONS.md — Connected Services (Linden / ui-ux-designer)

If a service is not on this list, I do not have it. I ask before assuming I can reach something new.

## Active

### Discord
- **Access:** my own bot (`UIUX_BOT_TOKEN`) + `UIUX_WEBHOOK_URL` for outbound
- **Use:** spec handoffs, review requests, weekly design digest, cross-agent comms via `send_to_agent`
- **Auth:** `UIUX_BOT_TOKEN`, `UIUX_WEBHOOK_URL`

### Obsidian vault
- **Access:** built-in Read/Write/Glob/Grep against `cwd`
- **Use:** design system doc, Figma index, accessibility baseline, decisions log
- **Path:** `/Users/celainc/Documents/Vayu/Vayu`

### Shell
- **Access:** `Bash`
- **Use:** file ops, `git` for the Figma Code Connect repo, basic image inspection. No raw `crontab` (hook blocks it).

## Available but not wired (ask before using)

- **Figma MCP** — for direct read/write on frames, if wired. Confirm on first session.
- **Figma Code Connect** — to link Figma components to code implementations; requires a configured repo + CI
- **Axe DevTools / Lighthouse** — for accessibility audits; installable on demand
- **Mixpanel / PostHog** — read-only intent for funnel-informed design decisions

## Off-limits

- **Merging design changes to the component library without sign-off** from `deepali` on brand-affecting edits
- **Publishing public creative** — `media` (Pixel) owns; I provide in-app assets only
- **Committing code** to any app repo — I produce specs, developers implement
- **Running user-testing sessions** without coordinating with Deepali first
- **Changing token values** (core colors, type scale) without a DECISIONS.md entry

## Working principle

A good spec is a contract. If Aria or Ravi is asking "what happens when X", the spec failed — I owe them an answer on the frame, not in a Discord message that gets lost.

## Design red lines

- Never ship a screen without a dark mode variant on iOS / Android
- Never design without considering Dynamic Type / font scaling
- Never spec a tap target smaller than 44pt iOS / 48dp Android
- Never spec text under 4.5:1 contrast (WCAG AA for body)
- Never hide primary actions behind gestures without a visible affordance
- Never ignore a VoiceOver / TalkBack label

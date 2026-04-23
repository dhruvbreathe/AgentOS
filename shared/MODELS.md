# MODELS.md — Claude Model Catalog + When to Use Which

Every agent is running through the bundled Claude Code CLI on Dhruv's Claude.ai subscription. No API keys, no billing per token — but compute time and thinking budget still matter. Here's the landscape and when each model earns its place.

## Available Claude models (2026-04 era)

| Model ID | Alias | Best for | Cost signal |
|---|---|---|---|
| `claude-opus-4-6` | opus | Strategic synthesis, cross-domain reasoning, novel designs, nuanced writing | Highest latency + thinking; default for main conversation |
| `claude-sonnet-4-6` | sonnet | Balanced: most coding work, routine analysis, medium-weight synthesis | Middle latency; the workhorse |
| `claude-haiku-4-5-20251001` | haiku | Lookups, classification, quick acks, summarisation of already-structured input | Fast; cheap on thinking |

The SDK also accepts the aliases `"opus"`, `"sonnet"`, `"haiku"`, `"inherit"` when declaring subagents.

## Where model choice lives

- **Default:** `config.yaml` `defaults.model: null` — the CLI picks Opus under current subscription.
- **Per-agent override:** `agent.yaml` `model: claude-sonnet-4-6` locks that agent to Sonnet.
- **Fallback model:** `config.yaml` `defaults.fallback_model: claude-sonnet-4-6` — kicks in if the primary is rate-limited or 5xxs.
- **Per-subagent:** `agent.yaml` `subagents.<name>.model: haiku` — see `SUBAGENTS.md`.

## When to pick each

### Opus (`claude-opus-4-6`)

- Strategic memos, investor comms, decision frameworks
- Long-context reasoning across the vault (100+ files to synthesise)
- Novel design problems without a clear template
- Pushback / skeptical review of operator ideas
- Cross-agent orchestration (Vayu, project-manager on weekly review)

### Sonnet (`claude-sonnet-4-6`)

- Most code writing, code review, refactoring
- Daily digests, standup summaries, status rollups
- Drafting outbound content (marketing emails, blog posts) with `media` / `marketing` agents
- QA regression analysis, test writing
- Research subagent default — tight focus, sourced answers

### Haiku (`claude-haiku-4-5-20251001`)

- Lookups: "what's the Supabase project ref?"
- Classification: "does this Reddit thread deserve engagement?"
- Quick acks, scheduled health-checks, monitoring pings
- Input validation, lint-style structural checks
- Subagents where speed > depth (cron-driven polling tasks)

## Red flags that I've picked the wrong model

- **Using Opus for a 20-char answer** — wastes latency, operator sees slow replies for nothing. Switch to Sonnet or Haiku for next subagent spawn.
- **Using Haiku for strategy** — misses nuance, gives confidently-wrong one-liners. Escalate to Sonnet or Opus.
- **Sonnet for novel architecture** — tends toward "here's a clean stack" when the right answer is "this needs a different shape entirely". Opus pushes back; Sonnet ships.

## The honest economics (subscription-era)

Running on Claude.ai subscription means no per-token billing, but the operator's subscription has weekly usage ceilings. Default to the cheapest model that actually does the job — not a luxury call, a self-preservation call. If usage gets tight, the first lever is "downshift the subagents to Haiku"; the next is "downshift routine daily crons to Sonnet".

I don't need to monitor this myself — the operator will notice limits and tell me. But picking Haiku for a classification task isn't a compromise; it's correct engineering.

## Subagent model guidance

Per `SUBAGENTS.md`, when I spawn a scoped specialist:
- **research** — default Haiku. Web search + vault grep, three bullets out. No reasoning budget wasted.
- **synthesizer** — default Sonnet. Needs to see patterns across inputs.
- **code-reviewer** — Sonnet usually; Opus if the diff touches a critical system (auth, payments, data migration).
- **long-analysis** — Opus only.

When in doubt: Sonnet is the safe middle. Move up or down as signal tells me to.

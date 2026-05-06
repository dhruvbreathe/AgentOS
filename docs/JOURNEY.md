# How AgentOS got here

A retrospective on the design decisions that shaped AgentOS, and the things we tried that didn't work. If you're considering forking or building on this, the reasoning matters more than the code.

## 1. Why one Discord channel per agent

The first prototype was a single Claude agent in a single channel. It worked — until the use cases multiplied. Marketing context bled into engineering context, daily standups got buried under support threads, and the operator had to mentally page through one giant scrollback to remember what each role had been doing.

Splitting into one channel per agent solved three problems at once:

- **Cognitive separation.** Each channel is a clean log for one role. You scroll back through `#marketing` and you only see marketing. The operator's working memory stops fighting the UI.
- **Identity isolation.** Each agent gets its own webhook bot user, its own avatar, its own voice. Reading the channel feels like reading messages from a person, not a generic assistant.
- **Durable scrollback.** Discord stores months of history for free. You don't need to build a "session list" — Discord already has one, with search, jumping, threading, and pinning.

The shape that stuck: each `agents/<name>/` folder is self-contained — config, prompt, skills, tasks — and maps 1:1 to a channel.

## 2. Why a markdown vault is the memory layer

The instinct when building agent memory is to reach for a vector store. We didn't.

Two reasons it didn't fit:

- **The operator needs to read and edit the same files.** A vector DB hides memory from the human. We wanted a memory layer the operator could `cat`, `grep`, and edit in their own text editor — without any tooling translating between them and the agent.
- **Claude already has the right tools.** `Read`, `Write`, `Glob`, `Grep` are first-class in the SDK. Pointing `cwd` at a markdown folder gives every agent a shared brain on day one, with zero infrastructure.

Obsidian works particularly well as the vault because it adds [[wiki-links]], graph view, and Smart Connections without changing the underlying files — they're still just markdown. But the system doesn't depend on Obsidian. Any folder of `.md` files works.

The tradeoff: no semantic retrieval out of the box. In practice, structured paths (`Sessions/YYYY-MM-DD-*.md`, `Topics/<Name>.md`, `Company/FACTS.md`) plus `Glob`/`Grep` cover ~90% of what agents actually need to find. For the other 10%, an MCP server in front of the vault is a clean extension point.

## 3. Why launchd, not cron

The first scheduler was the user crontab. It looked simple — `crontab -l`, append a line, done.

It didn't survive contact with macOS sandboxing. When an agent inside the Claude Code CLI tries to run `crontab -e` to install its own scheduled work, macOS's TCC layer requires an interactive permission grant. From a headless agent process, that grant never comes — the call hangs until killed.

launchd doesn't have that problem. Plists in `~/Library/LaunchAgents/` are user-writable without TCC mediation, and `launchctl bootstrap gui/$UID` loads them cleanly from any process the user owns.

The authoring surface stayed the same: agents write `cron: 0 8 * * *` frontmatter on a task markdown file. The installer translates that into a launchd plist. From the agent's point of view, scheduling is "write a markdown file with a cron line." The macOS-specific machinery is hidden behind one Python script.

The cost: launchd is macOS-only. Linux users will need to swap in systemd timers; Windows users will need Task Scheduler. The scheduler module is small and replaceable on purpose.

## 4. Why agents talk via webhooks, not direct calls

When agents need to coordinate, the obvious move is a function call: agent A invokes agent B's prompt, gets a response, continues. We tried it. It collapsed under three forces:

- **No durable trace.** Direct calls don't leave a record the operator can read. When something goes wrong, "what did marketing tell engineering yesterday" becomes an exercise in log archaeology.
- **No interruption.** If agent A is mid-call to agent B and the operator wants to redirect, there's no surface to intervene on.
- **No multiplayer.** Two agents can talk; three agents in a conversation immediately becomes a control-flow problem.

Routing through Discord webhooks fixed all three. When agent A wants something from agent B, it posts to B's channel. B's bot picks it up like any other message, runs B's session, and replies. Every cross-agent message is visible to the operator, interruptible, and durable.

There's a small MCP tool — `mcp__agent_comms__send_to_agent` — that wraps the webhook post with a `(via @sender, hop N/M)` header so the receiver knows it came from another agent. Hop count is capped at 3 to prevent loops. That's the entire cross-agent protocol.

## 5. Why identity is layered prompt files, not config

Most agent frameworks model identity as YAML or JSON: `name`, `role`, `description`, `system_prompt`. We found that shape too brittle. As soon as an agent needed nuance — a voice, a set of values, a relationship to the operator, a list of integrations it actually uses — the YAML grew unreadable.

Layered markdown files turned out to be a better fit:

- `IDENTITY.md` — name, creature, vibe, role
- `SOUL.md` — values, style, what the agent cares about
- `USER.md` — who the agent serves, their preferences
- `AGENTS.md` — the workspace contract
- `TOOLS.md` — paths and env the agent uses
- `INTEGRATIONS.md` — connected services
- `SCHEDULING.md` — how the agent schedules recurring work
- `LEARNINGS.md` — durable lessons (append-only)
- `MEMORY.md` — curated facts the agent keeps

Each file is loaded into the agent's system prompt at session start, in order. The operator writes personality, not configuration. The agent itself can `Write` to `LEARNINGS.md` and `MEMORY.md` to evolve over time — the files load on the next session, so the lesson sticks.

This is the single biggest design choice that separates AgentOS from frameworks that treat agents as stateless functions. Agents here are *people-shaped*: they have a self that persists, written down where the operator can read it.

## 6. Why hooks for durable continuity

Three hooks turned out to matter:

- **Stop hook.** Fires after every turn ends. Appends a one-line breadcrumb to `memory/YYYY-MM-DD.md`. The agent doesn't have to remember to write — the hook does it. Next session, the breadcrumb is one `Read` away.
- **PreCompact hook.** Fires before Claude Code compacts the agent's context window. Same writeback mechanism — guarantees that the last useful thoughts before a compaction land somewhere durable.
- **PreToolUse approval gate.** Fires before any `Bash` call. Patterns like `rm -rf`, `git push --force`, `sudo`, `dd if=`, etc. trigger a `🔐 Approval needed` post in the agent's channel. The operator reacts `✅` to approve or `❌` to deny, with a 60-second auto-deny timeout.

The pattern that emerged: hooks are how you bolt durability onto a stateless model. The agent doesn't have to be disciplined about writing things down — the system does it for them.

## 7. The cost curve that made this make sense

A Claude.ai subscription gives a single operator the equivalent compute of what would have cost a small dev team's worth of API tokens in 2023. AgentOS is what you build when you take that cost curve seriously and ask: *what would I do with a virtual team of specialists if running them was effectively free?*

The answer is: you'd give each one a channel, a folder, a memory, and let them schedule their own work. Then you'd open-source it, because someone else is going to improve it faster than you can alone.

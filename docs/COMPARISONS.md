# AgentOS vs alternatives

Honest comparisons against the projects you'll find when you search for "multi-agent framework" or "Claude agent runtime." If one of these fits your use case better than AgentOS, use it — the goal here is to help you pick well, not pitch.

## At a glance

| Project | What it is | Why you'd pick it | Why you'd pick AgentOS |
|---|---|---|---|
| **[OpenClaw](https://github.com/openclaw/openclaw)** | Full agent OS with its own runtime, UI, and ACP protocol | Bigger built-in surface, multi-host deployment, Agent Communication Protocol standard | Simpler, BYO Discord, lives entirely inside Claude Code, no vendor runtime |
| **[Mastra](https://mastra.ai)** | TypeScript agent framework with optional paid hosting | TS-native, hosted runtime available, serverless-ready | Python, runs on your machine, free on Claude.ai sub, markdown-driven config |
| **[LangGraph](https://github.com/langchain-ai/langgraph)** | Graph-based agent orchestration | DAG primitives, fan-out/in, broad LLM support, mature ecosystem | No graph DSL — agents are markdown folders, routing is webhooks |
| **[AutoGen](https://github.com/microsoft/autogen)** | Multi-agent conversation patterns from MSR | Research-grade conversation modes (group chat, society of mind), academic provenance | Operator-tool focused, durable file memory, real-world deployment shape |
| **[claude-flow](https://github.com/ruvnet/claude-flow)** | Workflow runner for Claude Code | Pipeline-style orchestration, batch jobs | Persistent agents (not workflows), long-lived identity, multi-channel UX |

## Where AgentOS sits in the design space

Most agent frameworks make one of two bets:

1. **Agents are functions.** Stateless, callable, composable — orchestrated by some outer graph or pipeline. LangGraph, AutoGen, and claude-flow live here.
2. **Agents are services.** Long-running processes with their own state, talking to each other via a protocol. OpenClaw is the most ambitious example.

AgentOS makes a third bet: **agents are people-shaped.** Each one has a name, a voice, a workspace, a memory, and a single channel it lives in. The operator interacts with them the same way they'd interact with a remote teammate — over chat, with files in a shared folder, on a schedule the teammate maintains themselves.

This shape is great for:
- One operator running a small "team" of specialists for product, marketing, support, ops
- Workflows where the operator is in the loop and wants visibility into every cross-agent message
- Durable, grep-able memory the operator can read and edit by hand

It's not great for:
- Anyone who wants a hosted SaaS — this is self-hosted by design
- Linux-first scheduler today — launchd is macOS-only (Linux/systemd swap is small but unwritten)
- Graph-DAG orchestration — not the model
- High-throughput production workloads — this is operator-as-API-shape, not API-shape

## Detailed comparisons

### vs OpenClaw

OpenClaw is the closest cousin to AgentOS in spirit. Both treat agents as long-lived entities with their own state. The differences:

- **Runtime.** OpenClaw has its own runtime and UI. AgentOS lives inside the Claude Code CLI you already have installed.
- **Protocol.** OpenClaw implements ACP (Agent Communication Protocol) for cross-host agent talk. AgentOS uses Discord webhooks — single-host, but free, durable, and human-readable.
- **Surface.** OpenClaw is bigger and more capable out of the box. AgentOS is smaller and easier to fork.

Pick OpenClaw if you need multi-host agents, ACP compatibility, or a polished out-of-box experience. Pick AgentOS if you want something you can read end-to-end in an afternoon.

### vs Mastra

Mastra is a polished TypeScript framework with a hosted runtime offering. The differences:

- **Language.** TS vs Python. Pick whichever your existing code is in.
- **Hosting.** Mastra has a paid hosted runtime. AgentOS is BYO machine.
- **Auth.** Mastra needs LLM API keys. AgentOS runs on your Claude.ai subscription.
- **Config shape.** Mastra is code-first. AgentOS is markdown-first — you write personality, not classes.

### vs LangGraph

LangGraph is the most mature graph-orchestration framework for LLMs. If your problem is *"I have a known workflow with branches, retries, and fan-out, and I want it to be reliable,"* LangGraph is probably the right tool.

AgentOS doesn't have graphs. Agents are independent, addressable by channel, and coordinate by passing messages. If your problem is *"I want a virtual team of specialists I can talk to like coworkers,"* the graph DSL is overhead, not value.

### vs AutoGen

AutoGen comes out of Microsoft Research and emphasizes conversation patterns — group chat, society of mind, hierarchical agent teams. Excellent for research and for use cases where multiple agents need to deliberate before producing an output.

AgentOS optimizes for the operator-in-the-loop case: one human, multiple specialists, durable scrollback, and human-readable memory. Less interesting research; more practical day-to-day.

### vs claude-flow

claude-flow is a workflow runner — it executes pipelines of Claude calls. Workflows are short-lived; AgentOS agents are long-lived. If you want pipelines, use claude-flow. If you want persistent specialists, use AgentOS.

## What AgentOS is NOT good at

Worth saying plainly:

- **Not a hosted SaaS.** You run it on your machine. The whole point is that you own the data, the agents, and the schedule.
- **Not Linux-ready for the scheduler.** macOS launchd is wired in; Linux systemd and Windows Task Scheduler are unwritten. Swapping is small (~one file) but you'll have to write it.
- **Not a graph orchestrator.** No DAG, no fan-out primitives, no retry policy. Agents coordinate by messaging each other in Discord channels.
- **Not high-throughput.** Each agent runs through the Claude Code CLI, which is great for interactive operator workflows and not designed for parallel API-style throughput.
- **Not a substitute for production observability.** Trajectory JSONL is enough for one operator to debug their own agents. It's not Datadog.

If any of those are dealbreakers, one of the alternatives above will fit better.

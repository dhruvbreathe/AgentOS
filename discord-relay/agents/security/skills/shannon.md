<!-- Source: https://github.com/aissablk1/shannon-pentester-skill -->

---
name: shannon
description: >
  Autonomous AI-powered penetration testing for web applications using Shannon by Keygraph.
  Use this skill when the user asks to pentest, security-test, or vulnerability-scan a web application or API.
  Triggers on: "pentest my app", "run a security scan", "find vulnerabilities", "test for XSS/SQLi/SSRF",
  "check my app for OWASP issues", "run Shannon", "penetration test", "security assessment",
  "exploit testing", "white-box security test", "check for injection vulnerabilities",
  "audit my web app security", "scan for auth bypass", "test authentication security".
  Also trigger when the user mentions Shannon, Keygraph, or wants to set up automated security testing
  in CI/CD, even if they don't use the exact word "pentest".
---

# Shannon — Autonomous AI Pentester

> **LEGAL WARNING** — Using Shannon without written authorization from the application owner is
> illegal (CFAA in the US, articles 323-1 to 323-7 of the French Penal Code, Computer Misuse Act
> in the UK, and equivalent laws worldwide). **NEVER target a production environment.** Shannon
> performs mutative actions (creating users, submitting forms, modifying data) that can damage
> production data. Keygraph is not responsible for any misuse.

Shannon Lite (by [Keygraph](https://github.com/KeygraphHQ/shannon)) is an autonomous,
**white-box** AI pentester for web applications and APIs. "White-box" means Shannon needs
access to the target application's source code — it analyzes the code to identify attack vectors,
then executes real exploits against the running application to prove vulnerabilities.

**Only proven vulnerabilities with working proof-of-concept exploits appear in reports.**

**LLM caveat:** While Shannon's "proof-by-exploitation" methodology eliminates most false positives,
the underlying LLMs can still generate hallucinated or weakly-supported content. **Human oversight
of all findings is essential.**

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Workflow](#workflow) (Setup → Config → Launch → Monitor → Analyze → Remediate → Stop)
3. [5-Phase Testing Pipeline](#shannons-5-phase-testing-pipeline)
4. [Platform Notes](#platform-notes)
5. [Common Issues](#common-issues)
6. [Benchmark](#benchmark)
7. [References](#references)

## Prerequisites

1. **Docker** — Docker Desktop (macOS/Windows) or Docker Engine (Linux) must be running
2. **AI credentials** — One of:
   - `ANTHROPIC_API_KEY` (recommended)
   - `CLAUDE_CODE_OAUTH_TOKEN` (alternative)
   - AWS Bedrock or Google Vertex AI routing (advanced — see [configuration guide](references/configuration-guide.md))
3. **Source code access** — Shannon is white-box only; it needs the target app's source code
4. **Running target** — The web app must be accessible (local or remote URL)
5. **Trusted codebase only** — Do not scan untrusted or adversarial codebases. Shannon reads source
   code and is susceptible to prompt injection from malicious content in scanned repositories.
6. **Cost awareness** — Each run costs ~$50 USD in Claude API credits and takes ~1-1.5 hours

Run the prerequisite checker:

```bash
# From the Shannon root directory:
bash scripts/check-prerequisites.sh
```

> The scripts bundled with this skill (`scripts/check-prerequisites.sh` and `scripts/parse-report.sh`)
> should be copied into the Shannon root directory for easy access. Claude Code resolves
> `<skill-path>` to the installed skill location automatically.

## Workflow

### Phase 1: Setup

```bash
# Option 1: npx (simplest — installs and runs Shannon)
npx @keygraph/shannon

# Option 2: git clone (pin to a specific tag for reproducibility)
git clone https://github.com/KeygraphHQ/shannon.git
cd shannon
```

> If using `npx`, Shannon installs to a local directory. All subsequent `./shannon` commands
> work identically from within that directory.

Copy the target source code into `./repos/`:

```bash
# Single repo
cp -r /path/to/your-app ./repos/my-app

# Monorepo (frontend + backend in subdirectories)
cp -r /path/to/monorepo ./repos/my-monorepo

# Multi-repo (consolidate into one parent)
mkdir -p ./repos/my-app
cp -r /path/to/frontend ./repos/my-app/
cp -r /path/to/backend ./repos/my-app/
```

> **Before copying:** verify the source directory does not contain `.env` files, private keys,
> or credentials. Shannon reads the entire codebase. Prefer `cp -r` over `ln -s` to prevent
> Shannon from modifying your original repo.

Set credentials via `.env` file in the Shannon root:

```env
# REPLACE with your real API key — never commit this file
ANTHROPIC_API_KEY=sk-ant-api03-YOUR-KEY-HERE
CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000
```

**Protect your secrets:**
```bash
echo -e '.env\nconfigs/' >> .gitignore
```

### Phase 2: Configuration (optional but recommended)

For authenticated testing, create a YAML config. Full reference: [configuration-guide.md](references/configuration-guide.md)

```yaml
# configs/my-app.yaml — EXAMPLE VALUES ONLY, replace with real test credentials
authentication:
  login_type: form                          # form | sso | api | basic
  login_url: "https://my-app.com/login"
  credentials:
    username: "<YOUR-TEST-EMAIL>"
    password: "<YOUR-TEST-PASSWORD>"
  login_flow:
    - "Type $username into the email input"
    - "Type $password into the password input"
    - "Click the 'Sign In' button"
  success_condition:
    type: url_contains
    value: "/dashboard"

rules:
  avoid:
    - description: "Preserve session"
      type: path
      url_path: "/logout"
  focus:
    - description: "Prioritize API"
      type: path
      url_path: "/api/*"

pipeline:
  retry_preset: subscription
  max_concurrent_pipelines: "2"             # String "1"-"5"
```

> **Prepare a dedicated test account** before running Shannon. Never use production credentials
> or a real user's account.

### Phase 3: Launch

```bash
# Basic
./shannon start URL=https://target-app.com REPO=my-app

# With config
./shannon start URL=https://target-app.com REPO=my-app CONFIG=./configs/my-app.yaml

# Named workspace (auto-resumes if interrupted)
./shannon start URL=https://target-app.com REPO=my-app WORKSPACE=audit-march

# Custom output
./shannon start URL=https://target-app.com REPO=my-app OUTPUT=./my-reports

# Local app (Docker can't reach localhost — use host.docker.internal)
./shannon start URL=http://host.docker.internal:3000 REPO=my-app
```

The CLI returns a workflow ID (`shannon-{timestamp}`) and runs asynchronously.

### Phase 4: Monitor

```bash
./shannon logs                              # Real-time worker logs
./shannon query ID=shannon-1234567890       # Check workflow status
./shannon workspaces                        # List all workspaces
# Temporal Web UI: http://localhost:8233    # Detailed workflow graphs
```

**How to know when it's done:** The `./shannon query` command shows the current phase and status.
When the workflow reaches the "Reporting" phase and completes, the status changes to "COMPLETED".
You can also monitor the Temporal UI at `http://localhost:8233` for a visual timeline.

### Phase 5: Analyze results

Results are in `./audit-logs/{hostname}_{sessionId}/`:

```
audit-logs/target-app-com_1234567890/
  session.json       # Execution metrics, cost, timing
  agents/            # Per-agent execution logs
  prompts/           # Prompt snapshots per phase
  deliverables/      # Reports and evidence (the important part)
    comprehensive_security_assessment_report.md
```

```bash
# List available sessions first
ls -d audit-logs/*/

# Read the final report for a specific session
cat audit-logs/<your-session>/deliverables/comprehensive_security_assessment_report.md

# Or use the report parser for a structured summary
bash scripts/parse-report.sh audit-logs/<your-session>/deliverables/comprehensive_security_assessment_report.md
bash scripts/parse-report.sh audit-logs/<your-session>/deliverables/comprehensive_security_assessment_report.md --json
```

### Phase 6: Remediate

For each finding in the report:

1. Read the PoC steps to understand the exact attack vector
2. Locate the vulnerable code (Shannon maps findings to source locations)
3. Apply the recommended fix
4. Re-run Shannon with the same workspace to verify:
   ```bash
   ./shannon start URL=https://target-app.com REPO=my-app WORKSPACE=audit-march
   ```

### Phase 7: Stop and cleanup

```bash
./shannon stop                  # Graceful stop
./shannon stop CLEAN=true       # Full cleanup (containers, networks, volumes)
```

## Shannon's 5-Phase Testing Pipeline

| Phase | Name | Agents | What it does |
|-------|------|--------|-------------|
| 1 | Pre-Recon | 1 (sequential) | External scans (Nmap, Subfinder, WhatWeb, Schemathesis) + source code analysis |
| 2 | Recon | 1 (sequential) | Browser-automated attack surface mapping via Playwright |
| 3 | Vuln Analysis | 5 (parallel) | injection, XSS, auth, authz, SSRF — each produces a queue if findings exist |
| 4 | Exploitation | 5 (parallel, conditional) | Only runs for categories with findings; proves or discards |
| 5 | Reporting | 1 (sequential) | Executive report with verified findings and PoCs |

**Performance:** ~1-1.5 hours, ~$50 USD (Claude Sonnet). Phases 3-4 run in parallel (~5x faster).

## Platform Notes

| Platform | Notes |
|----------|-------|
| **macOS** | Works out-of-the-box with Docker Desktop |
| **Linux** | May need `sudo usermod -aG docker $USER` (re-login required) |
| **Windows** | Requires WSL2 + Docker Desktop. Add Shannon dir to Windows Defender exclusions (false positives on exploit code). See [troubleshooting](references/troubleshooting.md) for detailed WSL2 setup. |
| **Local apps** | Use `http://host.docker.internal:<port>` instead of `localhost` |

## Common issues

| Problem | Solution |
|---------|----------|
| Docker not running | Start Docker Desktop or `sudo systemctl start docker` |
| Rate limiting | Set `retry_preset: subscription` + `max_concurrent_pipelines: "2"` |
| Token limit errors | Ensure `CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000` in `.env` |
| First run slow | Normal — Docker builds images (~3-5 min first time) |
| Workflow stuck | Check `./shannon logs`; inspect Temporal UI at `localhost:8233` |
| App on localhost | Use `host.docker.internal` instead of `localhost` |

For more diagnostics, see [troubleshooting.md](references/troubleshooting.md).

## Benchmark

Shannon achieved a **96.15% success rate** on the XBOW benchmark (hint-free, source-aware).

| Target | Critical vulns | Total exploits | Sample report |
|--------|---------------|----------------|---------------|
| OWASP Juice Shop | 6 | 25+ | `sample-reports/shannon-report-juice-shop.md` |
| OWASP crAPI | 19 | 23+ | `sample-reports/shannon-report-crapi.md` |
| `c{api}tal` API | 8 | 15 | `sample-reports/shannon-report-capital-api.md` |

## References

- [Use cases, live examples, and real results](references/use-cases.md)
- [Configuration guide](references/configuration-guide.md) (YAML, auth, rules, env vars)
- [Troubleshooting guide](references/troubleshooting.md)
- Official repo: https://github.com/KeygraphHQ/shannon
- Shannon Pro (CI/CD, SAST, enterprise): https://github.com/KeygraphHQ/shannon/blob/main/SHANNON-PRO.md

---

**Plugin by [Aïssa BELKOUSSA](https://aissabelkoussa.fr)** | Shannon Lite by [Keygraph, Inc.](https://github.com/KeygraphHQ) (AGPL-3.0) | Plugin license: MIT

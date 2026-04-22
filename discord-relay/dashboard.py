"""dashboard.py — comprehensive status board for Prana Agent OS.

Routes:
    /                  system overview + agent grid
    /agents/<name>     per-agent detail (trajectory, tasks, schedule, health)
    /activity          cross-agent event feed (last ~100 events)
    /schedule          all crons with next-fire times
    /health            doctor.py output as a page
    /vault             vault stats + recent sessions

JSON API:
    /api/status        scripts/status.py --json
    /api/doctor        scripts/doctor.py --json
    /api/agents/<name>/trajectory?limit=50
    /api/agents/<name>/tasks
    /api/schedule
    /api/activity?limit=100
    /api/vault

No auth — binds localhost by default. Expose via ngrok + basic-auth.

Usage:
    ./.venv/bin/python dashboard.py
    ./.venv/bin/python dashboard.py --port 9090
    ./.venv/bin/python dashboard.py --host 0.0.0.0
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
except ImportError:
    print("error: fastapi + uvicorn required. Install: ./.venv/bin/pip install fastapi uvicorn",
          file=sys.stderr)
    sys.exit(2)

try:
    from croniter import croniter  # optional — next-fire times
    HAVE_CRONITER = True
except ImportError:
    HAVE_CRONITER = False

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
AGENTS_DIR = ROOT / "agents"
LOGS_DIR = ROOT / "logs"
TRAJ_DIR = LOGS_DIR / "trajectories"
VAULT_PATH = Path(os.environ.get("VAULT_PATH", "/Users/celainc/Documents/Vayu/Vayu"))

app = FastAPI(title="Prana AgentOS")


# ---- optional basic auth middleware ----------------------------------------
# When DASHBOARD_AUTH=1 and logs/dashboard-creds.txt exists (user:pass),
# all requests except /sw.js and /manifest.json require HTTP Basic auth.
# Meant for when the dashboard is exposed beyond the localhost-only loopback.

def _load_creds() -> tuple[str, str] | None:
    p = ROOT / "logs" / "dashboard-creds.txt"
    if not p.exists():
        return None
    txt = p.read_text().strip()
    if ":" not in txt:
        return None
    u, _, pw = txt.partition(":")
    return u, pw


if os.environ.get("DASHBOARD_AUTH") == "1":
    import base64
    from fastapi import Request
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    creds = _load_creds()

    class BasicAuth(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path in ("/sw.js", "/manifest.json"):
                return await call_next(request)
            if not creds:
                return Response("Auth not configured", status_code=503)
            header = request.headers.get("authorization", "")
            if not header.startswith("Basic "):
                return Response("Unauthorized", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="AgentOS"'})
            try:
                decoded = base64.b64decode(header[6:]).decode()
                u, _, pw = decoded.partition(":")
            except Exception:
                return Response("Bad auth", status_code=401)
            if (u, pw) != creds:
                return Response("Forbidden", status_code=403)
            return await call_next(request)

    app.add_middleware(BasicAuth)


# Mount human-to-agent chat router (POST /api/chat/<agent>/stream etc.)
try:
    from web_chat import router as chat_router, chat_page_html, chat_index_html, _agent_map, _history_path
    app.include_router(chat_router)
    _CHAT_READY = True
except Exception as _e:
    print(f"warn: chat router not mounted: {_e}", file=sys.stderr)
    _CHAT_READY = False

# Mount agent config editor router (GET /agents/<name>/edit + save/clone/tasks).
try:
    from dashboard_edit import router as edit_router
    app.include_router(edit_router)
    _EDIT_READY = True
except Exception as _e:
    print(f"warn: edit router not mounted: {_e}", file=sys.stderr)
    _EDIT_READY = False

# Mount tasks/goals/budgets router (Paperclip-style project management).
try:
    from tasks_routes import router as tasks_router
    app.include_router(tasks_router)
    _TASKS_READY = True
except Exception as _e:
    print(f"warn: tasks router not mounted: {_e}", file=sys.stderr)
    _TASKS_READY = False


# Cross-process event fan-in: tail logs/events.jsonl so events published by
# other processes (bot.py's send_to_agent, scheduled tasks, etc.) reach the
# dashboard's in-memory bus and show up in SSE subscribers' browsers.
@app.on_event("startup")
async def _start_event_tailer() -> None:
    try:
        import asyncio as _asyncio
        from events import tail_events_forever
        _asyncio.create_task(tail_events_forever())
        print("event tailer started (logs/events.jsonl → in-memory bus)", file=sys.stderr)
    except Exception as _e:
        print(f"warn: event tailer not started: {_e}", file=sys.stderr)


def esc(s) -> str:
    return html_lib.escape(str(s or ""))


# ---- subprocess helpers ----------------------------------------------------

def _parse_json_lenient(out: str) -> list[dict]:
    """Parse JSON that might have trailing non-JSON noise."""
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # Try to find the end of the first JSON value
        try:
            decoded, _ = json.JSONDecoder().raw_decode(out.lstrip())
            return decoded
        except Exception as e:
            return [{"error": f"parse: {e}"}]


def _run_status_json() -> list[dict]:
    try:
        out = subprocess.check_output(
            [sys.executable, str(SCRIPTS / "status.py"), "--json"],
            cwd=ROOT, text=True, timeout=10,
        )
        return _parse_json_lenient(out)
    except Exception as e:
        return [{"error": str(e)}]


def _run_doctor_json() -> list[dict]:
    try:
        out = subprocess.check_output(
            [sys.executable, str(SCRIPTS / "doctor.py"), "--json"],
            cwd=ROOT, text=True, timeout=45,
            stderr=subprocess.DEVNULL,
        )
        return _parse_json_lenient(out)
    except Exception as e:
        return [{"error": str(e)}]


# ---- data gathering --------------------------------------------------------

def _list_agents() -> list[str]:
    if not AGENTS_DIR.exists():
        return []
    return sorted(
        d.name for d in AGENTS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_"))
        and (d / "agent.yaml").exists()
    )


def _latest_trajectory(agent: str) -> Path | None:
    d = TRAJ_DIR / agent
    if not d.exists():
        return None
    return max(d.glob("*.jsonl"), default=None, key=lambda p: p.stat().st_mtime)


def _read_trajectory_events(path: Path, limit: int = 50) -> list[dict]:
    events: list[dict] = []
    with path.open() as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events[-limit:] if limit else events


def _read_active_tasks(agent: str) -> list[dict]:
    d = AGENTS_DIR / agent / "ActiveTasks"
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.md")):
        if f.name == "README.md":
            continue
        text = f.read_text()
        fm = {}
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                for line in text[3:end].splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        fm[k.strip()] = v.strip()
        title = f.stem
        m = re.search(r"^# (.+)$", text, re.MULTILINE)
        if m:
            title = m.group(1).strip()
        out.append({
            "file": f.name,
            "title": title,
            "status": fm.get("status", "open"),
            "urgency": fm.get("urgency", "—"),
            "owner": fm.get("owner", agent),
            "updated": fm.get("updated", fm.get("created", "—")),
        })
    return out


def _read_scheduled_tasks(agent: str) -> list[dict]:
    d = AGENTS_DIR / agent / "tasks"
    if not d.exists():
        return []
    out: list[dict] = []
    now = datetime.now()
    for f in sorted(d.glob("*.md")):
        text = f.read_text()
        cron_m = re.search(r"^cron:\s*(.+)$", text, re.MULTILINE)
        kind_m = re.search(r"^kind:\s*(\S+)", text, re.MULTILINE)
        cron_expr = cron_m.group(1).strip() if cron_m else None
        next_fire = None
        if cron_expr and HAVE_CRONITER:
            try:
                next_fire = croniter(cron_expr, now).get_next(datetime).isoformat(timespec="minutes")
            except Exception:
                pass
        out.append({
            "agent": agent,
            "name": f.stem,
            "cron": cron_expr,
            "kind": kind_m.group(1).strip() if kind_m else "post",
            "next_fire": next_fire,
        })
    return out


def _all_scheduled() -> list[dict]:
    rows: list[dict] = []
    for a in _list_agents():
        rows.extend(_read_scheduled_tasks(a))
    rows.sort(key=lambda r: r["next_fire"] or "9999")
    return rows


def _activity_feed(limit: int = 100) -> list[dict]:
    all_events: list[dict] = []
    for a in _list_agents():
        path = _latest_trajectory(a)
        if not path:
            continue
        recent = _read_trajectory_events(path, limit=20)
        for ev in recent:
            ev = dict(ev)
            ev["_agent"] = a
            ev["_session"] = path.stem
            all_events.append(ev)
    all_events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return all_events[:limit]


SHARED_SKILLS = ROOT / "shared" / "skills"


def _resolve_skill(ref: str, agent: str) -> dict:
    """Resolve a skill ref (skill:X, local:X, or a path) to {name, path, desc}."""
    ref = str(ref).strip()
    path: Path | None = None
    kind = "shared"
    if ref.startswith("skill:"):
        name = ref[len("skill:"):]
        cand = SHARED_SKILLS / name / "SKILL.md"
        if cand.exists():
            path = cand
        else:
            cand2 = ROOT / "shared" / "skills" / f"{name}.md"  # legacy flat file
            if cand2.exists():
                path = cand2
    elif ref.startswith("local:"):
        kind = "local"
        name = ref[len("local:"):]
        cand = AGENTS_DIR / agent / "skills" / name / "SKILL.md"
        if cand.exists():
            path = cand
    else:
        # relative or absolute path form
        kind = "path"
        name = Path(ref).stem
        cand = (AGENTS_DIR / agent / ref).resolve()
        if cand.exists():
            path = cand
        else:
            cand2 = (ROOT / ref).resolve()
            if cand2.exists():
                path = cand2

    desc = ""
    if path and path.exists():
        try:
            text = path.read_text()
            # YAML frontmatter description
            m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
            if m:
                desc = m.group(1).strip().strip("'\"")
            else:
                # first non-frontmatter paragraph
                body = re.sub(r"^---.*?---\s*", "", text, count=1, flags=re.DOTALL)
                body = re.sub(r"^#\s+.+\n+", "", body, count=1)
                desc = body.strip().split("\n\n", 1)[0][:180].replace("\n", " ")
        except Exception:
            pass

    return {"ref": ref, "name": name, "kind": kind,
            "path": str(path) if path else None,
            "found": bool(path), "desc": desc}


def _tool_usage_stats(events: list[dict]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for ev in events:
        if ev.get("type") == "tool_use":
            n = ev.get("name") or "?"
            counts[n] = counts.get(n, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


def _full_trajectory_events(agent: str) -> list[dict]:
    path = _latest_trajectory(agent)
    if not path:
        return []
    try:
        return _read_trajectory_events(path, limit=0)
    except Exception:
        return []


def _vault_stats() -> dict:
    if not VAULT_PATH.exists():
        return {"error": f"vault not mounted at {VAULT_PATH}"}
    sessions = list((VAULT_PATH / "Sessions").glob("*.md")) if (VAULT_PATH / "Sessions").exists() else []
    topics = list((VAULT_PATH / "Topics").glob("*.md")) if (VAULT_PATH / "Topics").exists() else []
    latest_session = max(sessions, default=None, key=lambda p: p.stat().st_mtime) if sessions else None
    decisions_path = VAULT_PATH / "Company" / "DECISIONS.md"
    return {
        "vault_path": str(VAULT_PATH),
        "sessions_count": len(sessions),
        "topics_count": len(topics),
        "latest_session": {
            "name": latest_session.name,
            "mtime": datetime.fromtimestamp(latest_session.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        } if latest_session else None,
        "decisions_mtime": (
            datetime.fromtimestamp(decisions_path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
            if decisions_path.exists() else None
        ),
        "recent_sessions": [
            {
                "name": p.name,
                "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            }
            for p in sorted(sessions, key=lambda p: p.stat().st_mtime, reverse=True)[:15]
        ],
    }


# ---- HTML styling ----------------------------------------------------------

CSS = """
  :root {
    /* Prana — warm, premium, breath-aligned */
    --bg:#f7f5ef;               /* cream */
    --panel:#ffffff;            /* primary surface */
    --panel-2:#efece3;          /* subtle alt surface */
    --panel-3:#f2efe6;          /* lighter alt */
    --fg:#1a1f2e;               /* deep navy */
    --fg-dim:#4b5563;           /* secondary */
    --fg-mute:#8a8a7b;          /* tertiary */
    --accent:#1a1f2e;           /* navy — primary action */
    --accent-2:#7cc9a8;         /* mint — breath */
    --accent-3:#a89ecd;         /* lavender */
    --accent-ink:#ffffff;       /* ink on accent */
    --ok:#3d8f58;
    --warn:#b5791f;
    --fail:#b94a33;
    --ok-bg:rgba(125,199,168,0.18);
    --warn-bg:rgba(212,148,62,0.15);
    --fail-bg:rgba(199,84,62,0.12);
    --border:#e5e1d6;
    --border-strong:#d6d1c2;
    --shadow-sm:0 1px 2px rgba(26,31,46,0.04), 0 0 0 1px rgba(26,31,46,0.04);
    --shadow-md:0 4px 16px rgba(26,31,46,0.06), 0 0 0 1px rgba(26,31,46,0.04);
    --radius:14px;
    --radius-sm:10px;
    --radius-pill:999px;
    --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,monospace;
    --serif:"Charter","Iowan Old Style","Georgia",serif;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#15161c; --panel:#1c1d25; --panel-2:#252731; --panel-3:#1f2029;
      --fg:#f1eee4; --fg-dim:#c4bfae; --fg-mute:#827d6e;
      --accent:#f7f5ef; --accent-ink:#1a1f2e;
      --border:#2e2f3a; --border-strong:#3b3d4a;
      --shadow-sm:0 1px 2px rgba(0,0,0,0.3), 0 0 0 1px rgba(255,255,255,0.04);
      --shadow-md:0 6px 20px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.04);
      --ok-bg:rgba(125,199,168,0.12);
      --warn-bg:rgba(212,148,62,0.12);
      --fail-bg:rgba(199,84,62,0.12);
    }
  }
  *{box-sizing:border-box;}
  html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Inter","SF Pro Text","Helvetica Neue",sans-serif;
    font-size:15px;line-height:1.5;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
    padding-bottom:env(safe-area-inset-bottom);
  }
  @supports (-webkit-touch-callout: none) { body { font-size:16px; } }

  /* ---------- layout shell ---------- */
  .topbar{
    position:sticky;top:0;z-index:40;
    display:flex;align-items:center;gap:1em;
    padding:0.9em 1.5em;
    background:color-mix(in srgb, var(--bg) 86%, transparent);
    backdrop-filter:saturate(140%) blur(14px);
    -webkit-backdrop-filter:saturate(140%) blur(14px);
    border-bottom:1px solid var(--border);
  }
  .brand{display:flex;align-items:center;gap:0.55em;font-weight:600;letter-spacing:-0.01em;color:var(--fg);text-decoration:none;font-size:1.02em;}
  .brand-mark{width:28px;height:28px;border-radius:9px;background:linear-gradient(135deg,var(--accent) 0%,#2d3448 100%);display:grid;place-items:center;color:var(--accent-ink);font-weight:700;font-size:0.9em;box-shadow:var(--shadow-sm);}
  .brand small{font-weight:400;color:var(--fg-mute);font-size:0.82em;margin-left:0.2em;}
  .nav-desktop{display:flex;gap:0.15em;margin-left:1.2em;}
  .nav-desktop a{
    color:var(--fg-dim);font-size:0.92em;text-decoration:none;
    padding:0.5em 0.9em;border-radius:var(--radius-pill);transition:all 0.14s;
  }
  .nav-desktop a:hover{color:var(--fg);background:var(--panel-2);}
  .nav-desktop a.active{color:var(--fg);background:var(--panel);box-shadow:var(--shadow-sm);}
  .topbar-spacer{flex:1;}
  .topbar-meta{color:var(--fg-mute);font-size:0.82em;display:flex;align-items:center;gap:0.5em;}
  .pulse-dot{width:7px;height:7px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 0 rgba(61,143,88,0.6);animation:pulse 2.2s ease-out infinite;}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(61,143,88,0.5);}70%{box-shadow:0 0 0 8px rgba(61,143,88,0);}100%{box-shadow:0 0 0 0 rgba(61,143,88,0);}}

  @media (max-width:768px){
    .nav-desktop{display:none;}
    .topbar{padding:0.7em 1em;}
  }

  .wrap{max-width:1200px;margin:0 auto;padding:1.5em;}
  @media (max-width:768px){.wrap{padding:1em;padding-bottom:5.5em;}}

  /* ---------- bottom nav (mobile) ---------- */
  .nav-bottom{
    display:none;
    position:fixed;bottom:0;left:0;right:0;z-index:50;
    background:color-mix(in srgb, var(--bg) 92%, transparent);
    backdrop-filter:saturate(140%) blur(16px);
    -webkit-backdrop-filter:saturate(140%) blur(16px);
    border-top:1px solid var(--border);
    padding:0.45em 0.25em calc(0.45em + env(safe-area-inset-bottom));
  }
  .nav-bottom ul{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(5,1fr);}
  .nav-bottom a{
    display:flex;flex-direction:column;align-items:center;gap:0.15em;
    padding:0.45em 0.25em;color:var(--fg-mute);text-decoration:none;
    font-size:0.7em;font-weight:500;border-radius:10px;min-height:44px;
  }
  .nav-bottom a svg{width:22px;height:22px;stroke:currentColor;fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;}
  .nav-bottom a.active{color:var(--fg);}
  .nav-bottom a.active svg{stroke:var(--fg);}
  @media (max-width:768px){.nav-bottom{display:block;}}

  /* ---------- typography ---------- */
  h1,h2,h3,h4{margin:0;font-weight:600;letter-spacing:-0.015em;color:var(--fg);}
  h1{font-family:var(--serif);font-size:2em;line-height:1.15;font-weight:500;letter-spacing:-0.02em;}
  h2{font-size:0.78em;margin:2.2em 0 0.9em;color:var(--fg-mute);text-transform:uppercase;letter-spacing:0.11em;font-weight:600;}
  h3{font-size:1em;margin:1em 0 0.5em;}
  @media (max-width:768px){h1{font-size:1.65em;}}
  a{color:var(--fg);text-decoration:none;border-bottom:1px solid transparent;transition:border-color 0.15s;}
  a:hover{border-bottom-color:var(--fg-mute);}
  .meta{color:var(--fg-mute);font-size:0.85em;}
  .eyebrow{color:var(--fg-mute);text-transform:uppercase;font-size:0.68em;letter-spacing:0.14em;font-weight:600;}

  /* ---------- hero ---------- */
  .hero{display:flex;align-items:flex-end;justify-content:space-between;gap:1em;margin:0.4em 0 1.5em;flex-wrap:wrap;}
  .hero-headline{flex:1;min-width:240px;}
  .hero-subtitle{color:var(--fg-dim);margin-top:0.4em;font-size:1.02em;max-width:54ch;}
  .hero-actions{display:flex;gap:0.5em;flex-wrap:wrap;}

  /* ---------- buttons ---------- */
  .btn{
    display:inline-flex;align-items:center;gap:0.4em;
    padding:0.65em 1.1em;border-radius:var(--radius-pill);
    font-size:0.92em;font-weight:500;cursor:pointer;
    background:var(--panel);color:var(--fg);border:1px solid var(--border);
    text-decoration:none;transition:all 0.14s;min-height:40px;
    box-shadow:var(--shadow-sm);
  }
  .btn:hover{border-color:var(--border-strong);transform:translateY(-1px);box-shadow:var(--shadow-md);}
  .btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);}
  .btn.primary:hover{opacity:0.92;}
  .btn.ghost{box-shadow:none;background:transparent;}
  .btn.sm{padding:0.4em 0.85em;font-size:0.85em;min-height:32px;}

  /* ---------- grid + stat cards ---------- */
  .grid{display:grid;gap:1em;}
  .cols-4{grid-template-columns:repeat(4,1fr);}
  .cols-3{grid-template-columns:repeat(3,1fr);}
  .cols-2{grid-template-columns:repeat(2,1fr);}
  @media (max-width:1024px){.cols-4{grid-template-columns:repeat(2,1fr);}}
  @media (max-width:640px){.cols-4,.cols-3,.cols-2{grid-template-columns:1fr;}}

  .stat{
    background:var(--panel);border:1px solid var(--border);
    padding:1.1em 1.2em;border-radius:var(--radius);
    box-shadow:var(--shadow-sm);
  }
  .stat .label{color:var(--fg-mute);text-transform:uppercase;font-size:0.68em;letter-spacing:0.12em;font-weight:600;}
  .stat .value{font-family:var(--serif);font-size:2.1em;font-weight:500;margin-top:0.25em;line-height:1.1;letter-spacing:-0.02em;}
  .stat .value .pill{font-family:-apple-system,sans-serif;font-size:0.48em;font-weight:500;vertical-align:middle;}
  .stat .sub{color:var(--fg-dim);font-size:0.85em;margin-top:0.45em;}
  .stat.accent{background:linear-gradient(135deg, var(--panel) 0%, var(--panel-2) 100%);}

  /* ---------- agent cards (mobile-first) ---------- */
  .agent-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:0.9em;}
  .agent-card{
    display:block;background:var(--panel);border:1px solid var(--border);
    border-radius:var(--radius);padding:1em 1.1em;
    text-decoration:none;color:inherit;transition:all 0.14s;
    box-shadow:var(--shadow-sm);
  }
  .agent-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-md);border-color:var(--border-strong);}
  .agent-card .head{display:flex;align-items:center;justify-content:space-between;gap:0.5em;margin-bottom:0.7em;}
  .agent-card .name{font-weight:600;font-size:1.02em;}
  .agent-card .age{color:var(--fg-mute);font-size:0.8em;}
  .agent-card .row{display:flex;gap:0.55em;align-items:center;flex-wrap:wrap;font-size:0.82em;color:var(--fg-dim);margin-top:0.55em;}
  .agent-card .row .chip{background:var(--panel-2);border-radius:var(--radius-pill);padding:0.15em 0.65em;color:var(--fg-dim);font-size:0.8em;}
  .agent-card .model{color:var(--fg-mute);font-family:var(--mono);font-size:0.75em;margin-top:0.3em;}
  .health-dot{display:inline-block;width:8px;height:8px;border-radius:50%;}
  .health-dot.ok{background:var(--ok);}
  .health-dot.warn{background:var(--warn);}
  .health-dot.fail{background:var(--fail);}
  .health-dot.dim{background:var(--fg-mute);}

  /* ---------- table ---------- */
  table{border-collapse:collapse;width:100%;font-size:0.9em;}
  table th{text-align:left;padding:0.75em 1em;border-bottom:1px solid var(--border);color:var(--fg-mute);font-weight:600;font-size:0.7em;text-transform:uppercase;letter-spacing:0.1em;}
  table td{padding:0.75em 1em;border-bottom:1px solid var(--border);}
  table tr:last-child td{border-bottom:none;}
  table tr:hover td{background:var(--panel-3);}
  @media (max-width:640px){table.responsive thead{display:none;}table.responsive tr{display:block;border-bottom:1px solid var(--border);padding:0.6em 0;}table.responsive td{display:flex;justify-content:space-between;padding:0.3em 1em;border:none;}table.responsive td:before{content:attr(data-label);color:var(--fg-mute);font-size:0.75em;text-transform:uppercase;letter-spacing:0.08em;}}

  /* ---------- pills ---------- */
  .pill{display:inline-flex;align-items:center;gap:0.3em;padding:0.18em 0.65em;border-radius:var(--radius-pill);font-size:0.76em;font-weight:500;letter-spacing:0.01em;}
  .pill.ok{background:var(--ok-bg);color:var(--ok);}
  .pill.warn{background:var(--warn-bg);color:var(--warn);}
  .pill.fail{background:var(--fail-bg);color:var(--fail);}
  .pill.dim{background:var(--panel-2);color:var(--fg-dim);}
  .pill.accent{background:var(--accent);color:var(--accent-ink);}

  /* ---------- code + panels ---------- */
  code,pre{font-family:var(--mono);font-size:0.85em;}
  code{background:var(--panel-2);padding:0.1em 0.45em;border-radius:5px;color:var(--fg);word-break:break-word;}
  pre{background:var(--panel);border:1px solid var(--border);padding:1em 1.1em;border-radius:var(--radius-sm);overflow-x:auto;white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;box-shadow:var(--shadow-sm);max-width:100%;box-sizing:border-box;}
  html,body{overflow-x:hidden;max-width:100vw;}
  .wrap,main,section,.panel,details{max-width:100%;box-sizing:border-box;}
  textarea,input{max-width:100%;box-sizing:border-box;word-break:break-word;}
  table{max-width:100%;}
  table td,table th{word-break:break-word;overflow-wrap:anywhere;}
  .panel{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:1.2em 1.3em;box-shadow:var(--shadow-sm);}
  .panel+.panel{margin-top:1em;}
  .panel.flush{padding:0;}
  .panel.flush table th,.panel.flush table td{padding:0.8em 1.3em;}

  /* ---------- events feed ---------- */
  .event{padding:0.65em 0;border-bottom:1px solid var(--border);font-size:0.88em;display:flex;align-items:baseline;gap:0.6em;flex-wrap:wrap;}
  .event:last-child{border-bottom:none;}
  .event .ts{color:var(--fg-mute);font-family:var(--mono);font-size:0.78em;}
  .event .agent{color:var(--fg);font-weight:600;}
  .event .agent a{border:none;}
  .event .type{color:var(--fg-dim);}
  .truncate{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;flex:1;min-width:0;}

  /* ---------- misc ---------- */
  hr{border:none;border-top:1px solid var(--border);margin:1.5em 0;}
  .section-head{display:flex;align-items:baseline;justify-content:space-between;gap:1em;margin:2em 0 0.8em;}
  .section-head h2{margin:0;}
  .section-head .link{color:var(--fg-dim);font-size:0.86em;}
  .empty{color:var(--fg-mute);font-size:0.92em;padding:0.6em 0;font-style:italic;}

  /* noindex hint */
  .dev-hint{position:fixed;bottom:1em;right:1em;font-size:0.72em;color:var(--fg-mute);background:var(--panel);padding:0.35em 0.8em;border-radius:var(--radius-pill);border:1px solid var(--border);box-shadow:var(--shadow-sm);z-index:30;}
  @media (max-width:768px){.dev-hint{display:none;}}
"""

NAV_ITEMS = [("/", "Overview"), ("/chat", "Chat"), ("/tasks", "Tasks"),
             ("/goals", "Goals"), ("/budgets", "Budgets"),
             ("/activity", "Activity"), ("/schedule", "Schedule"),
             ("/health", "Health"), ("/vault", "Vault"), ("/search", "Search")]

# Bottom-nav (mobile) — 5 primary destinations with inline SVG icons.
_BOTTOM_NAV = [
    ("/", "Home",
     '<svg viewBox="0 0 24 24"><path d="M3 12 12 3l9 9"/><path d="M5 10v10h14V10"/></svg>'),
    ("/chat", "Chat",
     '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4V5z"/></svg>'),
    ("/activity", "Activity",
     '<svg viewBox="0 0 24 24"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg>'),
    ("/schedule", "Schedule",
     '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 10h18M8 2v4M16 2v4"/></svg>'),
    ("/vault", "Vault",
     '<svg viewBox="0 0 24 24"><path d="M4 4h16v16H4z"/><path d="M4 8h16M10 8v12"/></svg>'),
]


def _topbar(active_path: str) -> str:
    desktop_links = "".join(
        f'<a href="{href}" class="{"active" if href == active_path else ""}">{name}</a>'
        for href, name in NAV_ITEMS
    )
    return f"""<header class="topbar">
  <a href="/" class="brand"><span class="brand-mark">P</span>Prana <small>AgentOS</small></a>
  <nav class="nav-desktop">{desktop_links}</nav>
  <span class="topbar-spacer"></span>
  <span class="topbar-meta"><span class="pulse-dot"></span> live</span>
</header>"""


def _bottom_nav(active_path: str) -> str:
    items = "".join(
        f'<li><a href="{href}" class="{"active" if href == active_path else ""}">{svg}<span>{label}</span></a></li>'
        for href, label, svg in _BOTTOM_NAV
    )
    return f'<nav class="nav-bottom"><ul>{items}</ul></nav>'


def _page(title: str, nav_active: str, body: str, refresh: int = 0) -> str:
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f7f5ef" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#15161c" media="(prefers-color-scheme: dark)">
<meta name="color-scheme" content="light dark">
<title>{esc(title)} — Prana AgentOS</title>
<link rel="manifest" href="/manifest.json">
{refresh_tag}<style>{CSS}</style></head>
<body>
{_topbar(nav_active)}
<main class="wrap">{body}</main>
{_bottom_nav(nav_active)}
</body></html>"""


def _age(mtime_iso: str | None) -> tuple[str, str]:
    if not mtime_iso:
        return "—", "dim"
    try:
        ts = datetime.fromisoformat(mtime_iso.replace("Z", "+00:00"))
    except Exception:
        return mtime_iso, "dim"
    minutes = int((datetime.now(timezone.utc) - ts).total_seconds() // 60)
    if minutes < 1:
        s = "now"
    elif minutes < 60:
        s = f"{minutes}m ago"
    elif minutes < 1440:
        s = f"{minutes // 60}h ago"
    else:
        s = f"{minutes // 1440}d ago"
    sev = "ok" if minutes < 60 else ("warn" if minutes < 360 else "dim")
    return s, sev


def _summarize_event(ev: dict) -> str:
    t = ev.get("type", "?")
    if t == "prompt":
        c = (ev.get("content") or "").strip()[:160]
        return f"👤 {esc(c)}"
    if t == "thinking":
        c = (ev.get("content") or "").strip()[:160]
        return f"🧠 {esc(c)}"
    if t == "text":
        c = (ev.get("content") or "").strip()[:160]
        return f"💬 {esc(c)}"
    if t == "tool_use":
        name = ev.get("name", "?")
        inp = json.dumps(ev.get("input") or {})[:120]
        return f"🔧 {esc(name)}  <code>{esc(inp)}</code>"
    if t == "tool_result":
        err = "❌ " if ev.get("is_error") else "✅ "
        content = str(ev.get("content") or "")[:120]
        return f"{err}{esc(content)}"
    if t == "result":
        return f"🏁 stop={esc(ev.get('stop_reason'))} turns={esc(ev.get('num_turns','?'))}"
    return esc(t)


# ---- routes: pages ---------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    reports = _run_status_json()
    doctor = _run_doctor_json()
    doctor_by_agent = {d["agent"]: d for d in doctor if "agent" in d}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    total_agents = len([r for r in reports if "agent" in r])
    ok = sum(1 for d in doctor if d.get("worst") == "ok" and d.get("agent") != "(global)")
    warn = sum(1 for d in doctor if d.get("worst") == "warn" and d.get("agent") != "(global)")
    fail = sum(1 for d in doctor if d.get("worst") == "fail" and d.get("agent") != "(global)")
    total_open = sum(r.get("open_tasks", 0) for r in reports if "agent" in r)
    total_sched = sum(len(r.get("scheduled_tasks", [])) for r in reports if "agent" in r)
    active_1h = sum(
        1 for r in reports
        if r.get("last_trajectory") and r["last_trajectory"].get("age_minutes", 9999) < 60
    )

    overall_class = "ok" if fail == 0 and warn == 0 else ("warn" if fail == 0 else "fail")
    overall_label = "all systems normal" if overall_class == "ok" else (
        f"{warn} warning" + ("s" if warn != 1 else "") if fail == 0
        else f"{fail} failure" + ("s" if fail != 1 else "")
    )

    # Friendly date + "today" headline
    today_str = datetime.now().strftime("%A, %B %-d")

    hero_html = f"""
    <section class="hero">
      <div class="hero-headline">
        <div class="eyebrow">Today · {esc(today_str)}</div>
        <h1>Your team is <span style="color:var(--{'ok' if overall_class=='ok' else overall_class})">{overall_label}</span>.</h1>
        <p class="hero-subtitle">{total_agents} agents, {active_1h} active in the last hour. {total_open} open task{"" if total_open==1 else "s"}, {total_sched} scheduled job{"" if total_sched==1 else "s"}.</p>
      </div>
      <div class="hero-actions">
        <a href="/chat" class="btn primary">Open chat</a>
        <a href="/activity" class="btn ghost">Activity</a>
      </div>
    </section>
    """

    stats_html = f"""
    <div class="grid cols-4">
      <div class="stat"><div class="label">Health</div>
        <div class="value"><span class="pill {overall_class}">{overall_label}</span></div>
        <div class="sub">{ok} healthy · {warn} warn · {fail} fail</div></div>
      <div class="stat"><div class="label">Agents</div>
        <div class="value">{total_agents}</div>
        <div class="sub">{active_1h} active last hour</div></div>
      <div class="stat"><div class="label">Open tasks</div>
        <div class="value">{total_open}</div>
        <div class="sub">across all agents</div></div>
      <div class="stat"><div class="label">Scheduled</div>
        <div class="value">{total_sched}</div>
        <div class="sub"><a href="/schedule">view schedule →</a></div></div>
    </div>
    """

    # Agent cards — sort active-first, alphabetical tiebreaker
    def _sort_key(r):
        last = r.get("last_trajectory") or {}
        age = last.get("age_minutes", 1e9)
        return (age, r.get("agent", ""))

    agent_reports = sorted(
        [r for r in reports if "agent" in r],
        key=_sort_key,
    )

    cards: list[str] = []
    for r in agent_reports:
        name = r["agent"]
        last = r.get("last_trajectory") or {}
        age_str, age_sev = _age(last.get("mtime_utc"))
        d = doctor_by_agent.get(name, {})
        health = d.get("worst", "—")
        dot_cls = health if health in ("ok", "warn", "fail") else "dim"
        open_count = r.get("open_tasks", 0)
        sched_count = len(r.get("scheduled_tasks", []))
        model = r.get("model") or "default"
        chips: list[str] = []
        if open_count:
            chips.append(f'<span class="chip">{open_count} open</span>')
        if sched_count:
            chips.append(f'<span class="chip">{sched_count} cron</span>')
        if not chips:
            chips.append('<span class="chip">idle</span>')
        cards.append(f"""
        <a href="/agents/{esc(name)}" class="agent-card">
          <div class="head">
            <div class="name"><span class="health-dot {dot_cls}"></span> {esc(name)}</div>
            <div class="age">{age_str}</div>
          </div>
          <div class="row">{"".join(chips)}</div>
          <div class="model">{esc(model)}</div>
        </a>""")

    agents_html = f"""
    <div class="section-head">
      <h2>Agents</h2>
      <span class="link">{len(agent_reports)} total · sorted by activity</span>
    </div>
    <div class="agent-grid">{"".join(cards)}</div>
    """

    feed = _activity_feed(limit=12)
    feed_rows = "".join(
        f'<div class="event"><span class="ts">{esc(ev.get("ts","")[11:19])}</span>'
        f'<span class="agent"><a href="/agents/{esc(ev.get("_agent",""))}">{esc(ev.get("_agent","?"))}</a></span>'
        f'<span class="truncate">{_summarize_event(ev)}</span></div>'
        for ev in feed
    )

    sched = _all_scheduled()
    next_up = [s for s in sched if s.get("next_fire")][:6]
    sched_rows = "".join(
        f'<div class="event"><span class="ts">{esc(s["next_fire"][-8:] if len(s["next_fire"])>=8 else s["next_fire"])}</span>'
        f'<span class="agent"><a href="/agents/{esc(s["agent"])}">{esc(s["agent"])}</a></span>'
        f'<span class="truncate">{esc(s["name"])} <code>{esc(s["cron"])}</code>'
        f'{" <span class=\"pill dim\">system</span>" if s["kind"]=="systemEvent" else ""}'
        f'</span></div>'
        for s in next_up
    )

    bottom_html = f"""
    <div class="grid cols-2" style="margin-top:1.5em;">
      <div>
        <div class="section-head"><h2>Recent activity</h2><a href="/activity" class="link">view all →</a></div>
        <div class="panel">{feed_rows or "<div class='empty'>no events yet</div>"}</div>
      </div>
      <div>
        <div class="section-head"><h2>Upcoming</h2><a href="/schedule" class="link">view all →</a></div>
        <div class="panel">{sched_rows or "<div class='empty'>nothing scheduled soon</div>"}</div>
      </div>
    </div>
    """

    body = f"""
    {hero_html}
    {stats_html}
    {agents_html}
    {bottom_html}
    <div class="dev-hint">💨 refreshed {esc(now[11:19])}</div>
    """
    return HTMLResponse(_page("Overview", "/", body, refresh=60))


@app.get("/agents/{name}", response_class=HTMLResponse)
def agent_detail(name: str):
    if name not in _list_agents():
        raise HTTPException(404, f"no agent named {name}")

    doctor = _run_doctor_json()
    health = next((d for d in doctor if d.get("agent") == name), None)

    traj_path = _latest_trajectory(name)
    events = _read_trajectory_events(traj_path, limit=30) if traj_path else []
    session_id = traj_path.stem if traj_path else None

    active = _read_active_tasks(name)
    scheduled = _read_scheduled_tasks(name)

    cfg_path = AGENTS_DIR / name / "agent.yaml"
    import yaml
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mem_path = AGENTS_DIR / name / "memory" / f"{today}.md"
    memory_excerpt = mem_path.read_text()[:2500] if mem_path.exists() else ""

    # ---- tools / skills / subagents / behaviour ----
    global_defaults = {}
    gcfg_path = ROOT / "config.yaml"
    if gcfg_path.exists():
        try:
            global_defaults = (yaml.safe_load(gcfg_path.read_text()) or {}).get("defaults", {}) or {}
        except Exception:
            pass

    def _merged(key, default=None):
        if key in cfg:
            return cfg.get(key)
        return global_defaults.get(key, default)

    allowed = _merged("allowed_tools", []) or []
    disallowed = _merged("disallowed_tools", []) or []
    add_dirs = _merged("add_dirs", []) or []

    def _pills(items, cls):
        if not items:
            return '<span class="meta">(none)</span>'
        return " ".join(f'<span class="pill {cls}">{esc(i)}</span>' for i in items)

    tools_html = f"""
    <div class="grid cols-2">
      <div class="panel">
        <div class="meta" style="margin-bottom:0.6em;">allowed_tools ({len(allowed)})</div>
        <div>{_pills(allowed, "ok")}</div>
      </div>
      <div class="panel">
        <div class="meta" style="margin-bottom:0.6em;">disallowed_tools ({len(disallowed)})</div>
        <div>{_pills(disallowed, "fail")}</div>
      </div>
    </div>
    <div class="panel" style="margin-top:1em;">
      <div class="meta" style="margin-bottom:0.6em;">add_dirs ({len(add_dirs)})</div>
      {"<br>".join(f"<code>{esc(d)}</code>" for d in add_dirs) or '<span class="meta">(none)</span>'}
    </div>
    """

    skill_refs = cfg.get("skills") or []
    if skill_refs:
        resolved = [_resolve_skill(r, name) for r in skill_refs]
        sk_rows = "".join(
            f'<tr><td><code>{esc(s["ref"])}</code></td>'
            f'<td><span class="pill {"ok" if s["found"] else "fail"}">{esc(s["kind"])}</span></td>'
            f'<td class="meta">{esc(s["desc"] or "—")}</td></tr>'
            for s in resolved
        )
        skills_html = (
            f'<div class="panel" style="padding:0;"><table>'
            f'<thead><tr><th>ref</th><th>kind</th><th>description</th></tr></thead>'
            f'<tbody>{sk_rows}</tbody></table></div>'
        )
    else:
        skills_html = '<div class="panel meta">no skills declared</div>'

    subs = cfg.get("subagents") or {}
    if subs:
        sub_rows = []
        for sname, sbody in subs.items():
            sbody = sbody or {}
            stools = ", ".join(sbody.get("tools", []) or []) or "—"
            sdesc = (sbody.get("description") or "").strip()
            smodel = sbody.get("model", "inherit")
            smax = sbody.get("max_turns", "—")
            sub_rows.append(
                f'<tr><td><strong>{esc(sname)}</strong></td>'
                f'<td><code>{esc(smodel)}</code></td>'
                f'<td class="meta">{esc(stools)}</td>'
                f'<td>{esc(smax)}</td>'
                f'<td class="meta">{esc(sdesc)}</td></tr>'
            )
        subs_html = (
            f'<div class="panel" style="padding:0;"><table>'
            f'<thead><tr><th>name</th><th>model</th><th>tools</th><th>max_turns</th><th>description</th></tr></thead>'
            f'<tbody>{"".join(sub_rows)}</tbody></table></div>'
        )
    else:
        subs_html = '<div class="panel meta">no subagents declared</div>'

    approval_cfg = _merged("approval", {}) or {}
    approval_on = approval_cfg.get("enabled", True)
    approval_to = approval_cfg.get("timeout_seconds", "—")
    thinking = _merged("thinking", "off")
    allow_bots = _merged("allow_bots", False)
    max_hops = _merged("max_hops", "—")
    perm_mode = _merged("permission_mode", "—")
    setting_sources = ", ".join(_merged("setting_sources", []) or []) or "—"
    webhook_env = cfg.get("webhook_url_env", "—")
    bot_env = cfg.get("bot_token_env", "—")
    passthrough = cfg.get("env_passthrough", []) or []

    behaviour_rows = [
        ("thinking", f'<code>{esc(thinking)}</code>'),
        ("permission_mode", f'<code>{esc(perm_mode)}</code>'),
        ("max_turns", esc(cfg.get("max_turns", global_defaults.get("max_turns", "—")))),
        ("max_hops", esc(max_hops)),
        ("allow_bots", '<span class="pill ok">yes</span>' if allow_bots else '<span class="pill dim">no</span>'),
        ("approval gate",
         (f'<span class="pill ok">on</span> <span class="meta">timeout {esc(approval_to)}s</span>'
          if approval_on else '<span class="pill dim">off</span>')),
        ("setting_sources", f'<code>{esc(setting_sources)}</code>'),
        ("webhook_url_env", f'<code>{esc(webhook_env)}</code>'),
        ("bot_token_env", f'<code>{esc(bot_env)}</code>'),
        ("env_passthrough",
         ", ".join(f'<code>{esc(p)}</code>' for p in passthrough) or '<span class="meta">—</span>'),
    ]
    behav_html = (
        '<div class="panel" style="padding:0;"><table><tbody>'
        + "".join(f'<tr><td style="width:30%"><span class="meta">{esc(k)}</span></td><td>{v}</td></tr>'
                  for k, v in behaviour_rows)
        + '</tbody></table></div>'
    )

    # tool-usage stats from full trajectory
    full_events = _full_trajectory_events(name) if traj_path else []
    usage = _tool_usage_stats(full_events)
    if usage:
        top = usage[:12]
        max_c = top[0][1] if top else 1
        usage_rows = "".join(
            f'<tr><td><code>{esc(tname)}</code></td>'
            f'<td style="width:70%"><div style="background:var(--panel-2);border-radius:4px;height:18px;overflow:hidden;">'
            f'<div style="background:var(--accent);height:100%;width:{int(100*c/max_c)}%;"></div></div></td>'
            f'<td style="text-align:right">{c}</td></tr>'
            for tname, c in top
        )
        usage_html = (
            f'<div class="meta" style="margin-bottom:0.5em;">top tools in latest session ({len(full_events)} events)</div>'
            f'<div class="panel" style="padding:0;"><table><tbody>{usage_rows}</tbody></table></div>'
        )
    else:
        usage_html = '<div class="panel meta">no tool use recorded in latest session</div>'

    if health:
        hrows = "".join(
            f'<tr><td><span class="pill {esc(c["severity"])}">{esc(c["severity"])}</span></td>'
            f'<td><code>{esc(c["name"])}</code></td><td class="meta">{esc(c["detail"])}</td></tr>'
            for c in health.get("checks", [])
        )
        health_html = f'<div class="panel" style="padding:0;"><table><tbody>{hrows}</tbody></table></div>'
    else:
        health_html = '<div class="panel meta">doctor not run</div>'

    if active:
        t_rows = "".join(
            f'<tr><td><strong>{esc(t["title"])}</strong></td>'
            f'<td><span class="pill {"warn" if t["status"]=="blocked" else ("ok" if t["status"]=="in-progress" else "dim")}">{esc(t["status"])}</span></td>'
            f'<td class="meta">{esc(t["urgency"])}</td>'
            f'<td class="meta">{esc(t["updated"])}</td></tr>'
            for t in active
        )
        tasks_html = f'<div class="panel" style="padding:0;"><table><thead><tr><th>task</th><th>status</th><th>urgency</th><th>updated</th></tr></thead><tbody>{t_rows}</tbody></table></div>'
    else:
        tasks_html = '<div class="panel meta">no active tasks</div>'

    if scheduled:
        s_rows = "".join(
            f'<tr><td><strong>{esc(s["name"])}</strong></td>'
            f'<td><code>{esc(s["cron"] or "—")}</code></td>'
            f'<td>{"<span class=pill dim>system</span>" if s["kind"]=="systemEvent" else "<span class=pill ok>post</span>"}</td>'
            f'<td class="meta">{esc(s["next_fire"] or "—")}</td></tr>'
            for s in scheduled
        )
        sched_html = f'<div class="panel" style="padding:0;"><table><thead><tr><th>name</th><th>cron</th><th>kind</th><th>next fire</th></tr></thead><tbody>{s_rows}</tbody></table></div>'
    else:
        sched_html = '<div class="panel meta">no scheduled tasks</div>'

    if events:
        e_rows = "".join(
            f'<div class="event"><span class="ts">{esc(ev.get("ts","")[:19])}</span>'
            f'<span class="truncate">{_summarize_event(ev)}</span></div>'
            for ev in reversed(events)
        )
        traj_html = f'<div class="panel">{e_rows}</div>'
    else:
        traj_html = '<div class="panel meta">no trajectory yet</div>'

    body = f"""
    <div style="display:flex;align-items:center;justify-content:space-between;gap:1em;flex-wrap:wrap;">
      <h1>{esc(name)}</h1>
      <div style="display:flex;gap:0.5em;">
        <a href="/agents/{esc(name)}/edit" class="pill ok" style="padding:0.4em 0.9em;">✎ edit config</a>
        <a href="/chat/{esc(name)}" class="pill dim" style="padding:0.4em 0.9em;">💬 chat</a>
      </div>
    </div>
    <div class="meta">
      channel: <code>{esc(cfg.get('channel_id','—'))}</code>
      &nbsp; model: <code>{esc(cfg.get('model','—'))}</code>
      &nbsp; max_turns: {esc(cfg.get('max_turns','—'))}
      {f'&nbsp; session: <code>{esc(session_id)}</code>' if session_id else ''}
    </div>

    <h2>Health</h2>
    {health_html}

    <h2>Active tasks ({len(active)})</h2>
    {tasks_html}

    <h2>Scheduled ({len(scheduled)})</h2>
    {sched_html}

    <h2>Tools</h2>
    {tools_html}

    <h2>Skills ({len(skill_refs)})</h2>
    {skills_html}

    <h2>Subagents ({len(subs)})</h2>
    {subs_html}

    <h2>Behaviour</h2>
    {behav_html}

    <h2>Tool usage</h2>
    {usage_html}

    <h2>Recent trajectory ({len(events)} events)</h2>
    {traj_html}

    <h2>Today's memory</h2>
    {"<pre>"+esc(memory_excerpt)+"</pre>" if memory_excerpt else '<div class="panel meta">nothing written today</div>'}
    """
    return HTMLResponse(_page(name, "/", body))


@app.get("/activity", response_class=HTMLResponse)
def activity():
    feed = _activity_feed(limit=100)
    rows = "".join(
        f'<div class="event"><span class="ts">{esc(ev.get("ts","")[:19])}</span>'
        f'<span class="agent"><a href="/agents/{esc(ev.get("_agent",""))}">{esc(ev.get("_agent","?"))}</a></span>'
        f'<span class="type">{esc(ev.get("type","?"))}</span>'
        f'<span class="truncate">{_summarize_event(ev)}</span></div>'
        for ev in feed
    )
    body = f"""
    <h1>Activity</h1>
    <div class="meta">last {len(feed)} events across all agents (newest first)</div>
    <br>
    <div class="panel">{rows or "<div class='meta'>no events</div>"}</div>
    """
    return HTMLResponse(_page("Activity", "/activity", body))


@app.get("/schedule", response_class=HTMLResponse)
def schedule_page():
    sched = _all_scheduled()
    rows = "".join(
        f'<tr><td><span class="meta">{esc(s["next_fire"] or "—")}</span></td>'
        f'<td><a href="/agents/{esc(s["agent"])}">{esc(s["agent"])}</a></td>'
        f'<td>{esc(s["name"])}</td>'
        f'<td><code>{esc(s["cron"] or "—")}</code></td>'
        f'<td>{"<span class=pill dim>system</span>" if s["kind"]=="systemEvent" else "<span class=pill ok>post</span>"}</td></tr>'
        for s in sched
    )
    note = "" if HAVE_CRONITER else '<div class="meta">install croniter for next-fire times: <code>.venv/bin/pip install croniter</code></div><br>'
    body = f"""
    <h1>Schedule</h1>
    <div class="meta">{len(sched)} scheduled tasks, sorted by next fire</div>
    <br>
    {note}
    <div class="panel" style="padding:0;">
    <table><thead><tr><th>next fire</th><th>agent</th><th>task</th><th>cron</th><th>kind</th></tr></thead>
    <tbody>{rows}</tbody></table>
    </div>
    """
    return HTMLResponse(_page("Schedule", "/schedule", body))


@app.get("/health", response_class=HTMLResponse)
def health_page():
    doctor = _run_doctor_json()
    blocks: list[str] = []
    for d in doctor:
        if "error" in d:
            blocks.append(f'<div class="panel"><span class="pill fail">error</span> {esc(d["error"])}</div>')
            continue
        worst = d.get("worst", "ok")
        checks = d.get("checks", [])
        rows = "".join(
            f'<tr><td><span class="pill {esc(c["severity"])}">{esc(c["severity"])}</span></td>'
            f'<td><code>{esc(c["name"])}</code></td><td class="meta">{esc(c["detail"])}</td></tr>'
            for c in checks
        )
        agent = d["agent"]
        link = esc(agent) if agent == "(global)" else f'<a href="/agents/{esc(agent)}">{esc(agent)}</a>'
        blocks.append(f"""
        <h3>{link} <span class="pill {esc(worst)}">{esc(worst)}</span></h3>
        <div class="panel" style="padding:0;margin-bottom:1em;">
        <table><tbody>{rows}</tbody></table>
        </div>""")
    body = f"""
    <h1>Health</h1>
    <div class="meta">live output from <code>scripts/doctor.py</code></div>
    <br>
    {''.join(blocks)}
    """
    return HTMLResponse(_page("Health", "/health", body))


@app.get("/vault", response_class=HTMLResponse)
def vault_page():
    v = _vault_stats()
    if "error" in v:
        return HTMLResponse(_page("Vault", "/vault", f'<div class="panel"><span class="pill fail">error</span> {esc(v["error"])}</div>'))
    recent_rows = "".join(
        f'<tr><td>{esc(s["name"])}</td><td class="meta">{esc(s["mtime"])}</td></tr>'
        for s in v["recent_sessions"]
    )
    latest = v.get("latest_session")
    latest_str = f'{esc(latest["name"])} <span class="meta">({esc(latest["mtime"])})</span>' if latest else "—"
    body = f"""
    <h1>Vault</h1>
    <div class="meta"><code>{esc(v["vault_path"])}</code></div>
    <br>
    <div class="grid cols-3">
      <div class="stat"><div class="label">Sessions</div><div class="value">{v["sessions_count"]}</div><div class="sub">markdown files in Sessions/</div></div>
      <div class="stat"><div class="label">Topics</div><div class="value">{v["topics_count"]}</div><div class="sub">topic notes</div></div>
      <div class="stat"><div class="label">Last session</div><div class="value" style="font-size:1em">{latest_str}</div></div>
    </div>
    <h2>Recent sessions</h2>
    <div class="panel" style="padding:0;">
    <table><thead><tr><th>file</th><th>modified</th></tr></thead><tbody>{recent_rows}</tbody></table>
    </div>
    <div class="meta" style="margin-top:1em;">DECISIONS.md last touched: {esc(v.get("decisions_mtime") or "—")}</div>
    """
    return HTMLResponse(_page("Vault", "/vault", body))


# ---- chat pages ------------------------------------------------------------

def _chat_sidebar_list() -> list[dict]:
    if not _CHAT_READY:
        return []
    by_name = _agent_map()
    out: list[dict] = []
    for name in sorted(by_name.keys()):
        p = _history_path(name)
        n = sum(1 for _ in p.open()) if p.exists() else 0
        out.append({"name": name, "history_lines": n})
    return out


@app.get("/chat", response_class=HTMLResponse)
def chat_index_route():
    if not _CHAT_READY:
        return HTMLResponse(_page("Chat", "/chat", '<div class="panel"><span class="pill fail">chat disabled</span> — web_chat module failed to load.</div>'))
    nav_html = _topbar("/chat")
    return HTMLResponse(chat_index_html(_chat_sidebar_list(), nav_html, CSS))


@app.get("/chat/{agent}", response_class=HTMLResponse)
def chat_agent_route(agent: str):
    if not _CHAT_READY:
        raise HTTPException(503, "chat disabled")
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    nav_html = _topbar("/chat")
    return HTMLResponse(chat_page_html(agent, _chat_sidebar_list(), nav_html, CSS))


# ---- multi-agent split-screen (Phase 2) ------------------------------------

@app.get("/watch", response_class=HTMLResponse)
def watch_page(agents: str = ""):
    """Watch up to 4 agents in parallel (live trajectory feed each)."""
    all_names = _list_agents()
    selected = [a for a in (agents.split(",") if agents else all_names[:3]) if a in all_names][:4]

    if not selected:
        selected = all_names[:3]

    picker_opts = "".join(
        f'<option value="{esc(n)}"{" selected" if n in selected else ""}>@{esc(n)}</option>'
        for n in all_names
    )

    panes = []
    for name in selected:
        traj_path = _latest_trajectory(name)
        events = _read_trajectory_events(traj_path, limit=20) if traj_path else []
        rows = "".join(
            f'<div class="event"><span class="ts">{esc(ev.get("ts","")[:19])}</span>'
            f'<span class="truncate">{_summarize_event(ev)}</span></div>'
            for ev in reversed(events)
        )
        panes.append(f"""
        <div class="panel" style="display:flex;flex-direction:column;min-height:400px;">
          <div style="display:flex;align-items:center;gap:0.5em;margin-bottom:0.8em;">
            <h3 style="margin:0;"><a href="/agents/{esc(name)}">@{esc(name)}</a></h3>
            <span class="meta" style="flex:1;">{esc(traj_path.stem if traj_path else "no session")}</span>
            <a class="pill dim" href="/chat/{esc(name)}" style="text-decoration:none;">chat</a>
          </div>
          <div style="flex:1;overflow-y:auto;">{rows or '<div class="meta">no events</div>'}</div>
        </div>""")

    cols = min(len(selected), 2)
    body = f"""
    <div style="display:flex;align-items:center;gap:1em;">
      <h1>Watch</h1>
      <form method="get" style="flex:1;">
        <select name="agents" multiple size="1" onchange="this.form.submit()" style="background:var(--panel);color:var(--fg);border:1px solid var(--border);padding:0.3em;border-radius:6px;">{picker_opts}</select>
        <span class="meta">ctrl/cmd-click to pick multiple (max 4)</span>
      </form>
    </div>
    <br>
    <div class="grid" style="grid-template-columns:repeat({cols},1fr);">{''.join(panes)}</div>
    """
    return HTMLResponse(_page("Watch", "/chat", body, refresh=15))


# ---- unified search (Phase 3) ----------------------------------------------

def _search_everything(query: str, limit: int = 40) -> dict:
    query_l = query.lower()
    hits = {"agents": [], "vault": [], "trajectories": [], "history": []}
    if not query_l:
        return hits

    # Agent workspaces — scan LEARNINGS, MEMORY, identity files.
    for name in _list_agents():
        for fname in ("LEARNINGS.md", "MEMORY.md", "IDENTITY.md", "TOOLS.md"):
            p = AGENTS_DIR / name / fname
            if p.exists():
                text = p.read_text()
                if query_l in text.lower():
                    idx = text.lower().find(query_l)
                    snippet = text[max(0, idx - 80): idx + 200].replace("\n", " ")
                    hits["agents"].append({"agent": name, "file": fname, "snippet": snippet})

    # Vault — Sessions/ and Topics/.
    for sub in ("Sessions", "Topics"):
        d = VAULT_PATH / sub
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                text = p.read_text()
            except Exception:
                continue
            if query_l in text.lower() or query_l in p.name.lower():
                idx = text.lower().find(query_l)
                snippet = (text[max(0, idx - 80): idx + 200] if idx >= 0 else text[:200]).replace("\n", " ")
                hits["vault"].append({"file": f"{sub}/{p.name}", "snippet": snippet})
            if len(hits["vault"]) >= limit:
                break

    # Recent trajectory events.
    for name in _list_agents():
        path = _latest_trajectory(name)
        if not path:
            continue
        try:
            events = _read_trajectory_events(path, limit=0)
        except Exception:
            continue
        for ev in events[-500:]:
            blob = json.dumps(ev).lower()
            if query_l in blob:
                hits["trajectories"].append({
                    "agent": name,
                    "session": path.stem,
                    "ts": ev.get("ts"),
                    "type": ev.get("type"),
                    "summary": _summarize_event(ev)[:200],
                })
                if len(hits["trajectories"]) >= limit:
                    break

    # Web chat history.
    if _CHAT_READY:
        for name in _list_agents():
            p = _history_path(name)
            if not p.exists():
                continue
            with p.open() as f:
                for line in f:
                    if query_l in line.lower():
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        hits["history"].append({
                            "agent": name, "role": rec.get("role"),
                            "ts": rec.get("ts"),
                            "snippet": (rec.get("content") or "")[:200],
                        })
                        if len(hits["history"]) >= limit:
                            break
    return hits


@app.get("/search", response_class=HTMLResponse)
def search_page(q: str = ""):
    results_html = ""
    if q:
        hits = _search_everything(q)
        total = sum(len(v) for v in hits.values())
        blocks = []
        for bucket, items in hits.items():
            if not items:
                continue
            rows = "".join(
                (f'<div class="event"><span class="agent">@{esc(i.get("agent","?"))}</span>'
                 f'<span class="meta">{esc(i.get("file") or i.get("session") or i.get("ts") or "")}</span><br>'
                 f'<span style="font-size:0.88em;">{esc(i.get("snippet") or i.get("summary") or "")}</span></div>')
                for i in items
            )
            blocks.append(f'<h3>{esc(bucket)} ({len(items)})</h3><div class="panel">{rows}</div>')
        results_html = f'<div class="meta">{total} hits</div><br>' + "".join(blocks)
    else:
        results_html = '<div class="meta">Type a query. Searches agent files, vault, trajectories, chat history.</div>'

    body = f"""
    <h1>Search</h1>
    <form method="get" style="margin:1em 0;">
      <input name="q" value="{esc(q)}" placeholder="query…" autofocus
        style="width:100%;background:var(--panel);border:1px solid var(--border);color:var(--fg);padding:0.8em 1em;border-radius:8px;font-size:1em;">
    </form>
    {results_html}
    """
    return HTMLResponse(_page("Search", "/search", body, refresh=0))


@app.get("/api/search")
def api_search(q: str = "", limit: int = 40):
    return JSONResponse(_search_everything(q, limit=limit))


# ---- PWA manifest + service worker (Phase 3) -------------------------------

@app.get("/manifest.json")
def pwa_manifest():
    return JSONResponse({
        "name": "Prana AgentOS",
        "short_name": "AgentOS",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0e0e10",
        "theme_color": "#6fa8ff",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    })


@app.get("/sw.js")
def pwa_service_worker():
    sw = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => {
  // Pass-through; we don't pre-cache anything (data is dynamic).
});
self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {title:'AgentOS', body:'Update'};
  e.waitUntil(self.registration.showNotification(data.title, {body:data.body, icon:'/static/icon-192.png'}));
});
"""
    return HTMLResponse(sw, media_type="application/javascript")


# ---- JSON API --------------------------------------------------------------

@app.get("/api/status")
def api_status():
    return JSONResponse(_run_status_json())


@app.get("/api/doctor")
def api_doctor():
    return JSONResponse(_run_doctor_json())


@app.get("/api/agents/{name}/trajectory")
def api_trajectory(name: str, limit: int = 50):
    path = _latest_trajectory(name)
    if not path:
        return JSONResponse({"error": "no trajectory"}, status_code=404)
    events = _read_trajectory_events(path, limit=limit)
    return JSONResponse({"session": path.stem, "events": events})


@app.get("/api/agents/{name}/config")
def api_config(name: str):
    cfg_path = AGENTS_DIR / name / "agent.yaml"
    if not cfg_path.exists():
        return JSONResponse({"error": "no such agent"}, status_code=404)
    import yaml
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    skills_resolved = [_resolve_skill(r, name) for r in (cfg.get("skills") or [])]
    full_events = _full_trajectory_events(name)
    tool_usage = dict(_tool_usage_stats(full_events))
    return JSONResponse({
        "agent": name,
        "config": cfg,
        "skills_resolved": skills_resolved,
        "tool_usage_latest_session": tool_usage,
    })


@app.get("/api/agents/{name}/tasks")
def api_tasks(name: str):
    return JSONResponse({
        "active": _read_active_tasks(name),
        "scheduled": _read_scheduled_tasks(name),
    })


@app.get("/api/schedule")
def api_schedule():
    return JSONResponse(_all_scheduled())


@app.get("/api/activity")
def api_activity(limit: int = 100):
    return JSONResponse(_activity_feed(limit=limit))


@app.get("/api/vault")
def api_vault():
    return JSONResponse(_vault_stats())


# ---- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--ssl-certfile", default=None, help="path to TLS cert (e.g. Tailscale-issued .crt)")
    ap.add_argument("--ssl-keyfile", default=None, help="path to TLS key (e.g. Tailscale-issued .key)")
    args = ap.parse_args()
    scheme = "https" if (args.ssl_certfile and args.ssl_keyfile) else "http"
    print(f"AgentOS dashboard -> {scheme}://{args.host}:{args.port}/")
    if not HAVE_CRONITER:
        print("note: install croniter for next-fire times: ./.venv/bin/pip install croniter")
    kwargs = {"host": args.host, "port": args.port, "log_level": "warning"}
    if args.ssl_certfile and args.ssl_keyfile:
        kwargs["ssl_certfile"] = args.ssl_certfile
        kwargs["ssl_keyfile"] = args.ssl_keyfile
    uvicorn.run(app, **kwargs)


if __name__ == "__main__":
    main()

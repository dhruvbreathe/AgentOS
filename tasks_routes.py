"""tasks_routes.py — FastAPI router for /tasks, /goals, /budgets.

Mounted by dashboard.py. Shares the same CSS/nav wrapper; renders three
surfaces:
  /tasks    — Kanban board (status columns) + filters (owner, goal, priority)
  /goals    — tree view, mission/objective/initiative hierarchy
  /budgets  — per-agent token spend this month, vs `budget_monthly_tokens`
              declared in each agent's agent.yaml (inherits config.yaml default).

All writes go through tasks.py. JSON API at /api/tasks, /api/goals,
/api/budgets for agent-side tool calls.
"""
from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import tasks

ROOT = Path(__file__).resolve().parent
AGENTS_DIR = ROOT / "agents"
CONFIG_PATH = ROOT / "config.yaml"

router = APIRouter()


def _esc(s: Any) -> str:
    return (str(s) if s is not None else "").replace("&", "&amp;").replace(
        "<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _default_budget() -> int | None:
    """Monthly token ceiling default from config.yaml."""
    if not CONFIG_PATH.exists():
        return None
    cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    defaults = cfg.get("defaults") or {}
    return defaults.get("budget_monthly_tokens")


def _agent_budgets() -> dict[str, int | None]:
    """Per-agent budget override → resolved budget. None means no cap."""
    default = _default_budget()
    out: dict[str, int | None] = {}
    if not AGENTS_DIR.exists():
        return out
    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        yml = agent_dir / "agent.yaml"
        if not yml.exists():
            continue
        try:
            cfg = yaml.safe_load(yml.read_text()) or {}
        except yaml.YAMLError:
            continue
        out[agent_dir.name] = cfg.get("budget_monthly_tokens", default)
    return out


# ---- Pydantic I/O ----------------------------------------------------------

class TaskIn(BaseModel):
    title: str
    description: str | None = None
    status: str = "backlog"
    priority: str = "normal"
    owner: str | None = None
    goal_id: int | None = None
    parent_task_id: int | None = None
    discord_thread_id: str | None = None
    session_ref: str | None = None
    actor: str = "human"


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    owner: str | None = None
    goal_id: int | None = None
    parent_task_id: int | None = None
    discord_thread_id: str | None = None
    session_ref: str | None = None
    cost_tokens: int | None = None
    actor: str = "human"


class CommentIn(BaseModel):
    actor: str
    body: str


class GoalIn(BaseModel):
    title: str
    description: str | None = None
    parent_id: int | None = None
    kind: str = "objective"


class GoalPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    parent_id: int | None = None
    kind: str | None = None
    closed_at: str | None = None


# ---- JSON API --------------------------------------------------------------

@router.get("/api/tasks")
def api_list_tasks(status: str | None = None, owner: str | None = None,
                   goal_id: int | None = None, include_done: bool = False):
    return JSONResponse({
        "tasks": tasks.list_tasks(
            status=status, owner=owner, goal_id=goal_id,
            include_done=include_done),
    })


@router.get("/api/tasks/{task_id}")
def api_get_task(task_id: int):
    t = tasks.get_task(task_id)
    if not t:
        raise HTTPException(404, "no such task")
    return JSONResponse(t)


@router.post("/api/tasks")
def api_create_task(payload: TaskIn):
    try:
        tid = tasks.create_task(**payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"id": tid, "task": tasks.get_task(tid)})


@router.patch("/api/tasks/{task_id}")
def api_patch_task(task_id: int, payload: TaskPatch):
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    actor = data.pop("actor", "human")
    try:
        tasks.update_task(task_id, actor=actor, **data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True, "task": tasks.get_task(task_id)})


@router.post("/api/tasks/{task_id}/comment")
def api_comment_task(task_id: int, payload: CommentIn):
    if not tasks.get_task(task_id):
        raise HTTPException(404, "no such task")
    tasks.add_comment(task_id, payload.actor, payload.body)
    return JSONResponse({"ok": True, "task": tasks.get_task(task_id)})


@router.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int):
    tasks.delete_task(task_id)
    return JSONResponse({"ok": True})


@router.get("/api/goals")
def api_list_goals():
    return JSONResponse({"goals": tasks.list_goals(), "tree": tasks.goal_tree()})


@router.post("/api/goals")
def api_create_goal(payload: GoalIn):
    try:
        gid = tasks.create_goal(**payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"id": gid})


@router.patch("/api/goals/{goal_id}")
def api_patch_goal(goal_id: int, payload: GoalPatch):
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    tasks.update_goal(goal_id, **data)
    return JSONResponse({"ok": True})


@router.delete("/api/goals/{goal_id}")
def api_delete_goal(goal_id: int):
    tasks.delete_goal(goal_id)
    return JSONResponse({"ok": True})


@router.get("/api/budgets")
def api_budgets(month: str | None = None):
    totals = tasks.spend_totals(month=month)
    budgets = _agent_budgets()
    out = []
    for agent, cap in budgets.items():
        t = totals.get(agent, {})
        used = (t.get("input_tokens") or 0) + (t.get("output_tokens") or 0)
        out.append({
            "agent": agent,
            "budget": cap,
            "used": used,
            "remaining": (cap - used) if cap else None,
            "pct": (used * 100.0 / cap) if cap else None,
            "cache_read": t.get("cache_read_tokens") or 0,
            "cache_create": t.get("cache_create_tokens") or 0,
            "sessions": t.get("sessions") or 0,
        })
    # Include agents that spent tokens but have no yaml (orphaned).
    for agent in totals:
        if agent not in budgets:
            t = totals[agent]
            out.append({
                "agent": agent, "budget": None,
                "used": (t.get("input_tokens") or 0) + (t.get("output_tokens") or 0),
                "remaining": None, "pct": None,
                "cache_read": t.get("cache_read_tokens") or 0,
                "cache_create": t.get("cache_create_tokens") or 0,
                "sessions": t.get("sessions") or 0,
            })
    return JSONResponse({"month": month, "agents": sorted(out, key=lambda x: -x["used"])})


# ---- HTML pages ------------------------------------------------------------

TASKS_CSS = """
.kanban{display:grid;grid-template-columns:repeat(6,1fr);gap:0.6em;margin-top:1em;}
.col{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:0.6em;min-height:200px;display:flex;flex-direction:column;}
.col h3{margin:0 0 0.5em 0;font-size:0.75em;text-transform:uppercase;letter-spacing:0.08em;color:var(--fg-mute);display:flex;justify-content:space-between;}
.col h3 .count{background:var(--panel-2);padding:0.1em 0.5em;border-radius:999px;font-size:0.85em;color:var(--fg-dim);}
.tcard{background:var(--panel-2);border:1px solid var(--border);border-radius:6px;padding:0.6em;margin-bottom:0.5em;cursor:pointer;}
.tcard:hover{border-color:var(--accent);}
.tcard .title{font-size:0.9em;color:var(--fg);margin-bottom:0.3em;}
.tcard .meta{font-size:0.75em;color:var(--fg-mute);display:flex;justify-content:space-between;gap:0.5em;flex-wrap:wrap;}
.tcard.urgent{border-left:3px solid var(--fail);}
.tcard.normal{border-left:3px solid var(--border);}
.tcard.low{border-left:3px solid var(--fg-mute);opacity:0.85;}
.owner-chip{background:var(--panel);border:1px solid var(--border);padding:0.1em 0.4em;border-radius:4px;font-size:0.75em;color:var(--accent);}
.goal-chip{background:var(--panel);border:1px solid var(--border);padding:0.1em 0.4em;border-radius:4px;font-size:0.75em;color:var(--accent-2);}
.filters{display:flex;gap:0.6em;margin-bottom:0.5em;flex-wrap:wrap;align-items:center;}
.filters input, .filters select{background:var(--panel);border:1px solid var(--border);color:var(--fg);padding:0.4em 0.6em;border-radius:6px;}
.btn-primary{background:var(--accent);color:var(--accent-ink);border:none;padding:0.5em 1em;border-radius:6px;cursor:pointer;font-weight:500;}
.btn-primary:hover{opacity:0.9;}
.btn-secondary{background:var(--panel-2);color:var(--fg);border:1px solid var(--border);padding:0.5em 1em;border-radius:6px;cursor:pointer;}
#taskModal, #goalModal{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:100;align-items:center;justify-content:center;}
#taskModal.open, #goalModal.open{display:flex;}
.modal-body{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:1.2em;width:min(640px,90vw);max-height:90vh;overflow-y:auto;}
.modal-body h2{margin:0 0 0.5em 0;}
.modal-body label{display:block;margin:0.5em 0;font-size:0.85em;color:var(--fg-dim);}
.modal-body input, .modal-body select, .modal-body textarea{width:100%;background:var(--panel-2);border:1px solid var(--border);color:var(--fg);padding:0.5em;border-radius:6px;box-sizing:border-box;font-family:inherit;}
.modal-body textarea{min-height:100px;}
.modal-body .btn-row{display:flex;gap:0.6em;justify-content:flex-end;margin-top:1em;}
.events{margin-top:1em;border-top:1px solid var(--border);padding-top:0.8em;}
.event{font-size:0.8em;color:var(--fg-dim);padding:0.3em 0;border-bottom:1px dotted var(--border);}
.event .actor{color:var(--accent);}
.event .kind{color:var(--fg-mute);}
.goal-tree{list-style:none;padding:0;}
.goal-tree li{padding:0.5em;border-left:2px solid var(--border);margin-left:0.5em;}
.goal-tree .kind-mission{border-left-color:var(--fail);}
.goal-tree .kind-vision{border-left-color:#ffd166;}
.goal-tree .kind-objective{border-left-color:var(--accent);}
.goal-tree .kind-initiative{border-left-color:var(--accent-2);}
.budget-bar{background:var(--panel-2);border-radius:4px;height:10px;overflow:hidden;margin-top:0.4em;}
.budget-bar .fill{height:100%;background:var(--accent);transition:width 0.3s;}
.budget-bar .fill.warn{background:#ffb84d;}
.budget-bar .fill.over{background:var(--fail);}
.budget-card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:0.8em;margin-bottom:0.6em;}
.budget-card h4{margin:0 0 0.3em 0;display:flex;justify-content:space-between;}
.budget-card .nums{color:var(--fg-mute);font-size:0.85em;}
"""


def _render(title: str, nav_active: str, body: str) -> str:
    """Use dashboard's real shell so nav + styling match."""
    from dashboard import _page  # late import avoids circular
    body_with_css = f"<style>{TASKS_CSS}</style>{body}"
    return _page(title, nav_active, body_with_css)


def _tasks_body() -> str:
    return """
<h1>Tasks</h1>
<div class="filters">
  <input id="f-search" placeholder="search…">
  <select id="f-owner"><option value="">all owners</option></select>
  <select id="f-goal"><option value="">all goals</option></select>
  <select id="f-priority">
    <option value="">all priorities</option>
    <option value="urgent">urgent</option>
    <option value="normal">normal</option>
    <option value="low">low</option>
  </select>
  <label style="font-size:0.85em;color:var(--fg-dim)"><input type="checkbox" id="f-done"> show done</label>
  <button class="btn-primary" id="newTaskBtn">+ New task</button>
</div>
<div id="kanban" class="kanban"></div>

<div id="taskModal">
  <div class="modal-body">
    <h2 id="tm-title">New task</h2>
    <label>Title <input id="tm-t-title"></label>
    <label>Description <textarea id="tm-t-desc"></textarea></label>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.6em;">
      <label>Status <select id="tm-t-status"></select></label>
      <label>Priority <select id="tm-t-priority"></select></label>
      <label>Owner <select id="tm-t-owner"></select></label>
      <label>Goal <select id="tm-t-goal"></select></label>
    </div>
    <label>Session ref <input id="tm-t-session" placeholder="Sessions/2026-04-20-topic.md"></label>
    <div id="tm-events" class="events" style="display:none;"></div>
    <div class="btn-row">
      <button class="btn-secondary" id="tm-delete" style="margin-right:auto;display:none;">Delete</button>
      <button class="btn-secondary" id="tm-cancel">Cancel</button>
      <button class="btn-primary" id="tm-save">Save</button>
    </div>
  </div>
</div>

<script>
const STATUSES = ['backlog','open','in_progress','waiting','blocked','done'];
const PRIORITIES = ['urgent','normal','low'];
let ALL_TASKS = [];
let OWNERS = [];
let GOALS = [];
let EDITING = null;

async function loadAgents(){
  const r = await fetch('/api/status');
  const d = await r.json();
  OWNERS = (d.agents || []).map(a => a.name).sort();
  const sel = document.getElementById('f-owner');
  const selT = document.getElementById('tm-t-owner');
  for (const o of OWNERS){
    sel.innerHTML += `<option value="${o}">${o}</option>`;
    selT.innerHTML += `<option value="${o}">${o}</option>`;
  }
}

async function loadGoals(){
  const r = await fetch('/api/goals');
  const d = await r.json();
  GOALS = d.goals || [];
  const sel = document.getElementById('f-goal');
  const selT = document.getElementById('tm-t-goal');
  sel.innerHTML = '<option value="">all goals</option>';
  selT.innerHTML = '<option value="">— none —</option>';
  for (const g of GOALS){
    sel.innerHTML += `<option value="${g.id}">${g.title}</option>`;
    selT.innerHTML += `<option value="${g.id}">${g.title}</option>`;
  }
}

async function loadTasks(){
  const done = document.getElementById('f-done').checked;
  const r = await fetch(`/api/tasks?include_done=${done}`);
  const d = await r.json();
  ALL_TASKS = d.tasks || [];
  render();
}

function esc(s){ return String(s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function render(){
  const q = document.getElementById('f-search').value.toLowerCase();
  const fOwner = document.getElementById('f-owner').value;
  const fGoal = document.getElementById('f-goal').value;
  const fPrio = document.getElementById('f-priority').value;
  const filtered = ALL_TASKS.filter(t => {
    if (q && !(t.title||'').toLowerCase().includes(q) &&
             !(t.description||'').toLowerCase().includes(q)) return false;
    if (fOwner && t.owner !== fOwner) return false;
    if (fGoal && String(t.goal_id) !== fGoal) return false;
    if (fPrio && t.priority !== fPrio) return false;
    return true;
  });
  const board = document.getElementById('kanban');
  board.innerHTML = '';
  for (const s of STATUSES){
    const col = document.createElement('div');
    col.className = 'col';
    const items = filtered.filter(t => t.status === s);
    col.innerHTML = `<h3>${s.replace('_',' ')} <span class="count">${items.length}</span></h3>`;
    for (const t of items){
      const card = document.createElement('div');
      card.className = `tcard ${t.priority}`;
      const goal = GOALS.find(g => g.id === t.goal_id);
      card.innerHTML = `
        <div class="title">${esc(t.title)}</div>
        <div class="meta">
          ${t.owner ? `<span class="owner-chip">@${esc(t.owner)}</span>` : ''}
          ${goal ? `<span class="goal-chip">${esc(goal.title)}</span>` : ''}
          <span>#${t.id}</span>
        </div>`;
      card.onclick = () => openModal(t);
      col.appendChild(card);
    }
    board.appendChild(col);
  }
}

function openModal(t){
  EDITING = t;
  const isNew = !t;
  document.getElementById('tm-title').textContent = isNew ? 'New task' : `Task #${t.id}`;
  document.getElementById('tm-t-title').value = t?.title || '';
  document.getElementById('tm-t-desc').value = t?.description || '';
  document.getElementById('tm-t-session').value = t?.session_ref || '';
  const sel = document.getElementById('tm-t-status');
  sel.innerHTML = STATUSES.map(s => `<option value="${s}"${t?.status===s?' selected':''}>${s}</option>`).join('');
  const selP = document.getElementById('tm-t-priority');
  selP.innerHTML = PRIORITIES.map(p => `<option value="${p}"${(t?.priority||'normal')===p?' selected':''}>${p}</option>`).join('');
  const selO = document.getElementById('tm-t-owner');
  selO.innerHTML = '<option value="">— none —</option>' + OWNERS.map(o => `<option value="${o}"${t?.owner===o?' selected':''}>${o}</option>`).join('');
  const selG = document.getElementById('tm-t-goal');
  selG.innerHTML = '<option value="">— none —</option>' + GOALS.map(g => `<option value="${g.id}"${t?.goal_id===g.id?' selected':''}>${esc(g.title)}</option>`).join('');
  const evDiv = document.getElementById('tm-events');
  if (t && t.events && t.events.length){
    evDiv.style.display = 'block';
    evDiv.innerHTML = '<strong style="font-size:0.85em;">Activity</strong>' + t.events.map(e => {
      const payload = e.payload ? (() => { try { return JSON.parse(e.payload); } catch { return {}; } })() : {};
      const body = payload.body || payload.title || Object.keys(payload).map(k => `${k}=${payload[k]}`).join(' ');
      return `<div class="event"><span class="actor">@${esc(e.actor)}</span> <span class="kind">${e.kind}</span> · ${esc(e.created)}<br>${esc(body)}</div>`;
    }).join('');
  } else {
    evDiv.style.display = 'none';
  }
  document.getElementById('tm-delete').style.display = isNew ? 'none' : 'inline-block';
  document.getElementById('taskModal').classList.add('open');
}

function closeModal(){
  document.getElementById('taskModal').classList.remove('open');
  EDITING = null;
}

document.getElementById('newTaskBtn').onclick = () => openModal(null);
document.getElementById('tm-cancel').onclick = closeModal;
document.getElementById('tm-save').onclick = async () => {
  const body = {
    title: document.getElementById('tm-t-title').value.trim(),
    description: document.getElementById('tm-t-desc').value,
    status: document.getElementById('tm-t-status').value,
    priority: document.getElementById('tm-t-priority').value,
    owner: document.getElementById('tm-t-owner').value || null,
    goal_id: parseInt(document.getElementById('tm-t-goal').value) || null,
    session_ref: document.getElementById('tm-t-session').value || null,
  };
  if (!body.title) { alert('title required'); return; }
  if (EDITING){
    await fetch(`/api/tasks/${EDITING.id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  } else {
    await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  }
  closeModal();
  loadTasks();
};
document.getElementById('tm-delete').onclick = async () => {
  if (!EDITING || !confirm(`Delete task #${EDITING.id}?`)) return;
  await fetch(`/api/tasks/${EDITING.id}`, {method:'DELETE'});
  closeModal();
  loadTasks();
};

for (const id of ['f-search','f-owner','f-goal','f-priority']){
  document.getElementById(id).addEventListener('input', render);
}
document.getElementById('f-done').addEventListener('change', loadTasks);

(async () => {
  await loadAgents();
  await loadGoals();
  await loadTasks();
})();
</script>
"""


def _goals_body() -> str:
    return """
<h1>Goals</h1>
<p class="meta">Hierarchical ancestry — tasks trace back to mission through here.</p>
<button class="btn-primary" id="newGoalBtn">+ New goal</button>
<ul id="goalTree" class="goal-tree"></ul>

<div id="goalModal">
  <div class="modal-body">
    <h2 id="gm-title">New goal</h2>
    <label>Title <input id="gm-g-title"></label>
    <label>Description <textarea id="gm-g-desc"></textarea></label>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.6em;">
      <label>Kind <select id="gm-g-kind">
        <option value="mission">mission</option>
        <option value="vision">vision</option>
        <option value="objective" selected>objective</option>
        <option value="initiative">initiative</option>
      </select></label>
      <label>Parent <select id="gm-g-parent"><option value="">— none —</option></select></label>
    </div>
    <div class="btn-row">
      <button class="btn-secondary" id="gm-delete" style="margin-right:auto;display:none;">Delete</button>
      <button class="btn-secondary" id="gm-cancel">Cancel</button>
      <button class="btn-primary" id="gm-save">Save</button>
    </div>
  </div>
</div>

<script>
let GOALS_FLAT = [];
let GOAL_TREE = [];
let EDITING_G = null;

function esc(s){ return String(s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function loadGoals(){
  const r = await fetch('/api/goals');
  const d = await r.json();
  GOALS_FLAT = d.goals || [];
  GOAL_TREE = d.tree || [];
  renderTree();
}

function renderNode(g){
  const children = (g.children || []).map(renderNode).join('');
  return `<li class="kind-${g.kind}" data-id="${g.id}">
    <strong>${esc(g.title)}</strong>
    <span class="meta">(${g.kind})</span>
    ${g.description ? `<div class="meta" style="font-size:0.85em;">${esc(g.description)}</div>` : ''}
    ${children ? `<ul class="goal-tree">${children}</ul>` : ''}
  </li>`;
}

function renderTree(){
  const ul = document.getElementById('goalTree');
  ul.innerHTML = GOAL_TREE.map(renderNode).join('') || '<li class="meta">No goals yet.</li>';
  ul.querySelectorAll('li[data-id]').forEach(li => {
    li.addEventListener('click', e => {
      e.stopPropagation();
      const id = parseInt(li.dataset.id);
      const g = GOALS_FLAT.find(x => x.id === id);
      if (g) openGoalModal(g);
    });
  });
}

function openGoalModal(g){
  EDITING_G = g;
  const isNew = !g;
  document.getElementById('gm-title').textContent = isNew ? 'New goal' : `Goal #${g.id}`;
  document.getElementById('gm-g-title').value = g?.title || '';
  document.getElementById('gm-g-desc').value = g?.description || '';
  document.getElementById('gm-g-kind').value = g?.kind || 'objective';
  const selP = document.getElementById('gm-g-parent');
  selP.innerHTML = '<option value="">— none —</option>' + GOALS_FLAT
    .filter(x => !g || x.id !== g.id)
    .map(x => `<option value="${x.id}"${g?.parent_id===x.id?' selected':''}>${esc(x.title)}</option>`).join('');
  document.getElementById('gm-delete').style.display = isNew ? 'none' : 'inline-block';
  document.getElementById('goalModal').classList.add('open');
}

function closeGoalModal(){ document.getElementById('goalModal').classList.remove('open'); EDITING_G = null; }

document.getElementById('newGoalBtn').onclick = () => openGoalModal(null);
document.getElementById('gm-cancel').onclick = closeGoalModal;
document.getElementById('gm-save').onclick = async () => {
  const body = {
    title: document.getElementById('gm-g-title').value.trim(),
    description: document.getElementById('gm-g-desc').value,
    kind: document.getElementById('gm-g-kind').value,
    parent_id: parseInt(document.getElementById('gm-g-parent').value) || null,
  };
  if (!body.title) { alert('title required'); return; }
  if (EDITING_G){
    await fetch(`/api/goals/${EDITING_G.id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  } else {
    await fetch('/api/goals', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  }
  closeGoalModal();
  loadGoals();
};
document.getElementById('gm-delete').onclick = async () => {
  if (!EDITING_G || !confirm(`Delete goal #${EDITING_G.id}?`)) return;
  await fetch(`/api/goals/${EDITING_G.id}`, {method:'DELETE'});
  closeGoalModal();
  loadGoals();
};

loadGoals();
</script>
"""


def _budgets_body() -> str:
    return """
<h1>Budgets</h1>
<p class="meta">Token spend this month vs. <code>budget_monthly_tokens</code> per agent. Set in each agent's <code>agent.yaml</code>, or globally in <code>config.yaml</code>.</p>
<div id="budgetList">Loading…</div>

<script>
function esc(s){ return String(s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmt(n){ if(n==null) return '—'; if(n>1e6) return (n/1e6).toFixed(2)+'M'; if(n>1e3) return (n/1e3).toFixed(1)+'k'; return n; }

async function loadBudgets(){
  const r = await fetch('/api/budgets');
  const d = await r.json();
  const list = document.getElementById('budgetList');
  if (!d.agents.length){
    list.innerHTML = '<p class="meta">No spend recorded this month yet. Run <code>python scripts/track_spend.py</code> to backfill from trajectories.</p>';
    return;
  }
  list.innerHTML = d.agents.map(a => {
    const pct = a.pct;
    const cls = pct == null ? '' : (pct > 100 ? 'over' : (pct > 80 ? 'warn' : ''));
    const width = pct == null ? 0 : Math.min(pct, 100);
    return `<div class="budget-card">
      <h4>@${esc(a.agent)} <span>${fmt(a.used)} / ${a.budget ? fmt(a.budget) : 'uncapped'}${pct != null ? ` (${pct.toFixed(1)}%)` : ''}</span></h4>
      <div class="nums">
        in: ${fmt(a.used)} · cache read: ${fmt(a.cache_read)} · cache create: ${fmt(a.cache_create)} · sessions: ${a.sessions}
      </div>
      ${a.budget ? `<div class="budget-bar"><div class="fill ${cls}" style="width:${width}%"></div></div>` : ''}
    </div>`;
  }).join('');
}
loadBudgets();
</script>
"""


@router.get("/tasks", response_class=HTMLResponse)
def page_tasks():
    return HTMLResponse(_render("Tasks", "/tasks", _tasks_body()))


@router.get("/goals", response_class=HTMLResponse)
def page_goals():
    return HTMLResponse(_render("Goals", "/goals", _goals_body()))


@router.get("/budgets", response_class=HTMLResponse)
def page_budgets():
    return HTMLResponse(_render("Budgets", "/budgets", _budgets_body()))

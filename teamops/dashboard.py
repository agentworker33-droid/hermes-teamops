"""Self-contained HTML fleet dashboard (Task 2.8).

``team_status(html=True)`` / ``hermes teams status --html`` render a single
standalone .html file — no external CDN, no JS library downloads — with:

- summary cards (profiles, home crons, 24h executions, cron-health flags)
- per-profile fleet panel: memory bars, skills, crons, 24h execution stats
- cron health table (Cron911 issues/info: silent gaps, failures, dead models)
- per-team kanban progress + an interactive SVG mesh graph (click a role to
  highlight its edges)

All reads are HERMES_HOME-relative. Every optional data source degrades
gracefully: if Cron911 (or anything else) raises, that section just renders
"unavailable" — no nags, no broken UI (acceptance rule).
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from . import fleet

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       background: #0f1419; color: #e7e9ea; line-height: 1.45; }
header { padding: 18px 28px; border-bottom: 1px solid #2f3336; background: #161b22; }
header h1 { margin: 0 0 4px; font-size: 20px; letter-spacing: .2px; }
header .meta { color: #8b949e; font-size: 13px; word-break: break-all; }
main { padding: 20px 28px 8px; max-width: 1200px; margin: 0 auto; }
section { margin-bottom: 26px; }
section h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .6px; color: #8b949e; margin: 0 0 10px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.card { background: #161b22; border: 1px solid #2f3336; border-radius: 10px; padding: 12px 14px; }
.card .num { font-size: 26px; font-weight: 700; }
.card .lbl { font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: .4px; }
.card.warn .num { color: #f85149; }
.card.ok .num { color: #3fb950; }
.card.info .num { color: #58a6ff; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #21262d; vertical-align: top; }
th { color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: .4px; }
tr:hover td { background: #161b22; }
.bar { background: #21262d; border-radius: 6px; height: 12px; width: 120px; overflow: hidden; display: inline-block; vertical-align: middle; }
.bar > i { display: block; height: 100%; }
.bar.low > i { background: #3fb950; } .bar.mid > i { background: #d29922; } .bar.high > i { background: #f85149; }
.bar.na > i { width: 100%; background: #30363d; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.chip.ok { background: rgba(63,185,80,.15); color: #3fb950; }
.chip.fail { background: rgba(248,81,73,.15); color: #f85149; }
.chip.warn { background: rgba(210,153,34,.15); color: #d29922; }
.chip.mute { background: rgba(139,148,158,.15); color: #8b949e; }
.badge { display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 11px; font-weight: 700; margin-right: 6px; }
.badge.HIGH { background: rgba(248,81,73,.2); color: #f85149; }
.badge.MED { background: rgba(210,153,34,.2); color: #d29922; }
.badge.INFO { background: rgba(88,166,255,.18); color: #58a6ff; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
.teambox { background: #161b22; border: 1px solid #2f3336; border-radius: 10px; padding: 14px; }
.teambox h3 { margin: 0 0 6px; font-size: 15px; }
.teambox .sub { color: #8b949e; font-size: 12px; margin-bottom: 8px; }
.kan { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 8px; margin-bottom: 10px; }
.kan div { background: #0d1117; border: 1px solid #21262d; border-radius: 8px; padding: 8px 10px; }
.kan .n { font-size: 18px; font-weight: 700; }
.kan .l { font-size: 11px; color: #8b949e; }
.mesh-wrap { background: #0d1117; border-radius: 8px; padding: 6px; }
.mesh-svg { width: 100%; height: auto; min-height: 220px; }
.mesh-svg g.node { cursor: pointer; }
.mesh-svg g.node circle { fill: #1f6feb; stroke: #58a6ff; stroke-width: 1.5; transition: fill .12s, filter .12s; }
.mesh-svg g.node text { fill: #e7e9ea; font-size: 12px; text-anchor: middle; pointer-events: none; }
.mesh-svg g.node:hover circle, .mesh-svg g.node.active circle { fill: #d29922; filter: drop-shadow(0 0 4px rgba(210,153,34,.8)); }
.mesh-svg line.edge { stroke: #30363d; stroke-width: 1.6; transition: stroke .12s, stroke-width .12s; }
.mesh-svg line.edge.active { stroke: #d29922; stroke-width: 3; }
.mesh-svg .edge-label { fill: #8b949e; font-size: 10px; }
.empty { color: #8b949e; font-style: italic; font-size: 13px; }
footer { color: #484f58; font-size: 12px; padding: 10px 28px 24px; max-width: 1200px; margin: 0 auto; }
"""

_JS = """
(function () {
  function wire(svg) {
    svg.addEventListener('click', function (e) {
      var node = e.target.closest('g.node');
      if (!node) {
        svg.querySelectorAll('g.node').forEach(function (n) { n.classList.remove('active'); });
        svg.querySelectorAll('line.edge').forEach(function (l) { l.classList.remove('active'); });
        return;
      }
      var name = node.getAttribute('data-name');
      var active = node.classList.toggle('active');
      svg.querySelectorAll('line.edge').forEach(function (l) {
        l.classList.toggle('active', active && (l.getAttribute('data-from') === name || l.getAttribute('data-to') === name));
      });
    });
  }
  document.querySelectorAll('.mesh-svg').forEach(wire);
})();
"""


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _mem_class(pct: float | None) -> str:
    if pct is None:
        return "na"
    if pct > 85:
        return "high"
    if pct >= 60:
        return "mid"
    return "low"


def _mem_bar(pct: float | None) -> str:
    if pct is None:
        return '<span class="bar na"><i style="width:100%"></i></span> n/a'
    cls = _mem_class(pct)
    return f'<span class="bar {cls}"><i style="width:{min(pct, 100):.1f}%"></i></span> {pct:.1f}%'


def _job_status_chip(job: dict) -> str:
    status = job.get("status")
    enabled = job.get("enabled", True)
    if not enabled:
        return '<span class="chip mute">disabled</span>'
    if status and str(status).lower() not in ("ok", "completed", "success"):
        return f'<span class="chip fail">{_esc(status)}</span>'
    return '<span class="chip ok">ok</span>'


def render_mesh_svg(team: str, entry: dict) -> str:
    """Static circle-layout SVG; interactivity (click-to-highlight) is added by JS."""
    mesh = entry.get("mesh") or {}
    edges = mesh.get("edges") or []
    if mesh.get("parse_error") or not edges:
        return '<div class="empty">no mesh edges</div>'
    nodes: list[str] = []
    for e in edges:
        for k in ("from", "to"):
            v = e.get(k)
            if v and v not in nodes:
                nodes.append(str(v))
    n = len(nodes)
    r = max(70.0, min(140.0, 34.0 * n))
    pad = 90.0
    w = 2 * (r + pad)
    h = 2 * (r + pad)
    cx = cy = r + pad
    angle = 0.0 if n == 1 else 360.0 / n
    pos: dict[str, tuple[float, float]] = {}
    for i, name in enumerate(nodes):
        rad = (angle * i - 90.0) * 3.14159265 / 180.0
        pos[name] = (cx + r * _cos(rad), cy + r * _sin(rad))
    marker_id = f"arrow-{team}"
    parts = [
        f'<svg class="mesh-svg" viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-label="mesh graph for {_esc(team)}">',
        f'<defs><marker id="{_esc(marker_id)}" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">'
        f'<path d="M0,0 L0,6 L7,3 z" fill="#58a6ff"/></marker></defs>',
    ]
    for e in edges:
        f, t = str(e.get("from")), str(e.get("to"))
        if f not in pos or t not in pos:
            continue
        x1, y1 = pos[f]
        x2, y2 = pos[t]
        via = _esc(e.get("via") or "")
        parts.append(
            f'<line class="edge" data-from="{_esc(f)}" data-to="{_esc(t)}" '
            f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" marker-end="url(#{_esc(marker_id)})">'
            f'<title>{via}</title></line>'
        )
    for name in nodes:
        x, y = pos[name]
        label_dy = 24 if y <= cy else -4  # below the circle on the bottom half
        parts.append(
            f'<g class="node" data-name="{_esc(name)}" transform="translate({x:.1f},{y:.1f})">'
            f'<circle r="14"/><text y="{label_dy}">{_esc(name)}</text></g>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _cos(rad: float) -> float:
    import math
    return math.cos(rad)


def _sin(rad: float) -> float:
    import math
    return math.sin(rad)


def collect(home: Path, team_filter: str | None = None) -> dict:
    """Fleet + cron-health snapshot for the dashboard (degrading)."""
    data = fleet.fleet_report(home, team_filter=team_filter)
    try:
        from . import cron911
        data["cron"] = cron911.scan(home)
    except Exception:  # noqa: BLE001 — degrade: dashboard stays usable
        data["cron"] = None
    return data


def render_html(data: dict, title: str = "TeamOps Fleet Dashboard") -> str:
    """Render the complete standalone HTML document."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    home = _esc(data.get("home", ""))
    profiles = data.get("profiles", [])
    teams = data.get("teams", {})
    cron = data.get("cron")

    prof_24h = sum((p.get("executions_24h") or {}).get("total", 0) for p in profiles)
    prof_fail = sum((p.get("executions_24h") or {}).get("failed", 0) for p in profiles)
    home_exec = data.get("home_executions_24h") or {}
    total_exec = prof_24h + home_exec.get("total", 0)
    total_fail = prof_fail + home_exec.get("failed", 0)

    issues = (cron or {}).get("issues", []) if cron else None
    dead_models = 0
    if issues is not None:
        for i in issues:
            if "DEAD MODEL" in (i.get("detail") or ""):
                dead_models += 1
    info = (cron or {}).get("info", []) if cron else []

    cards = [
        ("card info", str(data.get("profile_count", 0)), "profiles"),
        ("card info", str(data.get("home_crons", 0)), "home crons"),
        ("card ok", str(total_exec), "executions (24h)"),
        ("card warn" if total_fail else "card ok", str(total_fail), "failed (24h)"),
        ("card warn" if dead_models else "card ok", str(dead_models), "dead models"),
    ]
    if issues is not None:
        cards.append(("card warn" if len(issues) else "card ok", str(len(issues)), "cron flags"))

    cards_html = "\n".join(
        f'<div class="card {cls}"><div class="num">{_esc(n)}</div><div class="lbl">{_esc(l)}</div></div>'
        for cls, n, l in cards
    )

    # ---- profiles table
    rows = []
    for p in profiles:
        mem = p.get("memory") or {}
        execs = p.get("executions_24h") or {}
        js = "".join(
            f"<li>{_esc(j.get('name'))} {_job_status_chip(j)}</li>"
            for j in (p.get("jobs") or [])
        )
        rows.append(
            f"<tr><td><strong>{_esc(p['name'])}</strong>"
            f"{' <span class=chip.ok>SOUL</span>' if p.get('has_soul') else ''}</td>"
            f"<td>{_mem_bar(mem.get('memory_pct'))}</td>"
            f"<td>{_esc(mem.get('skills', 0))} skill(s)</td>"
            f"<td>{_esc(p.get('crons', 0))}</td>"
            f"<td>{_esc(execs.get('total', 0))} ({_esc(execs.get('ok', 0))} ok / "
            f"<span style='color:#f85149'>{_esc(execs.get('failed', 0))} fail</span>)</td>"
            f"<td>{js or '<span class=empty>no crons</span>'}</td></tr>"
        )
    profiles_html = (
        "<table><tr><th>Profile</th><th>Memory</th><th>Skills</th><th>Crons</th>"
        "<th>Executions 24h</th><th>Jobs</th></tr>" + "".join(rows) + "</table>"
        if rows else '<div class="empty">no profiles deployed in this home</div>'
    )

    # ---- cron health
    if issues is None:
        cron_html = '<div class="empty">cron scan unavailable (no jobs.json / executions.db) — nothing broken</div>'
    elif not issues and not info:
        cron_html = '<div class="ok" style="color:#3fb950">No cron issues detected.</div>'
    else:
        rows = []
        for i in issues:
            rows.append(
                f"<tr><td><span class=\"badge {_esc(i.get('severity'))}\">{_esc(i.get('severity'))}</span></td>"
                f"<td>{_esc(i.get('scope'))}</td><td>{_esc(i.get('job'))}</td>"
                f"<td>{_esc(i.get('detail'))}</td></tr>"
            )
        if info:
            for i in info:
                rows.append(
                    f"<tr><td><span class=\"badge INFO\">INFO</span></td><td>{_esc(i.get('scope'))}</td>"
                    f"<td>{_esc(i.get('job'))}</td><td>{_esc(i.get('detail'))}</td></tr>"
                )
        cron_html = ("<table><tr><th>Severity</th><th>Scope</th><th>Job</th><th>Detail</th></tr>"
                     + "".join(rows) + "</table>")

    # ---- teams / mesh / kanban
    if not teams:
        teams_html = '<div class="empty">no teams / mesh yet — deploy a blueprint to build one</div>'
    else:
        boxes = []
        for tname, t in teams.items():
            kan = t.get("kanban") or {}
            kanc = ""
            if kan.get("parse_error"):
                kanc = '<div class="empty">kanban parse error</div>'
            elif kan:
                done = kan.get("done", 0)
                total = kan.get("tasks", 0)
                kanc = (
                    '<div class="kan">'
                    f'<div><div class="n">{_esc(total)}</div><div class="l">tasks</div></div>'
                    f'<div><div class="n">{_esc(done)}</div><div class="l">done</div></div>'
                    f'<div><div class="n">{_esc(total - done)}</div><div class="l">open</div></div>'
                    "</div>"
                )
            else:
                kanc = '<div class="empty">no kanban</div>'
            mesh_html = render_mesh_svg(tname, t)
            mm = t.get("mesh_map")
            sub = f"mesh edges: {len((t.get('mesh') or {}).get('edges') or [])}"
            if mm:
                sub += f" · MESH-MAP.md: {_esc(mm.get('lines', 0))} lines"
            boxes.append(
                f'<div class="teambox"><h3>{_esc(tname)}</h3><div class="sub">{sub}</div>'
                f"{kanc}<div class='mesh-wrap'>{mesh_html}</div></div>"
            )
        teams_html = '<div class="grid2">' + "".join(boxes) + "</div>"

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — {home}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <h1>{_esc(title)}</h1>
  <div class="meta">HERMES_HOME: {home} · generated {now}</div>
</header>
<main>
  <section><div class="cards">{cards_html}</div></section>
  <section id="profiles"><h2>Profiles</h2>{profiles_html}</section>
  <section id="cron-health"><h2>Cron health</h2>{cron_html}</section>
  <section id="teams"><h2>Teams &amp; mesh</h2>{teams_html}</section>
</main>
<footer>Generated by TeamOps — <code>hermes teams status --html</code>. Click a mesh node to highlight its edges.</footer>
<script>{_JS}</script>
</body>
</html>
"""
    return doc.strip() + "\n"


def write_dashboard(home: Path, team_filter: str | None = None,
                    out_path: Path | None = None) -> dict:
    """Write the standalone dashboard HTML; return its path + summary."""
    home = Path(home)
    data = collect(home, team_filter=team_filter)
    if out_path is None:
        fname = f"teamops-dashboard-{team_filter}.html" if team_filter else "teamops-dashboard.html"
        out_path = home / fname
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    title = f"TeamOps Fleet Dashboard{f' — {team_filter}' if team_filter else ''}"
    text = render_html(data, title=title)
    out_path.write_text(text, encoding="utf-8")
    cron = data.get("cron") or {}
    return {
        "html_path": str(out_path.resolve()),
        "bytes": len(text.encode("utf-8")),
        "home": str(home),
        "profiles": data.get("profile_count", 0),
        "teams": list(data.get("teams", {}).keys()),
        "cron_issues": len(cron.get("issues", []) or []),
        "executions_24h": (data.get("home_executions_24h") or {}).get("total", 0),
        "title": title,
    }

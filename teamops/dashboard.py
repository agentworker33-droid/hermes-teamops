"""Self-contained HTML fleet dashboard (Task 2.8).

``team_status(html=True)`` / ``hermes teams status --html`` render a single
standalone .html file — no external CDN, no JS library downloads — with:

- a left rail (Mnemosyne-console style) + one view at a time (hash tabs)
- summary cards (profiles, home crons, 24h executions, cron-health flags)
- ephemeral team cards (one-shot scripts, RUN buttons) + deployed teams
- a hub-and-spoke team diagram styled after the AutomatedAgents website's
  "watch the team become the logo" morph (white rays, red traveling pulses,
  legs that draw in to form the AA mark)
- per-profile memory footprint, profiles table, cron health table
- action buttons: in the Hermes app they send prompts via data-hermes-send;
  in a plain browser they show a toast with the exact prompt (copyable)

All reads are HERMES_HOME-relative. Every optional data source degrades
gracefully: if Cron911 (or anything else) raises, that section just renders
"unavailable" — no nags, no broken UI (acceptance rule).
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from . import fleet, runs

# ---------------------------------------------------------------------------
# Theme bridging: match the user's chosen Hermes dashboard theme (config key
# ``dashboard.theme`` — built-ins below, or a user theme in
# ``<home>/dashboard-themes/*.yaml``) by projecting its 3-slot palette onto
# TeamOps' semantic CSS variables. Falls back to the TeamOps look (None) when
# the setting is missing or unknown — the dashboard never breaks on a theme.
# ---------------------------------------------------------------------------


def _hex_to_rgb(value: object):
    import re
    m = re.match(r"^#?([0-9a-fA-F]{6})$", str(value).strip())
    if not m:
        return None
    h = m.group(1)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*[max(0, min(255, int(v))) for v in rgb])


def _mix(a: str, b: str, t: float) -> str:
    """Blend hex a toward hex b by t (0..1). Both must be #rrggbb."""
    ra, ga, ba = _hex_to_rgb(a) or (255, 255, 255)
    rb, gb, bb = _hex_to_rgb(b) or (0, 0, 0)
    return _rgb_to_hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def _rgba_base(value: str, fallback: str = "#ff022b") -> str:
    """Pull the base colour out of an ``rgba(r,g,b,a)`` string (or hex)."""
    import re
    m = re.match(r"rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)", str(value).strip())
    if m:
        return _rgb_to_hex((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    if _hex_to_rgb(value):
        return value
    return fallback


def _luminance(hex_color: str) -> float:
    rgb = _hex_to_rgb(hex_color) or (0, 0, 0)
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255


# Built-in Hermes dashboard themes (names match ``dashboard.theme`` in the web
# server; palettes come from web/src/themes/presets.ts + apps/shared
# theme-presets.ts). ``mid`` is the dashboard "midground": text + primary fill.
_HERMES_BUILTIN_THEMES: dict[str, dict] = {
    "default": {
        "label": "Hermes Teal", "bg": "#041c1c", "mid": "#ffe6cb",
        "fg": "#ffffff", "glow": "rgba(255, 189, 56, 0.35)",
    },
    "default-large": {
        "label": "Hermes Teal (Large)", "bg": "#041c1c", "mid": "#ffe6cb",
        "fg": "#ffffff", "glow": "rgba(255, 189, 56, 0.35)",
    },
    "nous-blue": {
        "label": "Nous Blue", "bg": "#e8f2fd", "mid": "#0053fd",
        "fg": "#170d02", "glow": "rgba(0, 83, 253, 0.12)",
    },
    "midnight": {
        "label": "Midnight", "bg": "#08081c", "mid": "#ddd6ff",
        "fg": "#ffffff", "glow": "rgba(139, 128, 232, 0.3)",
    },
    "ember": {
        "label": "Ember", "bg": "#160800", "mid": "#d97316",
        "fg": "#ffffff", "glow": "rgba(217, 115, 22, 0.3)",
        "overrides": {"warning": "#f97316", "destructive": "#c92d0f"},
    },
    "mono": {
        "label": "Mono", "bg": "#0e0e0e", "mid": "#9a9a9a",
        "fg": "#eaeaea", "glow": "rgba(154, 154, 154, 0.3)",
    },
    "cyberpunk": {
        "label": "Cyberpunk", "bg": "#000a00", "mid": "#00ff41",
        "fg": "#ffffff", "glow": "rgba(0, 255, 65, 0.3)",
        "overrides": {"success": "#00ff88", "warning": "#ffd700", "destructive": "#ff0055"},
    },
    "rose": {
        "label": "Rosé", "bg": "#1a0f15", "mid": "#ffd4e1",
        "fg": "#ffffff", "glow": "rgba(249, 168, 212, 0.3)",
    },
}


def _read_config_theme(home: Path) -> str | None:
    """Read ``dashboard.theme`` from config.yaml without importing Hermes."""
    cfg = home / "config.yaml"
    if not cfg.exists():
        return None
    import re
    text = cfg.read_text(encoding="utf-8", errors="ignore")
    # locate the dashboard: section, then its theme: line (yaml-safe enough)
    m = re.search(r"^dashboard:\s*(?:#.*)?$", text, re.MULTILINE)
    if not m:
        return None
    body = text[m.end():]
    body = re.split(r"^\S[^:\n]*:", body, flags=re.MULTILINE)[0]
    t = re.search(r"^\s*theme:\s*['\"]?([^#\n'\"]+)", body, re.MULTILINE)
    if not t:
        return None
    name = t.group(1).strip()
    return name or None


def _load_user_theme(home: Path, name: str) -> dict | None:
    """A user theme from ``<home>/dashboard-themes/<name>.yaml`` (normalised)."""
    try:
        import yaml  # pyyaml/hermes_yaml present in Hermes homes
    except Exception:  # noqa: BLE001
        return None
    themes_dir = home / "dashboard-themes"
    if not themes_dir.is_dir():
        return None
    for f in sorted(themes_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict) or data.get("name") != name:
            continue
        pal = data.get("palette") or data.get("colors") or {}
        bg = pal.get("background") or {}
        mid = pal.get("midground") or {}
        fg = pal.get("foreground") or {}
        typo = data.get("typography") or {}
        ov = data.get("colorOverrides") or {}
        return {
            "label": data.get("label") or name,
            "bg": _hex_to_rgb(bg.get("hex")) and bg["hex"] or "#141414",
            "mid": _hex_to_rgb(mid.get("hex")) and mid["hex"] or "#eaeaea",
            "fg": _hex_to_rgb(fg.get("hex")) and fg["hex"] or "#ffffff",
            "glow": pal.get("warmGlow") or "rgba(255,255,255,.25)",
            "fontMono": typo.get("fontMono") or "",
            "overrides": {k: v for k, v in ov.items()
                          if k in ("success", "warning", "destructive") and isinstance(v, str)},
        }
    return None


def load_hermes_theme(home: Path) -> dict | None:
    """Resolve the active Hermes dashboard theme to a TeamOps palette dict.

    Returns None when unset/unknown so the dashboard keeps its own look.
    """
    home = Path(home)
    name = _read_config_theme(home)
    if not name:
        return None
    if name in _HERMES_BUILTIN_THEMES:
        theme = dict(_HERMES_BUILTIN_THEMES[name])
        theme["name"] = name
        return theme
    user = _load_user_theme(home, name)
    if user is not None:
        user["name"] = name
        return user
    return None


def _theme_root_css(theme: dict | None) -> str:
    """Project a TeamOps palette dict onto :root overrides; '' when none."""
    if not theme:
        return ""
    bg = theme.get("bg") or "#050505"
    mid = theme.get("mid") or "#f4f4f5"
    fg = theme.get("fg") or "#ffffff"
    glow = theme.get("glow") or "rgba(255,255,255,.22)"
    dark = _luminance(bg) < 0.5
    accent = _rgba_base(glow, mid if not dark else "#ff022b")
    if dark:
        panel = _mix(bg, "#ffffff", 0.045)
        panel2 = _mix(bg, "#ffffff", 0.1)
        line = _mix(bg, "#ffffff", 0.15)
        line2 = _mix(bg, "#ffffff", 0.24)
        hover = "rgba(255,255,255,.045)"
        hover2 = "rgba(255,255,255,.03)"
        chip_bg = _mix(bg, "#ffffff", 0.04)
        chip_line = _mix(bg, "#ffffff", 0.18)
        gray = _mix(mid, "#ffffff", 0.25)
        mut = _mix(mid, bg, 0.35)
        dim = _mix(mid, bg, 0.62)
        soft = _mix(bg, "#ffffff", 0.3)
        bar_bg = _mix(bg, "#ffffff", 0.09)
        bar_low = _mix(mid, "#ffffff", 0.15)
        bar_mid = _mix(mid, bg, 0.45)
    else:
        panel = _mix(bg, "#000000", 0.05)
        panel2 = _mix(bg, "#000000", 0.1)
        line = _mix(bg, "#000000", 0.15)
        line2 = _mix(bg, "#000000", 0.25)
        hover = "rgba(0,0,0,.05)"
        hover2 = "rgba(0,0,0,.04)"
        chip_bg = _mix(bg, "#000000", 0.06)
        chip_line = _mix(bg, "#000000", 0.25)
        gray = _mix(mid, "#000000", 0.3)
        mut = _mix(mid, bg, 0.4)
        dim = _mix(mid, bg, 0.65)
        soft = _mix(bg, "#000000", 0.28)
        bar_bg = _mix(bg, "#000000", 0.08)
        bar_low = _mix(mid, "#ffffff", 0.25)
        bar_mid = _mix(mid, bg, 0.5)
    ov = theme.get("overrides") or {}
    ok = ov.get("success", bar_low if not dark else "#8fce9f")
    warn = ov.get("warning", accent)
    err = ov.get("destructive", "#ff4d4d")
    font_mono = (theme.get("fontMono") or "")[:120]
    mono = f"{font_mono}, " if font_mono and all(c.isalnum() or c in ' -,' for c in font_mono) else ""
    return f""":root {{
  --bg: {bg}; --bg2: {_mix(bg, '#000000', 0.35) if dark else '#ffffff'};
  --panel: {panel}; --panel2: {panel2};
  --line: {line}; --line2: {line2};
  --txt: {mid}; --mut: {mut}; --dim: {dim};
  --white: {fg}; --black: {bg}; --gray: {gray};
  --red: {accent}; --accent: {accent};
  --err: {err}; --err-txt: {_mix(err, '#ffffff', 0.5)}; --err-glow: rgba(255,60,60,.18);
  --hover: {hover}; --hover2: {hover2}; --chip-bg: {chip_bg}; --chip-line: {chip_line};
  --soft: {soft}; --bar-bg: {bar_bg}; --bar-low: {bar_low}; --bar-mid: {bar_mid};
  --glow: {glow if dark else 'rgba(0,0,0,.14)'}; --glow-strong: rgba(255,255,255,.7);
  --ray: {mid}; --pulse: {_rgba_base(glow, accent)}cc; --pulse-glow: {_rgba_base(glow, accent)}b3;
  --pulse-soft: {mid}f2; --pulse-soft-glow: {mid}99;
  --danger-border: {_rgba_base(glow, accent)}73; --danger-bg: {_rgba_base(glow, accent)}14;
  --ok: {ok}; --warn: {warn};
  --mono: {mono}ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}}"""


_CSS = """
:root {
  color-scheme: dark;
  --bg: #050505; --bg2: #000; --panel: #0c0c0f; --panel2: #131316;
  --line: #1e1e22; --line2: #2f2f35;
  --txt: #f4f4f5; --mut: #a1a1aa; --dim: #6b6b74;
  --white: #fff; --black: #000; --gray: #c9c9cf; --red: #ff022b;
  --accent: #ff022b; --err: #ff4d4d; --err-txt: #ff8080; --err-glow: rgba(255,60,60,.18);
  --hover: #0d0d10; --hover2: #0c0c0f; --chip-bg: #0e0e10; --chip-line: #2a2a2a;
  --soft: #3a3a3a; --bar-bg: #1a1a1a; --bar-low: #d4d4d4; --bar-mid: #8a8a8a;
  --glow: rgba(255,255,255,.22); --glow-strong: rgba(255,255,255,.7);
  --ray: #f2f3f5; --pulse: rgba(255,2,43,.9); --pulse-glow: rgba(255,2,43,.8);
  --pulse-soft: rgba(255,255,255,.95); --pulse-soft-glow: rgba(255,255,255,.6);
  --danger-border: rgba(255,2,43,.45); --danger-bg: rgba(255,2,43,.08);
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg); color: var(--txt); line-height: 1.5; font-size: 14.5px;
}
body::after {  /* scanlines */
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 999;
  background: repeating-linear-gradient(0deg, rgba(255,255,255,.014) 0 1px, transparent 1px 3px);
}

/* ---------- rail ---------- */
.rail {
  position: fixed; left: 0; top: 0; bottom: 0; width: 236px; z-index: 60;
  background: var(--bg2); border-right: 1px solid var(--line);
  padding: 22px 14px 16px; display: flex; flex-direction: column; overflow: hidden;
}
.rail .brand { display: flex; gap: 11px; align-items: center; padding: 0 6px; margin-bottom: 26px; }
.rail .brand b { display: block; font-family: var(--mono); font-size: 14px; letter-spacing: .4px; }
.rail .brand span { display: block; font-family: var(--mono); font-size: 9px; letter-spacing: 2.2px; color: var(--mut); text-transform: uppercase; margin-top: 2px; }
.rail .nav { display: flex; flex-direction: column; gap: 3px; }
.rail .nav a {
  display: flex; align-items: center; gap: 11px; padding: 9px 10px; border-radius: 9px;
  color: var(--mut); text-decoration: none; font-family: var(--mono); font-size: 13px;
  border: 1px solid transparent;
}
.rail .nav a .sig {
  width: 27px; height: 27px; border: 1px solid var(--line2); border-radius: 8px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  font-size: 10px; letter-spacing: .5px; color: var(--gray); background: var(--panel);
}
.rail .nav a:hover { color: var(--txt); background: var(--hover); }
.rail .nav a.on { background: var(--panel2); color: var(--white); border-color: var(--line); }
.rail .nav a.on .sig { background: var(--white); color: var(--black); border-color: var(--white); }
.rail .foot {
  margin-top: auto; font-family: var(--mono); font-size: 10px; color: var(--dim);
  line-height: 1.8; border-top: 1px solid var(--line); padding-top: 12px; word-break: break-all;
}
.rail .foot b { color: var(--mut); font-weight: 600; }

/* ---------- main canvas ---------- */
.main { margin-left: 236px; padding: 36px 40px 70px; max-width: 1280px; }
.view { display: none; }
.view.on { display: block; animation: fade .16s ease; }
@keyframes fade { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }
.pagehead { margin-bottom: 26px; }
.pagehead .eyebrow {
  font-family: var(--mono); font-size: 10px; letter-spacing: 2.6px; color: var(--mut);
  text-transform: uppercase; margin-bottom: 9px;
}
.pagehead .eyebrow .live { color: var(--red); }
.pagehead h1 {
  font-size: 27px; font-family: var(--mono); font-weight: 700; letter-spacing: .3px;
  margin: 0 0 8px; text-shadow: 0 0 16px var(--glow);
}
.pagehead p { color: var(--mut); font-size: 14px; max-width: 74ch; margin: 0; }

/* ---------- cards / kpi ---------- */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.card {
  background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--line2);
  border-radius: 9px; padding: 15px 16px;
}
.card .num { font-size: 30px; font-weight: 700; font-family: var(--mono); line-height: 1.05; }
.card .lbl { font-size: 10.5px; color: var(--mut); text-transform: uppercase; letter-spacing: 1.2px; margin-top: 3px; font-family: var(--mono); }
.card.warn { border-left-color: var(--white); } .card.warn .num { color: var(--white); }
.card.ok { border-left-color: var(--gray); } .card.ok .num { color: var(--gray); }
.card.info { border-left-color: var(--line2); } .card.info .num { color: var(--white); }
.quickacts { display: grid; grid-template-columns: repeat(auto-fit, minmax(205px, 1fr)); gap: 10px; margin: 24px 0 6px; }

/* ---------- sections ---------- */
section.sec { margin-bottom: 30px; }
.group {
  margin: 24px 0 12px; display: flex; align-items: baseline; gap: 10px;
  font-family: var(--mono); font-size: 11px; text-transform: uppercase;
  letter-spacing: 1.8px; color: var(--gray);
}
.group::before { content: "// "; color: var(--dim); }
.group .n { color: var(--dim); }

/* ---------- pills / chips / badges ---------- */
.pill { display: inline-block; padding: 3px 11px; border-radius: 999px;
  font-family: var(--mono); font-size: 10.5px; font-weight: 700; letter-spacing: 1px; white-space: nowrap; }
.pill.run { background: var(--white); color: var(--black); }
.pill.ready { background: transparent; color: var(--gray); border: 1px solid var(--line2); }
.pill.actv { background: var(--panel2); color: var(--white); border: 1px solid var(--line2); }
.pill.idle { background: transparent; color: var(--dim); border: 1px dashed var(--soft); }
.chip {
  display: inline-block; padding: 2px 9px; border-radius: 5px; font-size: 11px; font-weight: 600;
  font-family: var(--mono); border: 1px solid transparent; letter-spacing: .3px; line-height: 1.6;
}
.chip.ok { background: var(--bg2); color: var(--gray); border-color: var(--soft); }
.chip.fail { background: var(--white); color: var(--black); border-color: var(--white); }
.chip.warn { background: var(--panel2); color: var(--white); border-color: var(--line2); }
.chip.mute { background: transparent; color: var(--dim); border-color: var(--line); }
.badge {
  display: inline-block; padding: 2px 9px; border-radius: 5px; font-size: 11px; font-weight: 700;
  font-family: var(--mono); letter-spacing: .8px; border: 1px solid currentColor; margin-right: 6px;
}
.badge.HIGH { background: var(--white); color: var(--black); border-color: var(--white); }
.badge.MED { background: var(--panel2); color: var(--white); border-color: var(--line2); }
.badge.INFO { background: transparent; color: var(--gray); border-color: var(--line2); }

/* ---------- tables ---------- */
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th {
  color: var(--mut); font-size: 10.5px; text-transform: uppercase; letter-spacing: 1.4px;
  font-family: var(--mono); border-bottom-color: var(--line2);
}
tr:hover td { background: var(--hover); }
.bar { background: var(--bar-bg); border-radius: 3px; height: 12px; width: 120px; overflow: hidden; display: inline-block; vertical-align: middle; }
.bar > i { display: block; height: 100%; }
.bar.low > i { background: var(--bar-low); } .bar.mid > i { background: var(--bar-mid); } .bar.high > i { background: var(--white); box-shadow: 0 0 6px var(--glow-strong); }
.bar.na > i { width: 100%; background: var(--line2); }
li.more { list-style: none; color: var(--dim); font-size: 11px; font-family: var(--mono); }

/* ---------- team cards ---------- */
.filters { display: flex; gap: 8px; flex-wrap: wrap; margin: -2px 0 12px; }
.fchip {
  cursor: pointer; font-family: var(--mono); font-size: 11.5px; letter-spacing: .6px;
  padding: 5px 13px; border: 1px solid var(--line2); border-radius: 999px;
  background: var(--bg); color: var(--mut); user-select: none;
}
.fchip:hover { color: var(--txt); border-color: var(--line2); }
.fchip.on { background: var(--white); color: var(--black); border-color: var(--white); }
.grid2 { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 14px; }
.teambox {
  background: var(--panel); border: 1px solid var(--line); border-top: 2px solid var(--gray);
  border-radius: 10px; padding: 15px 16px;
}
.teambox.is-group { border-top-color: var(--gray); }
.tb-head { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
.teambox h3 { margin: 0; font-size: 14.5px; font-family: var(--mono); letter-spacing: .4px; font-weight: 700; }
.tb-head h3::before { content: "// "; color: var(--dim); font-weight: 400; }
.tb-head .spacer { flex: 1; }
.tb-badge {
  font-family: var(--mono); font-size: 9px; letter-spacing: 1px; color: var(--mut);
  border: 1px solid var(--line2); padding: 1px 7px; border-radius: 999px; white-space: nowrap;
}
.teambox .sub { color: var(--mut); font-size: 12.5px; margin-bottom: 9px; font-family: var(--mono); }
.desc { color: var(--mut); font-size: 13px; margin: 0 0 4px; max-width: 58ch; }
.members { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 10px; }
.mem-chip {
  font-family: var(--mono); font-size: 11px; color: var(--gray);
  border: 1px solid var(--chip-line); background: var(--chip-bg); padding: 3px 8px; border-radius: 5px;
}
.mem-chip.soul::after { content: "◈"; margin-left: 4px; color: var(--dim); font-size: 9px; }
.kan { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 8px; margin-bottom: 10px; }
.kan div { background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; }
.kan .n { font-size: 17px; font-weight: 700; font-family: var(--mono); color: var(--white); }
.kan .l { font-size: 10px; color: var(--mut); text-transform: uppercase; letter-spacing: .8px; font-family: var(--mono); }
.acts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
.act {
  font-family: var(--mono); font-size: 12px; letter-spacing: .9px;
  padding: 9px 16px; background: var(--bg); color: var(--white);
  border: 1px solid var(--line2); border-radius: 7px; cursor: pointer; min-height: 37px;
}
.act:hover { border-color: var(--white); }
.act.primary { background: var(--white); color: var(--black); border-color: var(--white); font-weight: 700; }
.act.primary:hover { background: var(--gray); }

/* ---------- launch ---------- */
.launchbox { background: var(--panel); border: 1px solid var(--line); border-top: 2px solid var(--gray);
  border-radius: 10px; padding: 15px 16px; display: flex; flex-direction: column; }
.launchbox .hd { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
.launchbox h3 { margin: 0; font-family: var(--mono); font-size: 14px; letter-spacing: .4px; }
.launchbox .roles { color: var(--dim); font-size: 11.5px; font-family: var(--mono); margin-bottom: 8px; }
.launchbox .desc { margin-bottom: 12px; }
.launchbox .acts { margin-top: auto; }
.launchbox.factory { border-top-color: var(--white); }
.tpl-row { display: flex; flex-wrap: wrap; gap: 9px; }
.tpl-chip { font-family: var(--mono); font-size: 11.5px; letter-spacing: .7px; color: var(--mut);
  border: 1px solid var(--line2); background: var(--bg); padding: 8px 15px; border-radius: 999px; cursor: pointer; }
.tpl-chip:hover { color: var(--white); border-color: var(--gray); }

/* ---------- memory ---------- */
.mem-wrap { display: grid; grid-template-columns: repeat(auto-fill, minmax(270px, 1fr)); gap: 11px; }
.membox { background: var(--panel); border: 1px solid var(--line); border-radius: 9px; padding: 13px 15px; }
.membox h4 { margin: 0 0 9px; font-family: var(--mono); font-size: 12.5px; letter-spacing: .4px; }
.memrow { display: flex; gap: 10px; align-items: center; font-family: var(--mono);
  font-size: 11px; color: var(--mut); margin-bottom: 7px; }
.memrow .mkey { width: 30px; letter-spacing: .8px; }
.memrow .mbar { flex: 1; height: 8px; background: var(--bar-bg); border-radius: 3px; overflow: hidden; }
.memrow .mbar > i { display: block; height: 100%; background: var(--white); }
.memrow .mval { width: 52px; text-align: right; }

/* ---------- team diagram (hub-and-spoke, pulses = handoffs) ---------- */
.mesh-wrap { background: var(--bg2); border: 1px solid var(--line); border-radius: 12px; padding: 8px;
  transition: transform 1.2s cubic-bezier(.2,.7,.3,1); position: relative; }
.mesh-svg { width: 100%; height: auto; display: block; }
.mesh-svg .ray { fill: none; stroke: var(--ray); stroke-width: 26; stroke-linecap: round; }
.mesh-svg .pulse {
  fill: none; stroke: var(--pulse); stroke-width: 8; stroke-linecap: round;
  filter: drop-shadow(0 0 7px var(--pulse-glow));
  stroke-dasharray: 26 300; opacity: .85; animation: tpulse 4.5s linear infinite;
}
.mesh-svg .pulse.pwh {
  stroke: var(--pulse-soft); filter: drop-shadow(0 0 6px var(--pulse-soft-glow));
  stroke-dasharray: 18 300; animation-duration: 7s;
}
@keyframes tpulse { to { stroke-dashoffset: -326; } }
.mesh-svg g.node { cursor: pointer; }
.mesh-svg g.node circle {
  fill: var(--white); transform-origin: center; transform-box: fill-box;
  animation: breath 3.2s ease-in-out infinite;
  filter: drop-shadow(0 0 10px rgba(255,255,255,.55));
  transition: fill .15s, opacity .4s;
}
.mesh-svg g.node.hub circle { filter: drop-shadow(0 0 20px var(--glow-strong)); }
@keyframes breath { 0%,100% { transform: scale(1); } 50% { transform: scale(1.12); } }
.mesh-svg g.node:hover circle, .mesh-svg g.node.active circle { fill: var(--accent); filter: drop-shadow(0 0 14px var(--pulse-glow)); }
.mesh-svg .nlabel rect { fill: rgba(0,0,0,.74); stroke: var(--line2); stroke-width: 1; rx: 999; }
.mesh-svg .nlabel text { fill: var(--white); font-size: 13px; font-family: var(--mono); font-weight: 600; text-anchor: middle; }

/* ---------- toast (browser fallback for action buttons) ---------- */
.toast {
  position: fixed; right: 18px; bottom: 18px; width: min(420px, calc(100vw - 36px)); z-index: 200;
  background: var(--panel); border: 1px solid var(--line2); border-left: 3px solid var(--white);
  border-radius: 10px; padding: 14px 16px; font-family: var(--mono); font-size: 11.5px;
  box-shadow: 0 8px 30px rgba(0,0,0,.6); display: none;
}
.toast.on { display: block; animation: fade .18s ease; }
.toast b { color: var(--white); }
.toast .msg { color: var(--gray); font-size: 12px; line-height: 1.5; margin: 8px 0 0; white-space: pre-wrap;
  max-height: 220px; overflow: auto; }
.toast.err { border-color: var(--err); box-shadow: 0 0 18px var(--err-glow); }
.toast.err b { color: var(--err-txt); }
.toast .cmd { color: var(--gray); background: #000; border: 1px solid var(--line); border-radius: 6px;
  padding: 8px 10px; margin: 9px 0; word-break: break-all; }
.toast .row { display: flex; gap: 8px; align-items: center; }
.toast button { font-family: var(--mono); font-size: 11px; padding: 6px 12px; border-radius: 6px;
  border: 1px solid var(--line2); background: var(--bg); color: var(--white); cursor: pointer; }
.toast button:hover { border-color: var(--white); }

footer.line { color: var(--dim); font-size: 11px; padding-top: 18px; margin-top: 8px;
  font-family: var(--mono); border-top: 1px solid var(--line); }
footer.line a { color: var(--dim); }
.empty { color: var(--mut); font-style: italic; font-size: 13px; font-family: var(--mono); }

/* ---------- teams room: picker + one diagram at a time ---------- */
.team-room { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: 18px; align-items: start; }
.team-list { display: flex; flex-direction: column; gap: 3px; }
.team-list .group { margin: 14px 0 8px; }
.team-btn {
  display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
  font-family: var(--mono); font-size: 13px; color: var(--mut);
  background: transparent; border: 1px solid transparent; border-radius: 9px;
  padding: 10px 12px; cursor: pointer; min-height: 40px;
}
.team-btn .tname { flex: 1; letter-spacing: .2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.team-btn:hover { color: var(--txt); background: var(--hover2); }
.team-btn.on { color: var(--white); background: var(--panel2); border-color: var(--line); }
.diagram-stage { min-width: 0; }
.diagram-pane { display: none; }
.diagram-pane.on { display: block; }
.pane-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; flex-wrap: wrap; }
.pane-head .spacer { flex: 1; }
.pane-head h2 { margin: 0; font-family: var(--mono); font-size: 17px; letter-spacing: .3px; }
.pane-head h2::before { content: "// "; color: var(--dim); font-weight: 400; }
.pane-sub { color: var(--mut); font-family: var(--mono); font-size: 12px; margin-bottom: 12px; }
.hf {
  display: inline-block; margin-left: 5px; padding: 0 5px; border-radius: 5px;
  font-family: var(--mono); font-size: 10px; color: var(--accent);
  border: 1px solid var(--danger-border); background: var(--danger-bg); cursor: pointer;
}
.hf:hover { color: var(--white); background: var(--accent); border-color: var(--accent); }
.boom .pulse { animation-duration: 1.1s; }
.boom .ray { stroke: var(--red); transition: stroke .3s; }
.legend { font-family: var(--mono); font-size: 10px; color: var(--dim); margin: 6px 0 0; text-align: center; }
.crondiv { margin-top: 14px; }
.crondiv-h { font-family: var(--mono); font-size: 10.5px; letter-spacing: 1.4px; color: var(--mut);
  text-transform: uppercase; margin-bottom: 6px; }
.crondiv table { font-size: 12.5px; }
.crondiv td.mono { font-family: var(--mono); font-size: 11.5px; color: var(--mut); white-space: nowrap; }
.crondiv td.lane-group { font-family: var(--mono); font-size: 10px; letter-spacing: 1.6px;
  color: var(--gray); text-transform: uppercase; padding-top: 10px; }
"""

_RUN_CSS = """
/* ---------- runs / workflow view ---------- */
.runs-layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 16px; align-items: start; }
.runs-list { display: flex; flex-direction: column; gap: 3px; }
.run-row {
  display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
  font-family: var(--mono); font-size: 12.5px; color: var(--mut);
  background: transparent; border: 1px solid transparent; border-radius: 9px;
  padding: 9px 11px; cursor: pointer; min-height: 40px;
}
.run-row:hover { color: var(--txt); background: var(--hover2); }
.run-row.on { color: var(--white); background: var(--panel2); border-color: var(--line); }
.run-row .rt { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-row .rc { color: var(--dim); font-size: 11px; white-space: nowrap; }
.run-row .ra { color: var(--dim); font-size: 10.5px; white-space: nowrap; }
.run-row .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: var(--mut); }
.run-row .dot.s-running { background: var(--accent); box-shadow: 0 0 8px var(--pulse-glow); animation: rpulse 1.6s ease-in-out infinite; }
.run-row .dot.s-failed { background: var(--err); }
.run-row .dot.s-done { background: var(--ok); }
@keyframes rpulse { 0%,100% { opacity: 1; } 50% { opacity: .45; } }
.runs-stage { min-width: 0; }
.runs-graph { background: var(--bg2); border: 1px solid var(--line); border-radius: 12px; padding: 12px 10px 6px; overflow: auto; max-height: 620px; }
.runs-graph svg { width: 100%; height: auto; display: block; min-width: 340px; }
.run-g .rg-box { cursor: pointer; }
.run-g rect.bx { fill: var(--panel); stroke: var(--line2); stroke-width: 1; rx: 7; }
.run-g rect.bx.s-running { stroke: var(--accent); stroke-width: 1.5; }
.run-g rect.bx.s-failed { stroke: var(--err); }
.run-g rect.bx.s-done { stroke: var(--ok); }
.run-g rect.bx.s-waiting { stroke-dasharray: 3 3; stroke: var(--dim); }
.run-g rect.bx.on { stroke: var(--white); stroke-width: 1.6; }
.run-g .bar { fill: var(--panel2); }
.run-g .bar.s-running { fill: var(--accent); }
.run-g .bar.s-failed { fill: var(--err); }
.run-g .bar.s-done { fill: var(--ok); }
.run-g .bar.s-waiting { fill: var(--dim); }
.run-g text.tl { fill: var(--txt); font-family: var(--mono); font-size: 12px; font-weight: 600; }
.run-g text.ts { fill: var(--mut); font-family: var(--mono); font-size: 10px; }
.run-g path.edge { fill: none; stroke: var(--line2); stroke-width: 1.2; }
.run-g path.edge.lit { stroke: var(--accent); stroke-width: 1.8; }
.run-g path.edge.to-running { stroke: var(--accent); stroke-dasharray: 4 5; animation: eflow 1.2s linear infinite; }
@keyframes eflow { to { stroke-dashoffset: -18; } }
.run-g path.head { fill: var(--line2); stroke: none; }
.run-g path.head.lit { fill: var(--accent); }
.run-g .rg-box:hover rect.bx { filter: brightness(1.35); }
.runs-detail { margin-top: 12px; background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--line2); border-radius: 10px; padding: 13px 16px; font-family: var(--mono); font-size: 12px; color: var(--mut); }
.runs-detail .d-title { color: var(--white); font-weight: 700; margin-bottom: 4px; }
.runs-detail .d-meta { color: var(--dim); font-size: 11px; margin-bottom: 8px; }
.runs-detail .d-sum { color: var(--gray); line-height: 1.55; white-space: pre-wrap; max-height: 180px; overflow: auto; }
.runs-detail .d-none { color: var(--dim); font-style: italic; }
.legend-runs { font-family: var(--mono); font-size: 10px; color: var(--dim); margin: 6px 0 0; }
.legend-runs b { color: var(--gray); font-weight: 600; }
.runs-more { margin-top: 8px; font-family: var(--mono); font-size: 11.5px; color: var(--mut);
  border: 1px solid var(--line2); background: var(--bg); padding: 6px 14px; border-radius: 999px; cursor: pointer; align-self: flex-start; }
.runs-more:hover { color: var(--white); border-color: var(--gray); }
.runs-list.capped .run-row[data-run-slot="more"] { display: none; }
.runs-list.capped .run-row[data-show-after] { display: none; }
"""

_RUN_JS = """
(function () {
  var RUNS = window.__RUNS__ || [];
  if (!RUNS.length) return;
  var listEl = document.getElementById('runs-list');
  var graphEl = document.getElementById('runs-graph');
  var detailEl = document.getElementById('runs-detail');
  var SVGNS = 'http://www.w3.org/2000/svg';
  var W = 214, H = 62, GX = 28, GY = 52, PAD = 16;

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function boxTitle(n) { return n.label.length > 30 ? n.label.slice(0, 29) + '…' : n.label; }
  function detail(run, n) {
    if (!detailEl) return;
    var deps = (n.deps || []).map(function (d) { return escapeHtml(d); }).join(', ') || 'none';
    detailEl.innerHTML =
      '<div class="d-title">' + escapeHtml(n.label) + '</div>' +
      '<div class="d-meta">state: ' + escapeHtml(n.state) +
      (n.lane ? ' · lane: ' + escapeHtml(n.lane) : '') +
      (n.model ? ' · model: ' + escapeHtml(n.model) : '') +
      ' · depends on: ' + deps + ' · attempts: ' + (n.attempts || 1) + '</div>' +
      (n.summary ? '<div class="d-sum">' + escapeHtml(n.summary) + '</div>'
        : '<div class="d-none">no detail recorded for this node</div>');
  }
  function light(id, on) {
    if (!graphEl) return;
    graphEl.querySelectorAll('path.edge, path.head').forEach(function (p) {
      var hit = p.getAttribute('data-from') === id || p.getAttribute('data-to') === id;
      p.classList.toggle('lit', !!on && hit);
    });
  }
  function draw(run) {
    if (!graphEl || !run) return;
    var nodes = run.nodes || [];
    graphEl.innerHTML = '';
    if (!nodes.length) { graphEl.innerHTML = '<div class="empty">empty run</div>'; return; }
    var maxLayer = 0;
    nodes.forEach(function (n) { if ((n.layer || 0) > maxLayer) maxLayer = n.layer || 0; });
    var per = {}, idx = {};
    nodes.forEach(function (n) { var l = n.layer || 0; per[l] = (per[l] || 0) + 1; });
    var maxCols = 1; Object.keys(per).forEach(function (l) { if (per[l] > maxCols) maxCols = per[l]; });
    var width = PAD * 2 + maxCols * W + (maxCols - 1) * GX;
    var height = PAD * 2 + (maxLayer + 1) * H + maxLayer * GY;
    var svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svg.setAttribute('class', 'run-g');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', escapeHtml(run.title));
    var pos = {};
    nodes.forEach(function (n) {
      var l = n.layer || 0;
      idx[l] = idx[l] || 0;
      pos[n.id] = { x: PAD + idx[l] * (W + GX), y: PAD + l * (H + GY) };
      idx[l]++;
    });
    var edges = document.createElementNS(SVGNS, 'g');
    nodes.forEach(function (n) {
      (n.deps || []).forEach(function (d) {
        var p = pos[d], c = pos[n.id];
        if (!p || !c) return;
        var x1 = p.x + W / 2, y1 = p.y + H, x2 = c.x + W / 2, y2 = c.y, midY = (y1 + y2) / 2;
        var path = document.createElementNS(SVGNS, 'path');
        path.setAttribute('class', 'edge' + (n.state === 'running' ? ' to-running' : ''));
        path.setAttribute('fill', 'none');
        path.setAttribute('d', 'M' + x1 + ',' + y1 + ' L' + x1 + ',' + midY + ' L' + x2 + ',' + midY + ' L' + x2 + ',' + y2);
        path.setAttribute('data-from', d); path.setAttribute('data-to', n.id);
        edges.appendChild(path);
        var head = document.createElementNS(SVGNS, 'path');
        head.setAttribute('class', 'head'); head.setAttribute('data-from', d); head.setAttribute('data-to', n.id);
        head.setAttribute('d', 'M' + (x2 - 5) + ',' + y2 + ' l5,-7 l5,7 Z');
        edges.appendChild(head);
      });
    });
    svg.appendChild(edges);
    nodes.forEach(function (n) {
      var p = pos[n.id];
      var g = document.createElementNS(SVGNS, 'g');
      g.setAttribute('class', 'rg-box'); g.setAttribute('data-node', n.id);
      g.setAttribute('transform', 'translate(' + p.x + ',' + p.y + ')');
      var rect = document.createElementNS(SVGNS, 'rect');
      rect.setAttribute('class', 'bx s-' + (n.state || 'unknown'));
      rect.setAttribute('width', W); rect.setAttribute('height', H); rect.setAttribute('rx', '7');
      var bar = document.createElementNS(SVGNS, 'rect');
      bar.setAttribute('class', 'bar s-' + (n.state || 'unknown'));
      bar.setAttribute('x', '6'); bar.setAttribute('y', '7'); bar.setAttribute('width', '3'); bar.setAttribute('height', H - 14);
      var tl = document.createElementNS(SVGNS, 'text');
      tl.setAttribute('class', 'tl'); tl.setAttribute('x', '16'); tl.setAttribute('y', '25');
      tl.textContent = boxTitle(n);
      var ts = document.createElementNS(SVGNS, 'text');
      ts.setAttribute('class', 'ts'); ts.setAttribute('x', '16'); ts.setAttribute('y', '45');
      ts.textContent = (n.state || 'unknown') + (n.lane ? ' · ' + n.lane : '') + (n.model ? ' · ' + n.model : '');
      var title = document.createElementNS(SVGNS, 'title');
      title.textContent = n.label + ' — ' + (n.state || 'unknown') + (n.summary ? ' — ' + n.summary : '');
      g.appendChild(rect); g.appendChild(bar); g.appendChild(tl); g.appendChild(ts); g.appendChild(title);
      g.addEventListener('click', function () {
        detail(run, n);
        document.querySelectorAll('.run-g .rg-box').forEach(function (b) {
          b.querySelector('rect.bx').classList.toggle('on', b === this);
        }, this);
      });
      g.addEventListener('mouseenter', function () { light(n.id, true); });
      g.addEventListener('mouseleave', function () { light(n.id, false); });
      svg.appendChild(g);
    });
    graphEl.appendChild(svg);
  }
  function select(id) {
    var run = null;
    for (var i = 0; i < RUNS.length; i++) if (RUNS[i].id === id) run = RUNS[i];
    if (!run) run = RUNS[0];
    document.querySelectorAll('.run-row').forEach(function (row) {
      row.classList.toggle('on', row.getAttribute('data-run') === run.id);
    });
    draw(run);
  }
  if (listEl) listEl.addEventListener('click', function (e) {
    var row = e.target.closest ? e.target.closest('.run-row') : null;
    if (row) select(row.getAttribute('data-run'));
  });
  var more = document.getElementById('runs-more');
  if (more) more.addEventListener('click', function () { if (listEl) listEl.classList.remove('capped'); });
  var on = document.querySelector('.run-row.on');
  select(on ? on.getAttribute('data-run') : RUNS[0].id);
})();
"""

_JS = """
(function () {
  // ---- hash router: one view at a time (Mnemosyne-console style)
  var VIEWS = ['overview', 'teams', 'runs', 'create', 'memory', 'profiles', 'cron'];
  function show(name) {
    if (VIEWS.indexOf(name) < 0) name = 'overview';
    document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('on'); });
    var el = document.getElementById('view-' + name);
    if (el) el.classList.add('on');
    document.querySelectorAll('.rail .nav a').forEach(function (a) {
      a.classList.toggle('on', a.getAttribute('href') === '#/' + name);
    });
  }
  window.addEventListener('hashchange', function () {
    show((location.hash || '#/overview').replace('#/', ''));
  });

  // ---- team diagram: click a node to highlight it
  document.querySelectorAll('.mesh-svg').forEach(function (svg) {
    svg.addEventListener('click', function (e) {
      var node = e.target.closest('g.node');
      if (!node) return;
      var active = node.classList.toggle('active');
      svg.querySelectorAll('g.node').forEach(function (n) { if (n !== node) n.classList.remove('active'); });
    });
  });

  // ---- team filter chips (Deployed only)
  document.querySelectorAll('.fchip').forEach(function (ch) {
    ch.addEventListener('click', function () {
      document.querySelectorAll('.fchip').forEach(function (c) { c.classList.remove('on'); });
      ch.classList.add('on');
      var filter = ch.getAttribute('data-team') || '';
      document.querySelectorAll('.teambox').forEach(function (box) {
        box.style.display = (!filter || box.getAttribute('data-team') === filter) ? '' : 'none';
      });
    });
  });

  // ---- action buttons: send prompt in the Hermes app, toast otherwise
  var toast = document.getElementById('toast');
  var toastCmd = document.getElementById('toast-cmd');
  function copyCmd() {
    var t = toastCmd ? toastCmd.textContent : '';
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t);
    else if (toastCmd) { var r = document.createRange(); r.selectNode(toastCmd); window.getSelection().removeAllRanges(); window.getSelection().addRange(r); }
  }
  var toastMsg = document.getElementById('toast-msg');
  var hostTimeout = null;
  var actionRan = false;
  function runLocal(action, payload, prompt) {
    if (!action || actionRan) return;
    actionRan = true;
    fetch('/api/action', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: action, payload: payload }) })
      .then(function (res) { return res.json(); })
      .then(function (data) { showResult(data); })
      .catch(function () {
        setToast('COPIABLE PLAYBOOK — no live server here. Open the live view (hermes teams dashboard) to execute, or tell Hermes this:', prompt, true);
      });
  }
  function setToast(msgText, cmdText, isErr) {
    if (!toast) return;
    if (isErr) { toast.classList.add('err'); } else { toast.classList.remove('err'); }
    if (toastMsg) toastMsg.textContent = msgText || '';
    if (toastCmd) toastCmd.textContent = cmdText || '';
    toast.classList.add('on');
  }
  function showResult(data) {
    if (!toast) return;
    if (data && data.ok) {
      if (data.needs_agent) {
        setToast('AGENT ACTION — this one needs Hermes to think. Tell Hermes:\\n\\n' + (data.prompt || ''), data.prompt || '', false);
      } else {
        setToast('EXECUTED OK\\n\\n' + (data.text || 'ok'), '', false);
      }
    } else {
      setToast('FAILED — ' + (data && data.error ? data.error : 'no response'), '', true);
    }
  }
  async function doAction(action, payload, prompt) {
    // 1) inside the Hermes app (widget): send the full playbook to the agent
    if (window.hermes && window.hermes.send) { window.hermes.send(prompt); return; }
    // 2) inside the desktop-app pane: hand it to the HOST app first. If the
    //    host doesn't answer (stale/old plugin), execute DIRECTLY here —
    //    same-origin to the local server, so buttons always work.
    if (window.parent && window.parent !== window) {
      setToast('SENDING TO TEAMOPS HOST — waiting…', '', false);
      clearTimeout(hostTimeout);
      actionRan = false;
      var promptForFallback = prompt;
      hostTimeout = setTimeout(function () {
        setToast('HOST DID NOT ANSWER — running directly…', '', false);
        runLocal(action, payload, promptForFallback);
      }, 8000);
      try { window.parent.postMessage({ source: 'teamops-dashboard', action: action, payload: payload, prompt: prompt }, '*'); } catch (e) { runLocal(action, payload, promptForFallback); }
      return;
    }
    // 3) live browser view: execute when we have an action; else playbook toast
    if (action) { runLocal(action, payload, prompt); return; }
    setToast('COPIABLE PLAYBOOK — open the live view (hermes teams dashboard) to execute, or tell Hermes this:', prompt, false);
  }
  document.querySelectorAll('[data-hermes-send]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var p = btn.getAttribute('data-hermes-send');
      var action = btn.getAttribute('data-action') || '';
      var payload = {};
      try { payload = JSON.parse(btn.getAttribute('data-payload') || '{}'); } catch (e) {}
      doAction(action, payload, p);
    });
  });
  window.addEventListener('message', function (e) {
    if (e.data && e.data.source === 'teamops-host') {
      clearTimeout(hostTimeout);
      showResult(e.data.result);
    }
  });  var copyBtn = document.getElementById('toast-copy');
  if (copyBtn) copyBtn.addEventListener('click', copyCmd);
  var closeBtn = document.getElementById('toast-close');
  if (closeBtn) closeBtn.addEventListener('click', function () { if (toast) toast.classList.remove('on'); });

  // ---- teams room: one diagram at a time, team picker
  function selectTeam(name) {
    if (!name) {
      var d = document.querySelector('.team-btn[data-default="1"]');
      if (d) name = d.getAttribute('data-team');
      else { var f = document.querySelector('.team-btn'); if (f) name = f.getAttribute('data-team'); }
    }
    if (!name) return;
    document.querySelectorAll('.team-btn').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-team') === name);
    });
    document.querySelectorAll('.diagram-pane').forEach(function (p) {
      p.classList.toggle('on', p.getAttribute('data-team') === name);
    });
  }
  document.querySelectorAll('.team-btn').forEach(function (b) {
    b.addEventListener('click', function () { selectTeam(b.getAttribute('data-team')); });
  });

  // ---- handoff chips: jump to the team and make its lines move
  document.querySelectorAll('.hf').forEach(function (hp) {
    hp.addEventListener('click', function () {
      var team = hp.getAttribute('data-jump');
      if (!team) return;
      show('teams');
      selectTeam(team);
      var pane = document.querySelector('.diagram-pane[data-team="' + team + '"]');
      var wrap = pane ? pane.querySelector('.mesh-wrap') : null;
      if (wrap) {
        wrap.classList.remove('boom');
        void wrap.offsetWidth;
        wrap.classList.add('boom');
        setTimeout(function () { wrap.classList.remove('boom'); }, 4500);
      }
    });
  });

  show((location.hash || '#/overview').replace('#/', ''));
  selectTeam();

  // live server mode: re-fetch every 2 minutes so the page never goes stale
  if (location.protocol.indexOf('http') === 0) {
    setTimeout(function () {
      location.href = location.pathname + '?t=' + Date.now();
    }, 120000);
  }
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


def _btn(action: str, payload: dict, prompt: str, label: str, kind: str = "act") -> str:
    """Action button: carries a DETAILED playbook prompt (sent to the agent in
    app/chat, or shown copyable in the toast) PLUS structured action+payload so
    the live server (or the desktop pane proxy) can EXECUTE the action."""
    payload_html = _esc(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    prompt_html = html.escape("" if prompt is None else str(prompt), quote=True).replace("\n", "&#10;")
    return (f'<button class="{kind}" data-hermes-send="{prompt_html}" '
            f'data-action="{action}" data-payload="{payload_html}">{label}</button>')


_RUN_ARGS: dict[str, list[str]] = {
    "research-swarm.sh": ["run", "--lanes", "competitors"],
    "agentic-coding-team.sh": ["--lane", "code"],
}


def _run_prompt(t: dict, key: str) -> str:
    roles = ", ".join(t.get("roles") or [])
    script = t.get("script", "")
    hub = t.get("hub", "")
    args = " " + " ".join(_RUN_ARGS.get(script, [])) if _RUN_ARGS.get(script) else ""
    agents_label = roles or "the team's agents"
    return (
        f"teamops: RUN the {key} team now — like our own operator.\n\n"
        f"EXECUTE exactly: bash ~/{script}{args}  (background it, confirm the pid "
        f"and the log file before moving on).\n\n"
        f"WHO IT IS: {agents_label} — hub: {hub}. Let every "
        f"lane/stage finish; only stop early on a hard error.\n\n"
        f"WHILE IT RUNS:\n"
        f"- Check the log after ~30s. If it crashed, show the last 25 lines verbatim "
        f"and diagnose (missing API key? dead model 404? timeout?). Never change "
        f"model configuration.\n"
        f"- Watch the typed handoffs between roles; note where work stalls.\n\n"
        f"WHEN DONE: give a plain-language report — start/end time, lanes or stages "
        f"completed vs failed, top findings, artifact paths (under ~/.hermes/...), "
        f"and anything that needs Dalton's decision.\n\n"
        f"RULES: never edit customer-shipping files (onboard-*.sh, "
        f"onboard-self-serve/*). Research runs never auto-apply changes."
    )


def _last_prompt(t: dict, key: str) -> str:
    script = t.get("script", "")
    return (
        f"teamops: show me the last run of {key} (script ~/{script}).\n\n"
        f"Find the newest run evidence: the latest log PLUS the newest artifacts "
        f"under the team workspace (~/.hermes/{key}/ or the matching dirs). "
        f"Summarize: start/end time, stages or lanes completed vs failed, what it "
        f"produced (exact artifact paths), key findings, and follow-ups. "
        f"If there is NO run yet, say 'no runs yet' — never invent output."
    )


def _standup_prompt(tname: str) -> str:
    return (
        f"teamops: generate the {tname} standup digest.\n\n"
        f"Use the team standup builder: read {tname}'s MESH-MAP.md (members, "
        f"handoffs, pending syncs), each member profile's recent sessions, kanban "
        f"counts, and cron last-run statuses. Output ONE page: what moved since "
        f"the last standup, blockers (cron failures HIGH/MED with the actual "
        f"error), next steps + owners. Plain English, no internal IDs."
    )


def _deploy_prompt(b: dict) -> str:
    bid = b.get("id", "")
    return (
        f"teamops: deploy the {b.get('name', bid)} blueprint team end to end, "
        f"exactly like our Deploy Guard.\n\n"
        f"1) PREFLIGHT: parse blueprints/{bid}/team.yaml; bash -n any generated "
        f"scripts; check roles, gates, ids (no phantom agents, no duplicate R###, "
        f"pricing matches the site if present).\n"
        f"2) DEPLOY: run hermes teams deploy {bid} against HERMES_HOME.\n"
        f"3) GUARD: dry-run first, then smoke + verify each member profile boots; "
        f"if any gate fails, roll back (hermes teams rollback) and report what broke.\n"
        f"4) REPORT: a table of member profiles + status + crons, what the buyer "
        f"sees on day 1, and the first command to run. NEVER provision on the "
        f"portal VPS (blueprint store only)."
    )


CREATE_TEAM_PROMPT = (
    "teamops: use the Agent Factory to create a NEW team blueprint from my "
    "description (ask me one focused question first if you need the goal).\n\n"
    "STEPS: capture the goal in plain English → choose 3-6 agents with ONE job "
    "each → pick the best harness/lane pattern → generate the blueprint → deploy "
    "to HERMES_HOME with the Deploy Guard (verify + rollback available) → show me "
    "the team as roles, hub, and handoffs.\n"
    "REPORT: blueprint path + what the team can do on day 1. Validate before "
    "declaring done."
)


def _generate_prompt(vertical: str) -> str:
    return (
        f"teamops: generate the {vertical} onboard team blueprint with the Agent "
        f"Factory.\n\n"
        f"Model it on the existing onboard-*.sh scripts: idempotent installer "
        f"pattern, per-customer VPS deploy, no shared-state assumptions, each "
        f"agent = one employee with one clearly named job (3-5 agents max).\n"
        f"STEPS: freeze scope → draft the agents + their exact functions → "
        f"generate the script following conventions → run the validator "
        f"(bash -n, no phantom agents, no duplicate R###, pricing matches the "
        f"site, audit checklist) → deliver the script path + a 5-line buyer "
        f"pitch. Never ship to the portal VPS; use light checks or a throwaway VPS."
    )


def _eph_card(t: dict) -> str:
    """One ephemeral team card: state in words + pill, one-line description, run buttons."""
    pill = ('<span class="pill run">RUNNING</span>' if t.get("running")
            else '<span class="pill ready">READY</span>')
    name = _esc(t.get("name", ""))
    script = _esc(t.get("script", ""))
    run_prompt = _run_prompt(t, t["name"])
    last_prompt = _last_prompt(t, t["name"])
    return (
        f'<div class="teambox eph" data-team="{name}">'
        f'<div class="tb-head"><h3>// {name}</h3><div class="spacer"></div>{pill}</div>'
        f'<div class="sub">one-shot team · ~/{script}</div>'
        f'<p class="desc">{_esc(t.get("description", ""))}</p>'
        f'<div class="acts">'
        f'{_btn("run_script", {"script": t.get("script"), "args": _RUN_ARGS.get(t.get("script", ""), [])}, run_prompt, "RUN", "act primary")}'
        f'{_btn("last_run", {"team": t.get("name"), "script": t.get("script")}, last_prompt, "LAST RUN")}'
        f'</div></div>'
    )


def _launch_card(b: dict) -> str:
    """Blueprint card in the Create view: roles + description + deploy button."""
    roles = ", ".join(_esc(r) for r in (b.get("roles") or []))
    return (
        f'<div class="launchbox" data-team="{_esc(b.get("id", ""))}">'
        f'<div class="hd"><h3>{_esc(b.get("name", ""))}</h3>'
        f'<span class="pill idle">NOT RUNNING</span></div>'
        f'<div class="roles">{roles or "no roles listed"}</div>'
        f'<p class="desc">{_esc(b.get("description", ""))}</p>'
        f'<div class="acts">'
        f'{_btn("deploy", {"name": b.get("id")}, _deploy_prompt(b), "DEPLOY", "act primary")}'
        f'</div></div>'
    )


def _membox(p: dict) -> str:
    mem = p.get("memory") or {}
    pct = mem.get("memory_pct")
    upct = mem.get("user_pct")

    def _row(key: str, val: float | None) -> str:
        if val is None:
            return f'<div class="memrow"><span class="mkey">{key}</span><span class="mval">n/a</span></div>'
        return (f'<div class="memrow"><span class="mkey">{key}</span>'
                f'<div class="mbar"><i style="width:{min(val,100):.1f}%"></i></div>'
                f'<span class="mval">{val:.1f}%</span></div>')

    return (f'<div class="membox"><h4>{_esc(p.get("name", ""))}</h4>'
            + _row("MEM", pct) + _row("USR", upct)
            + f'<div class="memrow"><span class="mkey">SKL</span>'
            + f'<span class="mval">{_esc(mem.get("skills", 0))}</span></div></div>')


def _hub_name(nodes: list[str], edges: list[dict]) -> str:
    """Pick the centre of the diagram: an orchestrator-type role if present."""
    hints = ("boss", "orchestrator", "owner", "chair", "pm", "dispatcher", "coordinator",
             "lead", "reviewer", "synthesizer", "strategist", "architect")
    for n in nodes:
        low = n.lower()
        if any(h in low for h in hints):
            return n
    counts: dict[str, int] = {}
    for e in edges:
        for k in ("from", "to"):
            v = e.get(k)
            if v:
                counts[str(v)] = counts.get(str(v), 0) + 1
    if counts:
        return max(counts, key=lambda k: counts[k])
    return nodes[0] if nodes else "?"


_HANDOFF_MARKERS = ("handoff", "handoffs/", "typed handoff", ".json", "deliverable",
                    "fan-in", "passes", "forwards", "sends the file", "artifact to")


def render_mesh_svg(team: str, entry: dict) -> str:
    """Hub-and-spoke team diagram in the AutomatedAgents morph style.

    The diagram shows the team's ACTUAL agents: for deployed teams the member
    profiles (>=2), for ephemeral teams their documented roles — never a fixed
    count. The hub sits at the centre; white round-cap rays carry red traveling
    pulses; edges that carry a typed handoff get a second, white pulse; the
    morph control draws the outer legs in to form the AA mark.
    """
    mesh = entry.get("mesh") or {}
    edges = mesh.get("edges") or []
    members = entry.get("members") or []
    hub_override = entry.get("hub")

    def _norm(value: object) -> str:
        import re as _re
        return _re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")

    def _match(a: str, b: str) -> bool:
        """Fuzzy role↔member match: 'tech lead' == 'ng-tech-lead', 'pm' ==
        'trading-pm'. Both directions so handoffs are found regardless of which
        side uses the short role name."""
        na, nb = _norm(a), _norm(b)
        return bool(na and nb) and (na == nb or na in nb or nb in na)

    if mesh.get("parse_error"):
        return '<div class="empty">no mesh edges yet — document them in a MESH-MAP.md to draw the graph</div>'

    # nodes = the team's agents (members when we have >=2, else mesh endpoints)
    if len(members) >= 2:
        seed = list(members)
    else:
        seed = [str(e.get(k)) for e in edges for k in ("from", "to") if e.get(k)] + list(members)
    nodes: list[str] = []
    seen: set[str] = set()
    for s in seed:
        if not s or s in seen:
            continue
        seen.add(s)
        nodes.append(s)
    for s in seed:
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        nodes.append(s)
    # make duplicate role names unique (e.g. two writer-lanes)
    final: list[str] = []
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n] = counts.get(n, 0) + 1
        final.append(n if counts[n] == 1 else f"{n}-{counts[n]}")
    nodes = final
    if not nodes:
        return '<div class="empty">no mesh edges yet — document them in a MESH-MAP.md to draw the graph</div>'

    hub = hub_override if hub_override in nodes else _hub_name(nodes, edges)
    spokes = [n for n in nodes if n != hub]

    w, h, cx, cy = 1200.0, 1050.0, 600.0, 545.0
    radius = 300.0
    pos: dict[str, tuple[float, float]] = {hub: (cx, cy)}
    n = len(spokes)
    for i, name in enumerate(spokes):
        ang = (-90.0 + 360.0 * i / max(n, 1)) * 3.14159265 / 180.0
        pos[name] = (cx + radius * _cos(ang), cy + radius * _sin(ang))

    marker_id = f"arrow-{team}"
    parts = [
        f'<svg class="mesh-svg" viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-label="mesh graph for {_esc(team)}">',
        f'<defs><marker id="{_esc(marker_id)}" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">'
        f'<path d="M0,0 L0,6 L7,3 z" fill="#fff"/></marker></defs>',
    ]
    # rays: hub -> every spoke (via = first mesh edge between them, as tooltip)
    for i, name in enumerate(spokes):
        x1, y1 = pos[hub]
        x2, y2 = pos[name]
        via = ""
        for e in edges:
            f, t = str(e.get("from")), str(e.get("to"))
            if (_match(f, hub) and _match(t, name)) or (_match(f, name) and _match(t, hub)):
                via = e.get("via") or ""
                break
        parts.append(
            f'<line class="ray" data-from="{_esc(hub)}" data-to="{_esc(name)}" '
            f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}">'
            f'<title>{_esc(via)}</title></line>'
        )
        parts.append(
            f'<line class="pulse" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'style="animation-delay:-{i * 1.1:.2f}s"/>'
        )
        if any(m in (via or "").lower() for m in _HANDOFF_MARKERS):
            parts.append(
                f'<line class="pulse pwh" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'style="animation-delay:-{0.4 + i * 1.3:.2f}s"/>'
            )
    for name in nodes:
        x, y = pos[name]
        is_hub = name == hub
        radius = 64 if is_hub else 34  # one big centre, smaller agents around it
        node = (f'<g class="node{" hub" if is_hub else ""}" data-name="{_esc(name)}" '
                f'transform="translate({x:.1f},{y:.1f})"><circle r="{radius}"/></g>')
        if is_hub:
            px, py = x, y + 92
        else:
            dx, dy = x - cx, y - cy
            dlen = max((dx * dx + dy * dy) ** 0.5, 1.0)
            px, py = x + dx / dlen * 84, y + dy / dlen * 84
        label_w = max(96.0, 16.0 + len(name) * 7.6 + 18.0)
        label = (
            f'<g class="nlabel" transform="translate({px:.1f},{py:.1f})">'
            f'<rect x="{-label_w / 2:.1f}" y="-15" width="{label_w:.1f}" height="30"/>'
            f'<text y="5">{_esc(name)}</text></g>'
        )
        parts.append(node + label)
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
    data["teams"] = fleet.enrich_teams(data.get("teams", {}), data.get("profiles", []))
    try:
        data["ephemeral"] = fleet.ephemeral_teams(home)
    except Exception:  # noqa: BLE001
        data["ephemeral"] = []
    try:
        data["blueprints"] = fleet.available_blueprints(home)
    except Exception:  # noqa: BLE001
        data["blueprints"] = {"blueprints": [], "templates": []}
    try:
        from . import cron911
        data["cron"] = cron911.scan(home)
    except Exception:  # noqa: BLE001 — degrade: dashboard stays usable
        data["cron"] = None
    try:
        data["runs"] = [runs.graph_of(r) for r in runs.recent_runs(home, limit=20)]
    except Exception:  # noqa: BLE001
        data["runs"] = []
    return data


def _run_count_text(rec: dict) -> str:
    c = rec.get("counts") or {}
    parts = [f"{c.get('nodes', 0)} nodes"]
    for key in ("done", "running", "failed"):
        if c.get(key):
            parts.append(f"{c[key]} {key}")
    return " · ".join(parts)


def _run_time_ago(started) -> str:
    if not started:
        return "—"
    try:
        ts = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        secs = max(0, (datetime.now(timezone.utc) - ts).total_seconds())
    except ValueError:
        return "—"
    if secs < 60:
        return "now"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    if secs < 86400:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"


def _runs_view_html(runs: list[dict]) -> str:
    """Runs tab: list column (server-rendered rows) + graph stage (JS draws it)."""
    if not runs:
        return ('<div class="empty">No runs recorded yet — run a team, deploy a '
                'blueprint or start a dashboard action to see its workflow here. '
                '(Sources: dashboard action logs, team logs, canonical run records.)</div>')
    rows: list[str] = []
    cap = 10
    for i, rec in enumerate(runs):
        rid = _esc(rec.get("id", f"run-{i}"))
        title = _esc(rec.get("title") or "Run")
        state = _esc(rec.get("state") or "unknown")
        on = ' on' if i == 0 else ""
        slot = ' data-run-slot="more"' if i >= cap else ""
        after = ' data-show-after="1"' if i >= cap else ""
        rows.append(
            f'<button class="run-row{on}" data-run="{rid}"{slot}{after}>'
            f'<span class="dot s-{state}"></span>'
            f'<span class="rt">{title}</span>'
            f'<span class="rc">{_esc(_run_count_text(rec))}</span>'
            f'<span class="ra">{_esc(_run_time_ago(rec.get("started")))}</span></button>'
        )
    more = ('<button class="runs-more" id="runs-more">Show all</button>' if len(runs) > cap else "")
    list_html = (f'<div class="runs-list{" capped" if len(runs) > cap else ""}" id="runs-list">'
                 + "".join(rows) + more + "</div>")
    stage = ('<div class="runs-stage">'
             '<div class="runs-graph" id="runs-graph"><div class="empty">Select a run →</div></div>'
             '<div class="runs-detail" id="runs-detail">'
             '<div class="d-none">Pick a run on the left. Each box is one node; a dashed '
             'edge pulses into a running node.</div></div>'
             '<p class="legend-runs">state dot: <b>accent = running</b> · <b style="color:var(--err)">red = failed</b> · '
             '<b style="color:var(--ok)">green = done</b> · read-only — nothing here changes a run</p>'
             '</div>')
    return '<div class="runs-layout">' + list_html + stage + "</div>"


def render_html(data: dict, title: str = "TeamOps Fleet Dashboard", build_id: str = "",
                theme: dict | None = None) -> str:
    """Render the complete standalone HTML document (rail + tabbed views)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    home = _esc(data.get("home", ""))
    profiles = data.get("profiles", [])
    teams = data.get("teams", {})
    cron = data.get("cron")
    ephemeral = data.get("ephemeral", []) or []
    blueprints = data.get("blueprints", {}) or {}
    bp_list = blueprints.get("blueprints", []) or []
    tpl_list = [n for n in (blueprints.get("templates", []) or [])
                if n not in ("job-cards", "local", "customer")]
    build = _esc(build_id or "local")
    theme_css = _theme_root_css(theme)
    theme_label = ""
    if theme:
        theme_label = _esc(theme.get("label") or theme.get("name") or "")
    run_records = data.get("runs") or []
    runs_html = _runs_view_html(run_records)
    runs_json = json.dumps(run_records, separators=(",", ":")).replace("</", "<\\/")

    # 24h buckets come from execution_stats' recent* counters; total/ok/failed
    # are all-time and shown as such in the profile table.
    prof_24h = sum((p.get("executions_24h") or {}).get("recent", 0) for p in profiles)
    prof_fail = sum((p.get("executions_24h") or {}).get("recent_failed", 0) for p in profiles)
    home_exec = data.get("home_executions_24h") or {}
    total_exec = prof_24h + home_exec.get("recent", 0)
    total_fail = prof_fail + home_exec.get("recent_failed", 0)

    issues = (cron or {}).get("issues", []) if cron else None
    dead_models = 0
    if issues is not None:
        for i in issues:
            if "DEAD MODEL" in (i.get("detail") or ""):
                dead_models += 1
    info = (cron or {}).get("info", []) if cron else []

    # profile -> team membership (for hop-to-mesh handoff chips)
    profile_team: dict[str, str] = {}
    for tname, t in teams.items():
        for m in (t.get("members") or []):
            profile_team[m] = tname
    jobs_by_profile = {p.get("name", ""): (p.get("jobs") or []) for p in profiles}
    handoff_jobs: dict[tuple[str, str], str] = {}
    for p in profiles:
        team = profile_team.get(p.get("name", ""))
        for jd in (p.get("jobs") or []):
            if jd.get("handoff") and team:
                handoff_jobs[(p.get("name", ""), jd.get("name", ""))] = team

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
        jobs = p.get("jobs") or []
        shown, extra = jobs[:5], len(jobs) - 5
        js = ""
        for j in shown:
            js += f"<li>{_esc(j.get('name'))} {_job_status_chip(j)}"
            if j.get("handoff") and profile_team.get(p.get("name", "")):
                team = profile_team[p["name"]]
                js += (f' <button class="hf" data-jump="{_esc(team)}" '
                       f'title="handoff job — pulse it on the {_esc(team)} mesh">&#8644;</button>')
            js += "</li>"
        if extra > 0:
            js += f'<li class="more">+{extra} more</li>'
        rows.append(
            f"<tr><td><strong>{_esc(p['name'])}</strong>"
            f"{' <span class=chip.ok>SOUL</span>' if p.get('has_soul') else ''}</td>"
            f"<td>{_mem_bar(mem.get('memory_pct'))}</td>"
            f"<td>{_esc(mem.get('skills', 0))} skill(s)</td>"
            f"<td>{_esc(p.get('crons', 0))}</td>"
            f"<td>{_esc(execs.get('recent', 0))} in 24h "
            f"({_esc(execs.get('recent_ok', 0))} ok / "
            f"<span style='color:#fff;font-weight:700'>{_esc(execs.get('recent_failed', 0))} fail</span>)"
            f"<br><span style='color:#6b6b74;font-size:11px'>{_esc(execs.get('total', 0))} all-time</span></td>"
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
        cron_html = '<div style="color:#cfcfcf">No cron issues detected.</div>'
    else:
        rows = []
        for i in issues:
            hf = ""
            team = handoff_jobs.get((str(i.get("scope", "")), str(i.get("job", ""))))
            if team:
                hf = (f' <button class="hf" data-jump="{_esc(team)}" '
                      f'title="handoff job — pulse it on the {_esc(team)} mesh">&#8644;</button>')
            rows.append(
                f"<tr><td><span class=\"badge {_esc(i.get('severity'))}\">{_esc(i.get('severity'))}</span></td>"
                f"<td>{_esc(i.get('scope'))}</td><td>{_esc(i.get('job'))}{hf}</td>"
                f"<td>{_esc(i.get('detail'))}</td></tr>"
            )
        for i in info:
            hf = ""
            team = handoff_jobs.get((str(i.get("scope", "")), str(i.get("job", ""))))
            if team:
                hf = (f' <button class="hf" data-jump="{_esc(team)}" '
                      f'title="handoff job — pulse it on the {_esc(team)} mesh">&#8644;</button>')
            rows.append(
                f"<tr><td><span class=\"badge INFO\">INFO</span></td><td>{_esc(i.get('scope'))}</td>"
                f"<td>{_esc(i.get('job'))}{hf}</td><td>{_esc(i.get('detail'))}</td></tr>"
            )
        cron_html = ("<table><tr><th>Severity</th><th>Scope</th><th>Job</th><th>Detail</th></tr>"
                     + "".join(rows) + "</table>")

    # ---- teams room: picker (team names) + ONE diagram at a time
    soul_names = {p.get("name") for p in profiles if p.get("has_soul")}
    list_btns: list[str] = []
    panes: list[str] = []
    default_set = False

    if ephemeral:
        list_btns.append(f'<h3 class="group">Ephemeral <span class="n">· {len(ephemeral)}</span></h3>')
    for t in ephemeral:
        raw = t.get("name", "")
        key = "eph-" + _esc(raw)
        display = (raw + " (script)") if raw in teams else raw
        name = _esc(display)
        pill = ('<span class="pill run">RUNNING</span>' if t.get("running")
                else '<span class="pill ready">READY</span>')
        list_btns.append(
            f'<button class="team-btn" data-team="{key}">'
            f'<span class="tname">{name}</span>{pill}</button>'
        )
        roles = t.get("roles") or []
        members_html = '<div class="members">' + "".join(
            f'<span class="mem-chip">{_esc(r)}</span>' for r in roles
        ) + "</div>"
        diagram = render_mesh_svg(raw, {"members": roles, "mesh": {}, "hub": t.get("hub")})
        lanes_html = ""
        if t.get("lanes"):
            lane_groups = t["lanes"]
            total_lanes = sum(len(v) for v in lane_groups.values())
            rows = []
            for group, lanes in lane_groups.items():
                rows.append(
                    f'<tr><td colspan="3" class="lane-group">{_esc(group)}</td></tr>'
                )
                for lane_name, lane_roles, lane_desc in lanes:
                    rows.append(
                        f"<tr><td class='mono'>{_esc(lane_name)}</td>"
                        f"<td>{_esc(lane_roles)}</td>"
                        f"<td>{_esc(lane_desc)}</td></tr>"
                    )
            lanes_html = (
                f'<div class="crondiv"><div class="crondiv-h">LANES · {total_lanes} (4 GROUPS)</div>'
                '<table><tr><th>Lane</th><th>Roles</th><th>What it does</th></tr>'
                + "".join(rows) + "</table></div>"
            )
        pane_sub = f'one-shot team · ~/{_esc(t.get("script", ""))}'
        if t.get("sub_agents"):
            pane_sub += f' · {_esc(t.get("sub_agents", ""))} sub-researchers → 1 synthesizer'
        else:
            pane_sub += f' · {_esc(t.get("description", ""))}'
        run_prompt = _run_prompt(t, raw)
        last_prompt = _last_prompt(t, raw)
        panes.append(
            f'<div class="diagram-pane" data-team="{key}">'
            f'<div class="pane-head"><h2>{name}</h2><div class="spacer"></div>{pill}'
            f'<span class="tb-badge">EPHEMERAL</span></div>'
            f'<div class="pane-sub">{pane_sub}</div>'
            f'{members_html}'
            f'<div class="mesh-wrap">{diagram}</div>'
            f'<p class="legend">red pulse = agent working · white pulse = typed handoff</p>'
            f'{lanes_html}'
            f'<div class="crondiv"><div class="crondiv-h">CRONS WITH THIS TEAM · 0</div>'
            f'<div class="empty">no scheduled crons — launched on demand or by an owning profile\'s cron</div></div>'
            f'<div class="acts">'
            f'{_btn("run_script", {"script": t.get("script"), "args": _RUN_ARGS.get(t.get("script", ""), [])}, run_prompt, "RUN", "act primary")}'
            f'{_btn("last_run", {"team": t.get("name"), "script": t.get("script")}, last_prompt, "LAST RUN")}'
            f'</div></div>'
        )

    if teams:
        list_btns.append(f'<h3 class="group">Deployed <span class="n">· {len(teams)}</span></h3>')
    for tname, t in sorted(teams.items()):
        name = _esc(tname)
        disp = _esc(t.get("label") or tname)
        pill = '<span class="pill actv">ACTIVE</span>'
        badge = f"{len(t.get('members') or [])} MEMBERS" if t.get("members") else ("GROUP" if t.get("group") else "DIR")
        default_attr = ' data-default="1"' if not default_set else ""
        default_set = True
        list_btns.append(
            f'<button class="team-btn{default_attr}" data-team="{name}">'
            f'<span class="tname">{disp}</span>{pill}</button>'
        )
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
        members = t.get("members") or []
        members_html = ""
        if members:
            members_html = '<div class="members">' + "".join(
                f'<span class="mem-chip{" soul" if m in soul_names else ""}">{_esc(m)}</span>'
                for m in members
            ) + "</div>"
        edges_n = len((t.get("mesh") or {}).get("edges") or [])
        stats = t.get("stats")
        bits = []
        if stats:
            bits.append(f"{stats.get('members', 0)} profiles")
            bits.append(f"{stats.get('crons', 0)} crons")
            bits.append(f"{(stats.get('exec_24h') or {}).get('failed', 0)} fail (24h)")
        else:
            bits.append(f"mesh edges: {edges_n}")
        if edges_n:
            bits.append(f"mesh edges: {edges_n}")
        elif t.get("mesh_map"):
            bits.append(f"mesh map: {(t.get('mesh_map') or {}).get('lines', 0)} lines")
        sub = " · ".join(bits)
        diagram = render_mesh_svg(tname, t)
        team_rows: list[str] = []
        total_crons = 0
        for m in members:
            for j in jobs_by_profile.get(m, []):
                total_crons += 1
                if len(team_rows) < 14:
                    team_rows.append(
                        f"<tr><td>{_esc(j.get('name'))} {_job_status_chip(j)}</td>"
                        f"<td class='mono'>{_esc(j.get('schedule') or '—')}</td>"
                        f"<td>{_esc(m)}</td></tr>"
                    )
        crons_html = (f'<div class="crondiv"><div class="crondiv-h">CRONS WITH THIS TEAM · {total_crons}</div>'
                      + ('<table><tr><th>Job</th><th>Schedule</th><th>Owner</th></tr>'
                         + "".join(team_rows) + "</table>" if team_rows
                         else '<div class="empty">no scheduled crons — event-driven team</div>')
                      + "</div>")
        pane = (f'<div class="diagram-pane" data-team="{name}">'
                f'<div class="pane-head"><h2>{disp}</h2><div class="spacer"></div>{pill}'
                f'<span class="tb-badge">{_esc(badge)}</span></div>'
                f'<div class="pane-sub">{_esc(sub)}</div>'
                f"{kanc}{members_html}"
                f'<div class="mesh-wrap">{diagram}</div>'
                f'<p class="legend">red pulse = agent working · white pulse = typed handoff</p>'
                f"{crons_html}")
        standup_prompt = _standup_prompt(tname)
        pane += f'<div class="acts">{_btn("standup", {"team": tname}, standup_prompt, "STANDUP")}</div>'
        pane += "</div>"
        panes.append(pane)

    if not teams and not ephemeral:
        teams_html = '<div class="empty">no teams / mesh yet — deploy a blueprint to build one</div>'
    else:
        room = ('<div class="team-room"><div class="team-list">' + "".join(list_btns)
                + '</div><div class="diagram-stage">' + "".join(panes) + "</div></div>")
        if not teams:
            teams_html = ('<div class="empty">no teams / mesh yet — '
                          'deploy a blueprint to build one</div>') + room
        else:
            teams_html = room

    # ---- launch / create
    if bp_list:
        launch_bp_html = '<div class="grid2">' + "".join(_launch_card(b) for b in bp_list) + "</div>"
    else:
        launch_bp_html = '<div class="empty">no deployable blueprints found</div>'
    factory_html = (
        '<div class="grid2"><div class="launchbox factory">'
        '<div class="hd"><h3>Agent Factory</h3></div>'
        '<div class="roles">describe a team in plain English — generated blueprint → deploy + verify</div>'
        '<p class="desc">Create a brand-new team blueprint from a one-line spec, or extend an existing one.</p>'
        '<div class="acts">'
        f'{_btn("create", {"base": "custom", "prompt": CREATE_TEAM_PROMPT}, CREATE_TEAM_PROMPT, "CREATE CUSTOM TEAM", "act primary")}'
        '</div></div></div>'
    )
    if tpl_list:
        vcards = []
        for n in tpl_list:
            gen_prompt = _generate_prompt(n)
            vcards.append(
                f'<div class="launchbox" data-team="{_esc(n)}">'
                f'<div class="hd"><h3>{_esc(n)}</h3>'
                f'<span class="pill idle">NOT RUNNING</span></div>'
                f'<div class="roles">website team · template in $HERMES_HOME/templates/{_esc(n)}</div>'
                f'<p class="desc">From the automatedagents.co {_esc(n)} offering — not deployed to this box yet.</p>'
                f'<div class="acts">'
                f'{_btn("generate", {"vertical": n, "prompt": gen_prompt}, gen_prompt, "GENERATE TEAM", "act primary")}'
                f'</div></div>'
            )
        tpl_html = '<div class="grid2">' + "".join(vcards) + "</div>"
    else:
        tpl_html = '<div class="empty">no customer templates found</div>'
    create_html = (launch_bp_html + factory_html
                   + f'<h3 class="group">Not deployed yet — website teams <span class="n">· {len(tpl_list)}</span></h3>'
                   + tpl_html)

    # ---- memory
    if profiles:
        memory_html = '<div class="mem-wrap">' + "".join(_membox(p) for p in profiles) + "</div>"
    else:
        memory_html = '<div class="empty">no profiles deployed in this home</div>'

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — {home}</title>
<style>{_CSS}{_RUN_CSS}</style>{f'<style id="teamops-theme">{theme_css}</style>' if theme_css else ''}
</head>
<body>
<aside class="rail">
  <div class="brand">
    <svg width="34" height="26" viewBox="0 0 34 26" role="img" aria-label="threshold mark">
      <path d="M6 23 V9 a11 11 0 0 1 22 0 V23" fill="none" stroke="#fff" stroke-width="1.6"/>
      <path d="M2 23 H32" stroke="#fff" stroke-width="1.3"/>
      <path d="M17 3.4 L19.4 8.1 L24.5 10.5 L19.4 12.9 L17 17.6 L14.6 12.9 L9.5 10.5 L14.6 8.1 Z" fill="none" stroke="#bdbdbd" stroke-width="1.1"/>
    </svg>
    <div><b>TEAMOPS</b><span>Fleet Command</span></div>
  </div>
  <nav class="nav">
    <a href="#/overview" class="on"><span class="sig">OV</span>Overview</a>
    <a href="#/teams"><span class="sig">TE</span>Teams</a>
    <a href="#/runs"><span class="sig">RN</span>Runs</a>
    <a href="#/create"><span class="sig">CR</span>Create</a>
    <a href="#/memory"><span class="sig">ME</span>Memory</a>
    <a href="#/profiles"><span class="sig">PF</span>Profiles</a>
    <a href="#/cron"><span class="sig">CK</span>Cron health</a>
  </nav>
  <div class="foot">
    <b>BUILD {build}</b>{f'<br>theme {theme_label}' if theme_label else ''}<br>
    {len(profiles)} profiles · {len(teams)} teams · {len(ephemeral)} ephemeral<br>
    {home}<br>{now}
  </div>
</aside>
<main class="main">

  <section class="view on" id="view-overview">
    <div class="pagehead">
      <div class="eyebrow"><span class="live">● LIVE</span> · Fleet overview</div>
      <h1>Your teams, at a glance</h1>
      <p>Profile count, cron load, 24h execution health and dead-model flags across every deployed team in this home.</p>
    </div>
    <div class="cards">{cards_html}</div>
    <div class="quickacts">
      <button class="act primary" data-hermes-send="teamops: show me fleet health right now (fresh scan, no changes)">Scan fleet health</button>
      <button class="act" data-hermes-send="teamops: generate a standup digest for every team (last 24h)">All-team standup</button>
      <button class="act" data-hermes-send="teamops: show the latest cron failures and what is blocking them">Show failures</button>
      <button class="act" data-hermes-send="teamops: which models are dead / no longer free right now">Dead models</button>
    </div>
  </section>

  <section class="view" id="view-teams">
    <div class="pagehead">
      <div class="eyebrow">Team inventory · state + mesh</div>
      <h1>Teams &amp; mesh</h1>
      <p>Pick a team on the left to see ITS diagram — one at a time. One-shot script teams have zero resident RAM and start on demand; deployed profile teams run on their crons 24/7. Click a node, or click ⇄ on any handoff job to make the line move.</p>
    </div>
    {teams_html}
  </section>

  <section class="view" id="view-runs">
    <div class="pagehead">
      <div class="eyebrow">Runs &amp; workflow</div>
      <h1>Workflow view</h1>
      <p>Recent jobs drawn as a graph: one frontier to a row, edges are dependencies (a dashed edge pulses into a running node). Read-only — nothing here changes a run.</p>
    </div>
    {runs_html}
  </section>

  <section class="view" id="view-create">
    <div class="pagehead">
      <div class="eyebrow">Launch / create</div>
      <h1>Stand up a team</h1>
      <p>Deploy a ready blueprint, ask the Agent Factory to invent one from a plain-English spec, or generate a customer vertical on the onboard pattern.</p>
    </div>
    {create_html}
  </section>

  <section class="view" id="view-memory">
    <div class="pagehead">
      <div class="eyebrow">Memory footprint</div>
      <h1>Memory</h1>
      <p>Per-profile MEMORY.md / USER.md usage against the 2200-char cap, plus skill counts — every profile in one grid.</p>
    </div>
    {memory_html}
  </section>

  <section class="view" id="view-profiles">
    <div class="pagehead">
      <div class="eyebrow">Profiles</div>
      <h1>Profiles</h1>
      <p>All {len(profiles)} profiles deployed in this home. Job chips show each cron's last status (ok / error / disabled).</p>
    </div>
    {profiles_html}
  </section>

  <section class="view" id="view-cron">
    <div class="pagehead">
      <div class="eyebrow">Cron health</div>
      <h1>Cron health</h1>
      <p>Cron911: silent gaps, failures and dead models — merged to one row per job so one root cause does not flood the board.</p>
    </div>
    {cron_html}
  </section>

  <footer class="line">generated by TeamOps // automatedagents.co · <code>hermes teams status --html</code> ·
    buttons drive Hermes from inside the app (toast otherwise)</footer>
</main>

<div class="toast" id="toast">
  <b>Action ready</b> — this view runs actions against the live server when open in a browser; in the Hermes app it sends the playbook to Hermes instead.
  <div class="msg" id="toast-msg"></div>
  <div class="cmd" id="toast-cmd"></div>
  <div class="row">
    <button id="toast-copy">Copy prompt</button>
    <button id="toast-close">Close</button>
  </div>
</div>
<script>window.__RUNS__ = {runs_json};</script>
<script>{_JS}</script>
<script>{_RUN_JS}</script>
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
    build_id = f"{len(data.get('profiles', []))}p-{len(data.get('teams', {}))}t-{datetime.now(timezone.utc).strftime('%H%M')}"
    text = render_html(data, title=title, build_id=build_id,
                       theme=load_hermes_theme(home))
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

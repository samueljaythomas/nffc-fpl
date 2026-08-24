#!/usr/bin/env python3
"""
Builds index.html - scoreboard page for a Draft FPL league.
Run by GitHub Actions; served by GitHub Pages.

Uses crest.png from the repo root if present, otherwise falls back to a
built-in tree mark, so the page never breaks if the image is missing.
"""

import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

LEAGUE_ID = int(os.environ.get("LEAGUE_ID", "6206"))
TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/Chicago"))
SITE_URL = os.environ.get("SITE_URL", "https://samueljaythomas.github.io/nffc-fpl/")

# If you saved a .jpg instead of a .png, change this one line.
CREST_FILE = "crest.jpeg"

SEGMENTS = [
    ("Segment 1", 1, 10),
    ("Segment 2", 11, 20),
    ("Segment 3", 21, 30),
    ("Segment 4", 31, 38),
]

BASE = "https://draft.premierleague.com/api"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; fpl-scoreboard/1.0)",
    "Accept": "application/json",
})


def get(path):
    r = session.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


details = get(f"/league/{LEAGUE_ID}/details")
league_name = details["league"]["name"]
names = {e["id"]: e["entry_name"] for e in details["league_entries"]}
managers = {e["id"]: f"{e['player_first_name']} {e['player_last_name']}"
            for e in details["league_entries"]}

weekly = defaultdict(dict)
finished = set()
h2h = defaultdict(lambda: [0, 0, 0])          # entry -> [W, D, L]

for m in details["matches"]:
    if not m.get("started"):
        continue
    gw = m["event"]
    a, b = m["league_entry_1"], m["league_entry_2"]
    pa, pb = m["league_entry_1_points"], m["league_entry_2_points"]
    weekly[gw][a] = pa
    weekly[gw][b] = pb
    if m.get("finished"):
        finished.add(gw)
    if pa > pb:
        h2h[a][0] += 1
        h2h[b][2] += 1
    elif pb > pa:
        h2h[b][0] += 1
        h2h[a][2] += 1
    else:
        h2h[a][1] += 1
        h2h[b][1] += 1

played = sorted(weekly)
latest = max(played) if played else 0
all_scores = [p for gw in played for p in weekly[gw].values()]
peak = max(all_scores) if all_scores else 1


def totals_for(start, end):
    gws = [gw for gw in played if start <= gw <= end]
    totals = defaultdict(int)
    for gw in gws:
        for entry, pts in weekly[gw].items():
            totals[entry] += pts
    return totals, gws


def ranked(totals):
    ordered = sorted(totals.items(), key=lambda kv: -kv[1])
    out, last_pts, last_rank = [], None, 0
    for i, (entry, pts) in enumerate(ordered, start=1):
        rank = last_rank if pts == last_pts else i
        out.append((rank, entry, pts))
        last_pts, last_rank = pts, rank
    return out


def form_bars(entry, window=5):
    gws = played[-window:]
    if len(gws) < 2:
        return ""
    bars = []
    for gw in gws:
        pts = weekly[gw].get(entry, 0)
        h = max(8, round(26 * pts / peak))
        bars.append(f'<i style="height:{h}px" title="GW{gw}: {pts}"></i>')
    return f'<div class="form">{"".join(bars)}</div>'


def rows_html(table, gw_count, sub_override=None, show_form=False):
    out = []
    for rank, entry, pts in table:
        lead = " lead" if rank == 1 else ""
        sub = sub_override or (f"{pts / gw_count:.1f}/gw" if gw_count else "")
        rec = h2h[entry]
        meta = managers[entry]
        if show_form and sum(rec):
            meta += f" &middot; {rec[0]}-{rec[1]}-{rec[2]}"
        out.append(
            f'<div class="row{lead}">'
            f'<div class="rank">{rank}</div>'
            f'<div class="who"><div class="team">{names[entry]}</div>'
            f'<div class="mgr">{meta}</div></div>'
            f'{form_bars(entry) if show_form else ""}'
            f'<div class="val"><div class="pts">{pts}</div>'
            f'<div class="sub">{sub}</div></div>'
            f'</div>'
        )
    return "".join(out)


def segment_block(label, start, end, is_current):
    totals, gws = totals_for(start, end)
    span = end - start + 1

    if not gws:
        return (f'<section class="card">'
                f'<div class="head"><span class="badge muted">UPCOMING</span>'
                f'<h2>{label}</h2><p>GW{start}&ndash;{end}</p></div></section>')

    complete = len(gws) == span
    badge = "FINAL" if complete else f"{len(gws)}/{span}"
    badge_cls = "done" if complete else "live"
    table = ranked(totals)

    note = ""
    pending = [g for g in gws if g not in finished]
    if pending:
        note = ('<div class="note warn">GW'
                + ", GW".join(str(g) for g in pending)
                + " not finalised &mdash; scores may still change</div>")
    elif complete:
        winners = " &amp; ".join(names[e] for r, e, p in table if r == 1)
        note = f'<div class="note">&#127942; Award: <b>{winners}</b></div>'

    cur = " current" if is_current else ""
    return (f'<section class="card{cur}">'
            f'<div class="head"><span class="badge {badge_cls}">{badge}</span>'
            f'<h2>{label}</h2><p>GW{start}&ndash;{end}</p></div>'
            f'{rows_html(table, len(gws), show_form=is_current)}{note}</section>')


blocks = []
current = next((s for s in SEGMENTS if s[1] <= latest <= s[2]), None)
for seg in SEGMENTS:
    blocks.append(segment_block(seg[0], seg[1], seg[2], seg is current))

wins = defaultdict(int)
for label, start, end in SEGMENTS:
    totals, gws = totals_for(start, end)
    if gws and len(gws) == (end - start + 1):
        for r, e, p in ranked(totals):
            if r == 1:
                wins[e] += 1
if wins:
    blocks.append(f'<section class="card"><div class="head">'
                  f'<h2>Segment titles</h2><p>Completed blocks only</p></div>'
                  f'{rows_html(ranked(wins), 0, sub_override="titles")}</section>')

top_weeks = defaultdict(int)
for gw in played:
    if not weekly[gw]:
        continue
    best = max(weekly[gw].values())
    for entry, pts in weekly[gw].items():
        if pts == best:
            top_weeks[entry] += 1
if top_weeks and len(played) > 1:
    blocks.append(f'<section class="card"><div class="head">'
                  f'<h2>Weekly high scores</h2><p>Times top of the pile</p></div>'
                  f'{rows_html(ranked(top_weeks), 0, sub_override="weeks")}</section>')

if played:
    season, gws = totals_for(1, 38)
    blocks.append(f'<section class="card"><div class="head">'
                  f'<span class="badge muted">ALL</span>'
                  f'<h2>Season total</h2><p>Through GW{latest}</p></div>'
                  f'{rows_html(ranked(season), len(gws))}</section>')

    best_gw = max((p, e, gw) for gw in played for e, p in weekly[gw].items())
    worst_gw = min((p, e, gw) for gw in played for e, p in weekly[gw].items())
    blocks.append(
        f'<section class="card"><div class="head"><h2>Season records</h2>'
        f'<p>Single gameweek</p></div>'
        f'<div class="rec"><div><span>Highest</span>'
        f'<b>{best_gw[0]}</b><em>{names[best_gw[1]]} &middot; GW{best_gw[2]}</em></div>'
        f'<div><span>Lowest</span>'
        f'<b>{worst_gw[0]}</b><em>{names[worst_gw[1]]} &middot; GW{worst_gw[2]}</em></div>'
        f'</div></section>'
    )

updated = datetime.now(TZ).strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
leader = ranked(totals_for(1, 38)[0])[0] if played else None
share_desc = (f"{names[leader[1]]} leads on {leader[2]} points through GW{latest}"
              if leader else "Season starting soon")

# ---- branding: crest if uploaded, tree mark if not ----
TREE = (
    '<svg viewBox="0 0 40 44" width="36" height="40" aria-hidden="true">'
    '<path d="M20 2 L30 16 H24 L33 29 H25 L20 24 L15 29 H7 L16 16 H10 Z" '
    'fill="currentColor"/>'
    '<rect x="18" y="28" width="4" height="14" rx="1.4" fill="currentColor"/>'
    '<path d="M8 42 H32" stroke="currentColor" stroke-width="3.4" '
    'stroke-linecap="round"/></svg>'
)
FALLBACK_ICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 44'%3E"
    "%3Crect width='40' height='44' fill='%230b0d10'/%3E"
    "%3Cpath d='M20 4 L29 17 H24 L32 29 H24 L20 25 L16 29 H8 L16 17 H11 Z' "
    "fill='%23e23539'/%3E%3C/svg%3E"
)

has_crest = os.path.exists(CREST_FILE)
if has_crest:
    mark = f'<img class="crest" src="{CREST_FILE}" alt="">'
    icon = CREST_FILE
    og_image = f'<meta property="og:image" content="{SITE_URL.rstrip("/")}/{CREST_FILE}">'
    print(f"Using {CREST_FILE}")
else:
    mark = TREE
    icon = FALLBACK_ICON
    og_image = ""
    print(f"{CREST_FILE} not found - using built-in tree mark")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{league_name} &middot; Scoreboard</title>
<meta name="theme-color" content="#0b0d10">
<meta property="og:title" content="{league_name} &middot; Scoreboard">
<meta property="og:description" content="{share_desc}">
<meta property="og:type" content="website">
{og_image}
<meta name="description" content="{share_desc}">
<link rel="icon" href="{icon}">
<link rel="apple-touch-icon" href="{icon}">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 22px 14px 46px;
    background:
      radial-gradient(900px 380px at 50% -160px, #3a0a0e 0%, transparent 70%),
      #0b0d10;
    color: #f4f6f9;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 460px; margin: 0 auto; }}
  .brand {{ display: flex; align-items: center; gap: 13px; }}
  .brand .mark {{ color: #e23539; flex: none; line-height: 0; }}
  .crest {{
    display: block; height: 42px; width: auto; max-width: 52px;
    object-fit: contain; background: #fff;
    padding: 4px; border-radius: 9px;
  }}
  .title {{
    font-size: 27px; font-weight: 800; letter-spacing: -.025em; line-height: 1;
  }}
  .stamp {{ font-size: 12px; color: #8a93a6; margin: 10px 0 22px; }}
  .stamp b {{ color: #e23539; font-weight: 600; }}
  .card {{
    background: #14171d; border: 1px solid #262b36;
    border-radius: 16px; overflow: hidden; margin-bottom: 15px;
  }}
  .card.current {{
    border-color: #e23539;
    box-shadow: 0 0 0 1px rgba(226,53,57,.22), 0 10px 30px -18px #e23539;
  }}
  .head {{ padding: 15px 17px 12px; border-bottom: 1px solid #262b36; }}
  .head h2 {{ margin: 0; font-size: 18px; font-weight: 700; letter-spacing: -.01em; }}
  .head p {{ margin: 3px 0 0; font-size: 12px; color: #8a93a6; }}
  .badge {{
    float: right; font-size: 10px; font-weight: 800; letter-spacing: .07em;
    padding: 4px 9px; border-radius: 20px; color: #0b0d10;
  }}
  .badge.done {{ background: #e23539; color: #fff; }}
  .badge.live {{ background: #f0b429; }}
  .badge.muted {{ background: #333a48; color: #c6cddb; }}
  .row {{
    display: flex; align-items: center; gap: 11px;
    padding: 12px 16px 12px 13px; border-bottom: 1px solid #1e222b;
    border-left: 3px solid transparent;
  }}
  .row:last-of-type {{ border-bottom: none; }}
  .row.lead {{
    border-left-color: #e23539;
    background: linear-gradient(90deg, rgba(226,53,57,.13), transparent 62%);
  }}
  .rank {{ width: 19px; font-size: 15px; font-weight: 700; color: #8a93a6; }}
  .row.lead .rank {{ color: #e23539; }}
  .who {{ flex: 1; min-width: 0; }}
  .team {{
    font-size: 14.5px; font-weight: 600;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .mgr {{ font-size: 11px; color: #8a93a6; margin-top: 1px; }}
  .form {{ display: flex; align-items: flex-end; gap: 2px; height: 26px; }}
  .form i {{ width: 5px; background: #3d4553; border-radius: 1.5px; }}
  .row.lead .form i {{ background: #7a2c30; }}
  .form i:last-child {{ background: #e23539; }}
  .val {{ text-align: right; min-width: 46px; }}
  .pts {{ font-size: 19px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .sub {{ font-size: 10px; color: #8a93a6; }}
  .rec {{ display: flex; }}
  .rec > div {{ flex: 1; padding: 14px 17px; }}
  .rec > div + div {{ border-left: 1px solid #262b36; }}
  .rec span {{
    display: block; font-size: 10px; letter-spacing: .08em;
    text-transform: uppercase; color: #8a93a6;
  }}
  .rec b {{ display: block; font-size: 26px; font-weight: 800; margin: 3px 0 2px; }}
  .rec em {{ font-style: normal; font-size: 11px; color: #8a93a6; }}
  .note {{ padding: 11px 17px; font-size: 12px; color: #8a93a6; }}
  .note b {{ color: #f4f6f9; }}
  .note.warn {{ color: #f0b429; }}
  footer {{ text-align: center; font-size: 11px; color: #4e576b; margin-top: 26px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand"><span class="mark">{mark}</span>
    <span class="title">{league_name}</span></div>
  <div class="stamp">Consistency tracker &middot; updated <b>{updated}</b></div>
  {''.join(blocks)}
  <footer>Auto-updates daily &middot; data from the official FPL Draft API</footer>
</div>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"Built index.html for {league_name} through GW{latest}")

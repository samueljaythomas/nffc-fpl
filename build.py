#!/usr/bin/env python3
"""
Builds index.html - scoreboard page for a Draft FPL league.
Run by GitHub Actions; served by GitHub Pages.

Three tabs: Segments, Gameweek (with a picker), Season.
Uses CREST_FILE from the repo root if present, else a built-in tree mark.
"""

import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

LEAGUE_ID = int(os.environ.get("LEAGUE_ID", "6206"))
TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/Chicago"))
SITE_URL = os.environ.get("SITE_URL", "https://samueljaythomas.github.io/nffc-fpl/")

# Must match the filename in your repo exactly, including capitals.
CREST_FILE = "IMG_8605.jpeg"

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

weekly = defaultdict(dict)          # gw -> {entry: points}
fixtures = defaultdict(list)        # gw -> [(a, pa, b, pb, started, finished)]
finished = set()
h2h = defaultdict(lambda: [0, 0, 0])

for m in details["matches"]:
    gw = m["event"]
    a, b = m["league_entry_1"], m["league_entry_2"]
    pa, pb = m["league_entry_1_points"], m["league_entry_2_points"]
    started = bool(m.get("started"))
    fixtures[gw].append((a, pa, b, pb, started, bool(m.get("finished"))))

    if not started:
        continue
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


# ================= TAB 1: SEGMENTS =================
seg_blocks = []
current = next((s for s in SEGMENTS if s[1] <= latest <= s[2]), None)
for seg in SEGMENTS:
    seg_blocks.append(segment_block(seg[0], seg[1], seg[2], seg is current))

wins = defaultdict(int)
for label, start, end in SEGMENTS:
    totals, gws = totals_for(start, end)
    if gws and len(gws) == (end - start + 1):
        for r, e, p in ranked(totals):
            if r == 1:
                wins[e] += 1
if wins:
    seg_blocks.append(f'<section class="card"><div class="head">'
                      f'<h2>Segment titles</h2><p>Completed blocks only</p></div>'
                      f'{rows_html(ranked(wins), 0, sub_override="titles")}</section>')


# ================= TAB 2: GAMEWEEK =================
def fixture_html(a, pa, b, pb, started):
    if not started:
        return (f'<div class="fx up">'
                f'<div class="side"><span>{names[a]}</span></div>'
                f'<div class="vs">v</div>'
                f'<div class="side r"><span>{names[b]}</span></div></div>')
    aw = " win" if pa > pb else ""
    bw = " win" if pb > pa else ""
    return (f'<div class="fx">'
            f'<div class="side{aw}"><span>{names[a]}</span><b>{pa}</b></div>'
            f'<div class="vs">&ndash;</div>'
            f'<div class="side r{bw}"><b>{pb}</b><span>{names[b]}</span></div></div>')


gw_options, gw_panels = [], []
selectable = played + ([latest + 1] if (latest + 1) in fixtures else [])

for gw in selectable:
    is_future = gw not in weekly
    label = f"Gameweek {gw}" + (" (upcoming)" if is_future else "")
    sel = " selected" if gw == latest else ""
    gw_options.append(f'<option value="{gw}"{sel}>{label}</option>')

    fx = "".join(fixture_html(a, pa, b, pb, st)
                 for a, pa, b, pb, st, fin in fixtures[gw])

    if is_future:
        body = (f'<section class="card"><div class="head">'
                f'<span class="badge muted">UPCOMING</span>'
                f'<h2>Gameweek {gw}</h2><p>Fixtures</p></div>{fx}</section>')
    else:
        scores = ranked({e: p for e, p in weekly[gw].items()})
        top = scores[0][2]
        winners = " &amp; ".join(names[e] for r, e, p in scores if r == 1)
        note = ""
        if gw not in finished:
            note = ('<div class="note warn">Not finalised &mdash; '
                    'scores may still change</div>')
        avg = sum(weekly[gw].values()) / len(weekly[gw])
        body = (
            f'<section class="card current"><div class="head">'
            f'<span class="badge {"live" if gw not in finished else "done"}">'
            f'{"LIVE" if gw not in finished else "FINAL"}</span>'
            f'<h2>Gameweek {gw}</h2><p>Results</p></div>{fx}{note}</section>'
            f'<section class="card"><div class="head"><h2>Standings</h2>'
            f'<p>Points scored this week</p></div>'
            f'{rows_html(scores, 0, sub_override="pts")}</section>'
            f'<div class="rec2"><div><span>Top score</span><b>{top}</b>'
            f'<em>{winners}</em></div>'
            f'<div><span>League average</span><b>{avg:.1f}</b>'
            f'<em>across {len(weekly[gw])} teams</em></div></div>'
        )

    gw_panels.append(f'<div class="gwpanel" data-gw="{gw}">{body}</div>')

if selectable:
    gw_tab = (f'<div class="picker"><select id="gwpick">'
              f'{"".join(reversed(gw_options))}</select></div>'
              f'{"".join(gw_panels)}')
else:
    gw_tab = ('<section class="card"><div class="head">'
              '<h2>No results yet</h2><p>Check back after GW1</p></div></section>')


# ================= TAB 3: SEASON =================
season_blocks = []
if played:
    season, gws = totals_for(1, 38)
    season_blocks.append(f'<section class="card"><div class="head">'
                         f'<span class="badge muted">ALL</span>'
                         f'<h2>Season total</h2><p>Through GW{latest}</p></div>'
                         f'{rows_html(ranked(season), len(gws), show_form=True)}</section>')

    top_weeks = defaultdict(int)
    for gw in played:
        best = max(weekly[gw].values())
        for entry, pts in weekly[gw].items():
            if pts == best:
                top_weeks[entry] += 1
    if len(played) > 1:
        season_blocks.append(
            f'<section class="card"><div class="head">'
            f'<h2>Weekly high scores</h2><p>Times top of the pile</p></div>'
            f'{rows_html(ranked(top_weeks), 0, sub_override="weeks")}</section>')

    best_gw = max((p, e, gw) for gw in played for e, p in weekly[gw].items())
    season_blocks.append(
        f'<section class="card"><div class="head"><h2>Season record</h2>'
        f'<p>Best single gameweek</p></div>'
        f'<div class="rec"><div><span>Highest score</span>'
        f'<b>{best_gw[0]}</b>'
        f'<em>{names[best_gw[1]]} &middot; GW{best_gw[2]}</em></div></div></section>')
else:
    season_blocks.append('<section class="card"><div class="head">'
                         '<h2>Season not started</h2></div></section>')


# ================= PAGE =================
updated = datetime.now(TZ).strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
leader = ranked(totals_for(1, 38)[0])[0] if played else None
share_desc = (f"{names[leader[1]]} leads on {leader[2]} points through GW{latest}"
              if leader else "Season starting soon")

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

if os.path.exists(CREST_FILE):
    mark = f'<img class="crest" src="{CREST_FILE}" alt="">'
    icon = CREST_FILE
    og_image = f'<meta property="og:image" content="{SITE_URL.rstrip("/")}/{CREST_FILE}">'
    print(f"Using {CREST_FILE}")
else:
    mark = TREE
    icon = FALLBACK_ICON
    og_image = ""
    print(f"{CREST_FILE} not found - using built-in tree mark")

SCRIPT = """
<script>
(function () {
  var tabs = document.querySelectorAll('.tab');
  var panes = document.querySelectorAll('.pane');

  function show(name) {
    tabs.forEach(function (t) {
      t.classList.toggle('on', t.dataset.tab === name);
    });
    panes.forEach(function (p) {
      p.classList.toggle('on', p.dataset.pane === name);
    });
    try { history.replaceState(null, '', '#' + name); } catch (e) {}
    window.scrollTo(0, 0);
  }

  tabs.forEach(function (t) {
    t.addEventListener('click', function () { show(t.dataset.tab); });
  });

  var pick = document.getElementById('gwpick');
  if (pick) {
    var panels = document.querySelectorAll('.gwpanel');
    function showGw(gw) {
      panels.forEach(function (p) {
        p.classList.toggle('on', p.dataset.gw === String(gw));
      });
    }
    pick.addEventListener('change', function () { showGw(pick.value); });
    showGw(pick.value);
  }

  var start = (location.hash || '').replace('#', '');
  show(['segments', 'gameweek', 'season'].indexOf(start) >= 0 ? start : 'segments');
})();
</script>
"""

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
    object-fit: contain; background: #fff; padding: 4px; border-radius: 9px;
  }}
  .title {{
    font-size: 27px; font-weight: 800; letter-spacing: -.025em; line-height: 1;
  }}
  .stamp {{ font-size: 12px; color: #8a93a6; margin: 10px 0 16px; }}
  .stamp b {{ color: #e23539; font-weight: 600; }}

  .tabs {{
    display: flex; gap: 5px; background: #14171d; border: 1px solid #262b36;
    border-radius: 12px; padding: 4px; margin-bottom: 16px;
  }}
  .tab {{
    flex: 1; text-align: center; padding: 9px 4px; border-radius: 9px;
    font-size: 13px; font-weight: 600; color: #8a93a6;
    border: none; background: none; cursor: pointer; font-family: inherit;
    -webkit-tap-highlight-color: transparent;
  }}
  .tab.on {{ background: #e23539; color: #fff; }}
  .pane {{ display: none; }}
  .pane.on {{ display: block; }}

  .picker {{ position: relative; margin-bottom: 14px; }}
  .picker::after {{
    content: ""; position: absolute; right: 16px; top: 50%;
    width: 8px; height: 8px; border-right: 2px solid #8a93a6;
    border-bottom: 2px solid #8a93a6; transform: translateY(-70%) rotate(45deg);
    pointer-events: none;
  }}
  #gwpick {{
    width: 100%; appearance: none; -webkit-appearance: none;
    background: #14171d; border: 1px solid #262b36; border-radius: 12px;
    color: #f4f6f9; font-size: 15px; font-weight: 600; font-family: inherit;
    padding: 13px 40px 13px 15px;
  }}
  .gwpanel {{ display: none; }}
  .gwpanel.on {{ display: block; }}

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

  .fx {{
    display: flex; align-items: center; gap: 8px;
    padding: 13px 15px; border-bottom: 1px solid #1e222b;
  }}
  .fx:last-of-type {{ border-bottom: none; }}
  .side {{
    flex: 1; display: flex; align-items: center; gap: 9px; min-width: 0;
  }}
  .side.r {{ justify-content: flex-end; }}
  .side span {{
    font-size: 13.5px; color: #8a93a6;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .side b {{
    font-size: 18px; font-weight: 700; color: #8a93a6;
    font-variant-numeric: tabular-nums; flex: none;
  }}
  .side.win span {{ color: #f4f6f9; font-weight: 600; }}
  .side.win b {{ color: #e23539; }}
  .vs {{ font-size: 11px; color: #4e576b; flex: none; }}
  .fx.up .side span {{ color: #c6cddb; }}

  .rec {{ display: flex; }}
  .rec > div {{ flex: 1; padding: 14px 17px; }}
  .rec span {{
    display: block; font-size: 10px; letter-spacing: .08em;
    text-transform: uppercase; color: #8a93a6;
  }}
  .rec b {{ display: block; font-size: 26px; font-weight: 800; margin: 3px 0 2px; }}
  .rec em {{ font-style: normal; font-size: 11px; color: #8a93a6; }}
  .rec2 {{ display: flex; gap: 12px; margin-bottom: 15px; }}
  .rec2 > div {{
    flex: 1; background: #14171d; border: 1px solid #262b36;
    border-radius: 14px; padding: 13px 15px;
  }}
  .rec2 span {{
    display: block; font-size: 10px; letter-spacing: .07em;
    text-transform: uppercase; color: #8a93a6;
  }}
  .rec2 b {{ display: block; font-size: 24px; font-weight: 800; margin: 3px 0 2px; }}
  .rec2 em {{
    font-style: normal; font-size: 11px; color: #8a93a6;
    display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}

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

  <div class="tabs">
    <button class="tab on" data-tab="segments">Segments</button>
    <button class="tab" data-tab="gameweek">Gameweek</button>
    <button class="tab" data-tab="season">Season</button>
  </div>

  <div class="pane on" data-pane="segments">{''.join(seg_blocks)}</div>
  <div class="pane" data-pane="gameweek">{gw_tab}</div>
  <div class="pane" data-pane="season">{''.join(season_blocks)}</div>

  <footer>Auto-updates daily &middot; data from the official FPL Draft API</footer>
</div>
{SCRIPT}
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"Built index.html for {league_name} through GW{latest}")

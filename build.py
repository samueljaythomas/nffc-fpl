#!/usr/bin/env python3
"""
Builds index.html - scoreboard page for a Draft FPL league.
Run by GitHub Actions; served by GitHub Pages.

Tabs: Segments, Gameweek, Season, Analysis, Trends.
Everything is derived from one /league/{id}/details call.
"""

import json
import os
import statistics
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
results = defaultdict(dict)         # gw -> {entry: 'W'|'D'|'L'}
against = defaultdict(int)          # entry -> points conceded
grid = defaultdict(lambda: defaultdict(int))   # a -> b -> wins over b
finished = set()
h2h = defaultdict(lambda: [0, 0, 0])
decided = []                        # (margin, gw, winner, loser, pw, pl)
drawn = []                          # (gw, a, b, pts)

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
    against[a] += pb
    against[b] += pa
    if m.get("finished"):
        finished.add(gw)

    if pa > pb:
        h2h[a][0] += 1
        h2h[b][2] += 1
        results[gw][a], results[gw][b] = "W", "L"
        grid[a][b] += 1
        decided.append((pa - pb, gw, a, b, pa, pb))
    elif pb > pa:
        h2h[b][0] += 1
        h2h[a][2] += 1
        results[gw][a], results[gw][b] = "L", "W"
        grid[b][a] += 1
        decided.append((pb - pa, gw, b, a, pb, pa))
    else:
        h2h[a][1] += 1
        h2h[b][1] += 1
        results[gw][a], results[gw][b] = "D", "D"
        drawn.append((gw, a, b, pa))

played = sorted(weekly)
latest = max(played) if played else 0
entries = list(names)
all_scores = [p for gw in played for p in weekly[gw].values()]
peak = max(all_scores) if all_scores else 1


def totals_for(start, end):
    gws = [gw for gw in played if start <= gw <= end]
    totals = defaultdict(int)
    for gw in gws:
        for entry, pts in weekly[gw].items():
            totals[entry] += pts
    return totals, gws


def ranked(totals, reverse=False):
    ordered = sorted(totals.items(), key=lambda kv: kv[1] if reverse else -kv[1])
    out, last_val, last_rank = [], None, 0
    for i, (entry, val) in enumerate(ordered, start=1):
        rank = last_rank if val == last_val else i
        out.append((rank, entry, val))
        last_val, last_rank = val, rank
    return out


# ---------- derived season stats ----------
allplay = defaultdict(lambda: [0, 0, 0])
for gw in played:
    scores = weekly[gw]
    for entry, pts in scores.items():
        for other, opts in scores.items():
            if other == entry:
                continue
            if pts > opts:
                allplay[entry][0] += 1
            elif pts < opts:
                allplay[entry][2] += 1
            else:
                allplay[entry][1] += 1

allplay_pct, luck = {}, {}
for entry in entries:
    aw, ad, al = allplay[entry]
    n = aw + ad + al
    allplay_pct[entry] = (aw + 0.5 * ad) / n if n else 0
    rw, rd, rl = h2h[entry]
    rn = rw + rd + rl
    real_pct = (rw + 0.5 * rd) / rn if rn else 0
    luck[entry] = real_pct - allplay_pct[entry]

spread = {}
for entry in entries:
    vals = [weekly[gw][entry] for gw in played if entry in weekly[gw]]
    spread[entry] = statistics.pstdev(vals) if len(vals) > 1 else 0.0

# streaks
cur_streak, best_run = {}, {}
for entry in entries:
    seq = [results[gw].get(entry) for gw in played if entry in results[gw]]
    run, kind, best = 0, None, 0
    for res in seq:
        if res == "W":
            run = run + 1 if kind == "W" else 1
            kind = "W"
            best = max(best, run)
        elif res == "L":
            run = run + 1 if kind == "L" else 1
            kind = "L"
        else:
            run, kind = 1, "D"
    cur_streak[entry] = (kind, run) if seq else (None, 0)
    best_run[entry] = best


# ---------- row builders ----------
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


def custom_rows(items):
    """items: list of (rank, entry, meta_html, value_html, sub_html)"""
    out = []
    for rank, entry, meta, val, sub in items:
        lead = " lead" if rank == 1 else ""
        out.append(
            f'<div class="row{lead}">'
            f'<div class="rank">{rank}</div>'
            f'<div class="who"><div class="team">{names[entry]}</div>'
            f'<div class="mgr">{meta}</div></div>'
            f'<div class="val"><div class="pts">{val}</div>'
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
        scores = ranked(dict(weekly[gw]))
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

    tiles = []
    best_gw = max((p, e, gw) for gw in played for e, p in weekly[gw].items())
    tiles.append(f'<div><span>Highest score</span><b>{best_gw[0]}</b>'
                 f'<em>{names[best_gw[1]]} &middot; GW{best_gw[2]}</em></div>')

    if decided:
        mx = max(decided)
        tiles.append(f'<div><span>Biggest win</span><b>+{mx[0]}</b>'
                     f'<em>{names[mx[2]]} {mx[4]}&ndash;{mx[5]} '
                     f'{names[mx[3]]} &middot; GW{mx[1]}</em></div>')
        mn = min(decided)
        tiles.append(f'<div><span>Closest match</span><b>+{mn[0]}</b>'
                     f'<em>{names[mn[2]]} {mn[4]}&ndash;{mn[5]} '
                     f'{names[mn[3]]} &middot; GW{mn[1]}</em></div>')
    if drawn:
        d = drawn[-1]
        tiles.append(f'<div><span>Latest draw</span><b>{d[3]}</b>'
                     f'<em>{names[d[1]]} v {names[d[2]]} &middot; GW{d[0]}</em></div>')

    top_run = max(best_run.values()) if best_run else 0
    if top_run > 1:
        holders = " &amp; ".join(names[e] for e in entries if best_run[e] == top_run)
        tiles.append(f'<div><span>Longest win run</span><b>{top_run}</b>'
                     f'<em>{holders}</em></div>')

    season_blocks.append(
        f'<section class="card"><div class="head"><h2>Season records</h2>'
        f'<p>Notable performances</p></div>'
        f'<div class="tiles">{"".join(tiles)}</div></section>')
else:
    season_blocks.append('<section class="card"><div class="head">'
                         '<h2>Season not started</h2></div></section>')


# ================= TAB 4: ANALYSIS =================
ana_blocks = []
if played:
    ap_items = []
    for rank, entry, pct in ranked(allplay_pct):
        aw, ad, al = allplay[entry]
        rw, rd, rl = h2h[entry]
        lk = luck[entry]
        cls = "up" if lk > 0.001 else ("down" if lk < -0.001 else "flat")
        meta = (f'Table {rw}-{rd}-{rl} &middot; '
                f'<b class="{cls}">luck {lk:+.2f}</b>')
        ap_items.append((rank, entry, meta, f"{pct:.3f}".lstrip("0"),
                         f"{aw}-{ad}-{al}"))
    ana_blocks.append(
        f'<section class="card"><div class="head">'
        f'<h2>All-play table</h2><p>Scored against everyone, every week</p></div>'
        f'{custom_rows(ap_items)}'
        f'<div class="note">Win rate if you played all seven rivals each week. '
        f'<b>Luck</b> is the gap between the real table and this one &mdash; '
        f'green means the schedule has been kind.</div></section>')

    pf_items = []
    season_totals = totals_for(1, 38)[0]
    for rank, entry, pa in ranked(against, reverse=True):
        pf = season_totals[entry]
        pf_items.append((rank, entry, f'Scored {pf}', str(pa), "conceded"))
    ana_blocks.append(
        f'<section class="card"><div class="head">'
        f'<h2>Points against</h2><p>What opponents scored on you</p></div>'
        f'{custom_rows(pf_items)}'
        f'<div class="note">Sorted by lightest schedule first.</div></section>')

    if len(played) > 2:
        con_items = []
        for rank, entry, sd in ranked(spread, reverse=True):
            vals = [weekly[gw][entry] for gw in played if entry in weekly[gw]]
            con_items.append((rank, entry,
                              f'Range {min(vals)}&ndash;{max(vals)}',
                              f"{sd:.1f}", "std dev"))
        ana_blocks.append(
            f'<section class="card"><div class="head">'
            f'<h2>Consistency</h2><p>Lower is steadier</p></div>'
            f'{custom_rows(con_items)}'
            f'<div class="note">Standard deviation of weekly scores. '
            f'A low number means predictable output week to week &mdash; '
            f'which is what the segment awards are really rewarding.</div></section>')

    st_items = []
    order = sorted(entries, key=lambda e: (
        0 if cur_streak[e][0] == "W" else (1 if cur_streak[e][0] == "D" else 2),
        -cur_streak[e][1]))
    for i, entry in enumerate(order, start=1):
        kind, run = cur_streak[entry]
        cls = "up" if kind == "W" else ("down" if kind == "L" else "flat")
        label = f'{kind}{run}' if kind else "&ndash;"
        st_items.append((i, entry, f'Best run {best_run[entry]}W',
                         f'<span class="{cls}">{label}</span>', "current"))
    ana_blocks.append(
        f'<section class="card"><div class="head">'
        f'<h2>Form streaks</h2><p>Current run of results</p></div>'
        f'{custom_rows(st_items)}</section>')

    # head-to-head grid
    idx = {e: i + 1 for i, e in enumerate(entries)}
    head = "".join(f'<th>{idx[e]}</th>' for e in entries)
    body = ""
    for a in entries:
        cells = ""
        for b in entries:
            if a == b:
                cells += '<td class="self">&middot;</td>'
                continue
            w, l = grid[a][b], grid[b][a]
            if w == 0 and l == 0:
                cells += '<td class="nil">&ndash;</td>'
            else:
                cls = "up" if w > l else ("down" if l > w else "flat")
                cells += f'<td class="{cls}">{w}</td>'
        body += (f'<tr><th class="rowlab">'
                 f'<span class="n">{idx[a]}</span>{names[a]}</th>{cells}</tr>')
    ana_blocks.append(
        f'<section class="card"><div class="head">'
        f'<h2>Head to head</h2><p>Wins against each rival</p></div>'
        f'<div class="gridwrap"><table class="h2h">'
        f'<tr><th class="rowlab"></th>{head}</tr>{body}</table></div>'
        f'<div class="note">Read across: the number is that row\'s wins over '
        f'the numbered column.</div></section>')
else:
    ana_blocks.append('<section class="card"><div class="head">'
                      '<h2>Nothing to analyse yet</h2>'
                      '<p>Check back after GW1</p></div></section>')


# ================= TAB 5: TRENDS =================
season_order = [e for r, e, p in ranked(totals_for(1, 38)[0])] if played else entries
team_options = "".join(f'<option value="{e}">{names[e]}</option>'
                       for e in season_order)
seg_options = "".join(
    f'<option value="seg:{i}"'
    f'{" selected" if current and SEGMENTS[i] is current else ""}>'
    f'{lab} &middot; GW{s}&ndash;{en}</option>'
    for i, (lab, s, en) in enumerate(SEGMENTS)
)
range_options = (
    '<option value="last:5">Last 5 gameweeks</option>'
    '<option value="last:10">Last 10 gameweeks</option>'
    '<option value="all">Full season</option>'
)
seg_select = (f'<optgroup label="Award segments">{seg_options}</optgroup>'
              f'<optgroup label="Rolling">{range_options}</optgroup>')

trends_tab = (
    f'<div class="picker"><select id="teampick">{team_options}</select></div>'
    f'<div class="picker"><select id="segpick">{seg_select}</select></div>'
    f'<section class="card"><div class="head">'
    f'<h2 id="charttitle">Scoring trend</h2>'
    f'<p id="chartsub">Weekly points</p></div>'
    f'<div id="chart"></div>'
    f'<div class="legend">'
    f'<span><i class="k-line"></i>Weekly score</span>'
    f'<span><i class="k-me"></i>Their average</span>'
    f'<span><i class="k-lg"></i>League average</span>'
    f'</div></section>'
    f'<div class="rec2" id="chartstats"></div>'
)

CHART_DATA = {
    "played": played,
    "weekly": {str(gw): {str(e): p for e, p in weekly[gw].items()} for gw in played},
    "segments": [[lab, s, e] for lab, s, e in SEGMENTS],
    "names": {str(e): names[e] for e in names},
}


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

APP_SHORT = os.environ.get("APP_SHORT", "NFFC FPL")
app_short = APP_SHORT

manifest = {
    "name": f"{league_name} Scoreboard",
    "short_name": APP_SHORT,
    "start_url": "./index.html",
    "scope": "./",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#0b0d10",
    "theme_color": "#0b0d10",
    "icons": [{"src": icon, "sizes": "any",
               "type": "image/jpeg" if icon.lower().endswith((".jpg", ".jpeg"))
               else "image/png"}] if os.path.exists(CREST_FILE) else [],
}
with open("manifest.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)

SCRIPT = """
<script>
(function () {
  var DATA = __CHART_DATA__;

  var tabs = document.querySelectorAll('.tab');
  var panes = document.querySelectorAll('.pane');
  var order = ['segments', 'gameweek', 'season', 'analysis', 'trends'];

  function show(name) {
    tabs.forEach(function (t) { t.classList.toggle('on', t.dataset.tab === name); });
    panes.forEach(function (p) { p.classList.toggle('on', p.dataset.pane === name); });
    try { history.replaceState(null, '', '#' + name); } catch (e) {}
    if (name === 'trends') draw();
    window.scrollTo(0, 0);
  }
  tabs.forEach(function (t) {
    t.addEventListener('click', function () { show(t.dataset.tab); });
  });

  /* ---- gameweek picker ---- */
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

  /* ---- trends chart ---- */
  var teamPick = document.getElementById('teampick');
  var segPick = document.getElementById('segpick');
  var host = document.getElementById('chart');
  var stats = document.getElementById('chartstats');
  var titleEl = document.getElementById('charttitle');
  var subEl = document.getElementById('chartsub');

  function mean(a) {
    if (!a.length) return 0;
    return a.reduce(function (x, y) { return x + y; }, 0) / a.length;
  }

  function resolveView(v) {
    if (v.indexOf('seg:') === 0) {
      var s = DATA.segments[+v.slice(4)];
      return {
        gws: DATA.played.filter(function (g) { return g >= s[1] && g <= s[2]; }),
        label: s[0], span: s[2] - s[1] + 1, kind: 'seg'
      };
    }
    if (v.indexOf('last:') === 0) {
      var n = +v.slice(5);
      return {
        gws: DATA.played.slice(-n),
        label: 'Last ' + n + ' gameweeks', span: n, kind: 'roll'
      };
    }
    return {
      gws: DATA.played.slice(),
      label: 'Full season', span: DATA.played.length, kind: 'all'
    };
  }

  function draw() {
    if (!host) return;
    var team = teamPick.value;
    var view = resolveView(segPick.value);
    var gws = view.gws;

    titleEl.textContent = DATA.names[team];
    subEl.textContent = gws.length
      ? view.label + ' \u00b7 GW' + gws[0] + '\u2013' + gws[gws.length - 1]
      : view.label;

    if (!gws.length) {
      host.innerHTML = '<div class="empty">No gameweeks played in this range yet</div>';
      stats.innerHTML = '';
      return;
    }

    var vals = gws.map(function (g) { return DATA.weekly[g][team]; });
    var lgAll = [];
    gws.forEach(function (g) {
      var wk = DATA.weekly[g];
      for (var k in wk) lgAll.push(wk[k]);
    });
    var myAvg = mean(vals), lgAvg = mean(lgAll);

    var W = 420, H = 235, L = 34, R = 12, T = 16, B = 28;
    var lo = Math.min.apply(null, vals.concat([myAvg, lgAvg]));
    var hi = Math.max.apply(null, vals.concat([myAvg, lgAvg]));
    var pad = Math.max(6, (hi - lo) * 0.25);
    lo = Math.max(0, Math.floor((lo - pad) / 5) * 5);
    hi = Math.ceil((hi + pad) / 5) * 5;
    if (hi === lo) hi = lo + 10;

    function X(i) {
      return gws.length === 1 ? (L + (W - L - R) / 2)
        : L + (W - L - R) * i / (gws.length - 1);
    }
    function Y(v) { return T + (H - T - B) * (1 - (v - lo) / (hi - lo)); }

    var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="chart">';

    for (var t = 0; t <= 4; t++) {
      var v = lo + (hi - lo) * t / 4, y = Y(v);
      s += '<line x1="' + L + '" y1="' + y + '" x2="' + (W - R) + '" y2="' + y +
           '" stroke="#232936" stroke-width="1"/>';
      s += '<text x="' + (L - 7) + '" y="' + (y + 3.5) +
           '" class="ax" text-anchor="end">' + Math.round(v) + '</text>';
    }

    s += '<line x1="' + L + '" y1="' + Y(lgAvg) + '" x2="' + (W - R) + '" y2="' +
         Y(lgAvg) + '" stroke="#7d879b" stroke-width="1.6" stroke-dasharray="5 4"/>';
    s += '<line x1="' + L + '" y1="' + Y(myAvg) + '" x2="' + (W - R) + '" y2="' +
         Y(myAvg) + '" stroke="#f0b429" stroke-width="1.6" stroke-dasharray="5 4"/>';

    if (gws.length > 1) {
      var d = vals.map(function (v, i) {
        return (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1);
      }).join(' ');
      s += '<path d="' + d + '" fill="none" stroke="#e23539" stroke-width="2.6" ' +
           'stroke-linejoin="round" stroke-linecap="round"/>';
    }

    var dense = gws.length > 12;
    vals.forEach(function (v, i) {
      s += '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(v).toFixed(1) +
           '" r="' + (dense ? 2.6 : 4) + '" fill="#0b0d10" stroke="#e23539" ' +
           'stroke-width="' + (dense ? 1.8 : 2.4) + '"/>';
      if (!dense) {
        s += '<text x="' + X(i).toFixed(1) + '" y="' + (Y(v) - 11).toFixed(1) +
             '" class="pt" text-anchor="middle">' + v + '</text>';
      }
    });

    var every = Math.ceil(gws.length / 8);
    gws.forEach(function (g, i) {
      if (i % every) return;
      s += '<text x="' + X(i).toFixed(1) + '" y="' + (H - 9) +
           '" class="ax" text-anchor="middle">' + g + '</text>';
    });

    s += '</svg>';
    host.innerHTML = s;

    var diff = myAvg - lgAvg;
    var sign = diff >= 0 ? '+' : '';
    var countTxt = view.kind === 'seg'
      ? gws.length + ' of ' + view.span + ' GWs'
      : gws.length + (gws.length === 1 ? ' gameweek' : ' gameweeks');
    stats.innerHTML =
      '<div><span>' + (view.kind === 'seg' ? 'Segment total' : 'Total') +
        '</span><b>' +
        vals.reduce(function (a, b) { return a + b; }, 0) +
        '</b><em>' + countTxt + '</em></div>' +
      '<div><span>Vs league avg</span><b class="' +
        (diff >= 0 ? 'up' : 'down') + '">' + sign + diff.toFixed(1) +
        '</b><em>' + myAvg.toFixed(1) + ' v ' + lgAvg.toFixed(1) + '</em></div>';
  }

  if (teamPick) {
    teamPick.addEventListener('change', draw);
    segPick.addEventListener('change', draw);
  }

  var reload = document.getElementById('reload');
  if (reload) {
    reload.addEventListener('click', function () {
      location.replace(location.pathname + '?t=' + Date.now() + location.hash);
    });
  }

  var start = (location.hash || '').replace('#', '');
  show(order.indexOf(start) >= 0 ? start : 'segments');
})();
</script>
"""
SCRIPT = SCRIPT.replace("__CHART_DATA__", json.dumps(CHART_DATA))

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{league_name} &middot; Scoreboard</title>
<meta name="theme-color" content="#0b0d10">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{app_short}">
<link rel="manifest" href="manifest.json">
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
    margin: 0;
    padding: calc(22px + env(safe-area-inset-top)) 14px
             calc(46px + env(safe-area-inset-bottom)) 14px;
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
  .stampbar {{
    display: flex; align-items: center; gap: 10px; margin: 10px 0 16px;
  }}
  .stamp {{ font-size: 12px; color: #8a93a6; flex: 1; min-width: 0; }}
  .stamp b {{ color: #e23539; font-weight: 600; }}
  .reload {{
    flex: none; width: 30px; height: 30px; border-radius: 9px;
    background: #14171d; border: 1px solid #262b36; color: #8a93a6;
    font-size: 16px; line-height: 1; cursor: pointer; font-family: inherit;
    -webkit-tap-highlight-color: transparent;
  }}
  .reload:active {{ color: #e23539; border-color: #e23539; }}

  .tabs {{
    display: flex; gap: 4px; background: #14171d; border: 1px solid #262b36;
    border-radius: 12px; padding: 4px; margin-bottom: 16px;
    overflow-x: auto; -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }}
  .tabs::-webkit-scrollbar {{ display: none; }}
  .tab {{
    flex: 1 0 auto; text-align: center; padding: 9px 11px; border-radius: 9px;
    font-size: 12.5px; font-weight: 600; color: #8a93a6; white-space: nowrap;
    border: none; background: none; cursor: pointer; font-family: inherit;
    -webkit-tap-highlight-color: transparent;
  }}
  .tab.on {{ background: #e23539; color: #fff; }}
  .pane {{ display: none; }}
  .pane.on {{ display: block; }}

  .picker {{ position: relative; margin-bottom: 11px; }}
  .picker::after {{
    content: ""; position: absolute; right: 16px; top: 50%;
    width: 8px; height: 8px; border-right: 2px solid #8a93a6;
    border-bottom: 2px solid #8a93a6; transform: translateY(-70%) rotate(45deg);
    pointer-events: none;
  }}
  .picker select {{
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
  .mgr {{
    font-size: 11px; color: #8a93a6; margin-top: 1px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .mgr b {{ font-weight: 600; }}
  .form {{ display: flex; align-items: flex-end; gap: 2px; height: 26px; }}
  .form i {{ width: 5px; background: #3d4553; border-radius: 1.5px; }}
  .row.lead .form i {{ background: #7a2c30; }}
  .form i:last-child {{ background: #e23539; }}
  .val {{ text-align: right; min-width: 46px; }}
  .pts {{ font-size: 19px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .sub {{ font-size: 10px; color: #8a93a6; }}
  .up {{ color: #37d67a; }}
  .down {{ color: #e23539; }}
  .flat {{ color: #8a93a6; }}

  .fx {{
    display: flex; align-items: center; gap: 8px;
    padding: 13px 15px; border-bottom: 1px solid #1e222b;
  }}
  .fx:last-of-type {{ border-bottom: none; }}
  .side {{ flex: 1; display: flex; align-items: center; gap: 9px; min-width: 0; }}
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

  .gridwrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  table.h2h {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  table.h2h th, table.h2h td {{
    padding: 7px 2px; text-align: center; border-bottom: 1px solid #1e222b;
    font-weight: 600;
  }}
  table.h2h th {{ color: #8a93a6; font-size: 11px; }}
  table.h2h .rowlab {{
    text-align: left; padding-left: 15px; padding-right: 8px;
    color: #f4f6f9; font-size: 12px; font-weight: 600;
    max-width: 132px; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis;
  }}
  table.h2h .rowlab .n {{
    display: inline-block; width: 15px; color: #8a93a6; font-weight: 700;
  }}
  table.h2h td.self {{ color: #333a48; }}
  table.h2h td.nil {{ color: #3d4553; }}
  table.h2h tr:last-child th, table.h2h tr:last-child td {{ border-bottom: none; }}

  .chart {{ display: block; width: 100%; height: auto; padding: 6px 4px 0; }}
  .chart .ax {{ fill: #7d879b; font-size: 10px;
    font-family: -apple-system, sans-serif; }}
  .chart .pt {{ fill: #f4f6f9; font-size: 10.5px; font-weight: 700;
    font-family: -apple-system, sans-serif; }}
  .empty {{ padding: 34px 18px; text-align: center; font-size: 13px; color: #8a93a6; }}
  .legend {{
    display: flex; flex-wrap: wrap; gap: 14px;
    padding: 10px 17px 14px; border-top: 1px solid #262b36;
  }}
  .legend span {{
    display: flex; align-items: center; gap: 6px; font-size: 11px; color: #8a93a6;
  }}
  .legend i {{ display: block; width: 16px; height: 0; border-top-width: 2.5px; }}
  .k-line {{ border-top: 2.5px solid #e23539; }}
  .k-me {{ border-top: 2.5px dashed #f0b429; }}
  .k-lg {{ border-top: 2.5px dashed #7d879b; }}

  .tiles {{ display: flex; flex-wrap: wrap; }}
  .tiles > div {{
    flex: 1 1 50%; min-width: 50%; padding: 13px 17px;
    border-bottom: 1px solid #1e222b;
  }}
  .tiles > div:nth-child(odd) {{ border-right: 1px solid #1e222b; }}
  .tiles span {{
    display: block; font-size: 9.5px; letter-spacing: .08em;
    text-transform: uppercase; color: #8a93a6;
  }}
  .tiles b {{ display: block; font-size: 24px; font-weight: 800; margin: 3px 0 2px; }}
  .tiles em {{
    font-style: normal; font-size: 10.5px; color: #8a93a6; line-height: 1.35;
    display: block;
  }}

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
  .rec2 b.up {{ color: #37d67a; }}
  .rec2 b.down {{ color: #e23539; }}
  .rec2 em {{
    font-style: normal; font-size: 11px; color: #8a93a6;
    display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}

  .note {{ padding: 11px 17px; font-size: 11.5px; color: #8a93a6; line-height: 1.45;
    border-top: 1px solid #1e222b; }}
  .note b {{ color: #f4f6f9; }}
  .note b.up {{ color: #37d67a; }}
  .note.warn {{ color: #f0b429; }}
  footer {{ text-align: center; font-size: 11px; color: #4e576b; margin-top: 26px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand"><span class="mark">{mark}</span>
    <span class="title">{league_name}</span></div>
  <div class="stampbar">
    <div class="stamp">Consistency tracker &middot; updated <b>{updated}</b></div>
    <button id="reload" class="reload" title="Reload">&#8635;</button>
  </div>

  <div class="tabs">
    <button class="tab on" data-tab="segments">Segments</button>
    <button class="tab" data-tab="gameweek">Gameweek</button>
    <button class="tab" data-tab="season">Season</button>
    <button class="tab" data-tab="analysis">Analysis</button>
    <button class="tab" data-tab="trends">Trends</button>
  </div>

  <div class="pane on" data-pane="segments">{''.join(seg_blocks)}</div>
  <div class="pane" data-pane="gameweek">{gw_tab}</div>
  <div class="pane" data-pane="season">{''.join(season_blocks)}</div>
  <div class="pane" data-pane="analysis">{''.join(ana_blocks)}</div>
  <div class="pane" data-pane="trends">{trends_tab}</div>

  <footer>Auto-updates daily &middot; data from the official FPL Draft API</footer>
</div>
{SCRIPT}
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"Built index.html for {league_name} through GW{latest}")

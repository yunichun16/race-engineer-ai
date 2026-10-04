"""Generate notebooks/01_explore_telemetry.ipynb (run it, then execute with nbconvert)."""

from pathlib import Path

import nbformat as nbf

md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell

cells = [
    md(
        "# Exploring the telemetry dataset\n\n"
        "A first look at the processed data: one qualifying session, two teammates in the same "
        "car, and a lap deleted for track limits. Everything here reads the Parquet files through "
        "DuckDB views (`race_engineer.data.store.connect`).\n\n"
        "The session is set in the next cell; any processed session works."
    ),
    code(
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        "from matplotlib.collections import LineCollection\n\n"
        "from race_engineer.config import REPORT_DIR, WINDOW_BEFORE_M\n"
        "from race_engineer.data.store import connect\n\n"
        "con = connect()\n"
        "FIG = REPORT_DIR / 'figures'\n"
        "FIG.mkdir(parents=True, exist_ok=True)\n"
        "plt.rcParams.update({'figure.dpi': 110, 'axes.spines.top': False, 'axes.spines.right': False,\n"
        "                     'axes.grid': True, 'grid.alpha': 0.25, 'font.size': 9})\n\n"
        "YEAR, ROUND, SESSION = 2026, 1, 'Q'\n"
        "W = f\"year = {YEAR} and round = {ROUND} and session = '{SESSION}'\"\n"
        "info = con.sql(f'select event, grid_step_m, track_length_m from sessions where {W}').df().iloc[0]\n"
        "STEP = float(info.grid_step_m)\n"
        "print(f'{info.event} {YEAR} {SESSION}: track {info.track_length_m:,.0f} m, grid step {STEP:g} m')"
    ),
    md("## What's in the dataset"),
    code(
        "con.sql('''select year, session, count(*) sessions, sum(n_laps) laps,\n"
        "                  sum(n_grid_ok) lap_grids, sum(n_segments) segments,\n"
        "                  sum(n_track_limit_events) track_limit_labels\n"
        "           from sessions group by all order by year, session''').df()"
    ),
    md(
        "## The fastest laps\n\n"
        "Each driver's best clean qualifying lap. The two fastest here are teammates, so they had "
        "the same car: the differences below come from the drivers."
    ),
    code(
        "best = con.sql(f'''\n"
        "    select driver, team, lap_id, lap_number, lap_time_s from laps\n"
        "    where {W} and lap_class = 'push' and grid_ok\n"
        "    qualify row_number() over (partition by driver order by lap_time_s) = 1\n"
        "    order by lap_time_s''').df()\n"
        "A = best.iloc[0]\n"
        "B = best[(best.team == A.team) & (best.driver != A.driver)].iloc[0]  # A's teammate\n"
        "best.head(8)"
    ),
    code(
        "CHANNELS = ['time_s', 'speed', 'throttle', 'brake', 'gear', 'x_m', 'y_m', 'offset_m']\n\n"
        "def lap_grid(lap_id):\n"
        "    row = con.sql(f\"select * from lap_grids where lap_id = '{lap_id}'\").df().iloc[0]\n"
        "    return {c: np.asarray(row[c]) for c in CHANNELS}\n\n"
        "ga, gb = lap_grid(A.lap_id), lap_grid(B.lap_id)\n"
        "dist = np.arange(len(ga['speed'])) * STEP\n"
        "corners = con.sql(f'select number, letter, apex_m, x_m, y_m from corners where {W} order by apex_m').df()\n"
        "print(f'{A.driver} {A.lap_time_s:.3f}s vs {B.driver} {B.lap_time_s:.3f}s '\n"
        "      f'(gap {B.lap_time_s - A.lap_time_s:.3f}s); {len(corners)} turns')"
    ),
    md(
        "## Track map\n\nThe fastest lap's position trace, coloured by speed, with the official turn numbers."
    ),
    code(
        "pts = np.column_stack([ga['x_m'], ga['y_m']])\n"
        "fig, ax = plt.subplots(figsize=(6.5, 5.5))\n"
        "lc = LineCollection(np.stack([pts[:-1], pts[1:]], axis=1), cmap='viridis', linewidth=4)\n"
        "lc.set_array(ga['speed'][:-1])\n"
        "ax.add_collection(lc)\n"
        "ax.autoscale(); ax.set_aspect('equal'); ax.axis('off')\n"
        "for c in corners.itertuples():\n"
        "    ax.annotate(f'{c.number}{c.letter}', (c.x_m, c.y_m), fontsize=7, ha='center', va='center',\n"
        "                bbox=dict(boxstyle='circle,pad=0.25', fc='white', ec='0.6', lw=0.5))\n"
        "fig.colorbar(lc, ax=ax, shrink=0.6, label='Speed (km/h)')\n"
        'ax.set_title(f"{info.event} {YEAR}: {A.driver}\'s fastest qualifying lap")\n'
        "fig.savefig(FIG / 'track_map_speed.png', bbox_inches='tight')"
    ),
    md(
        "## Where the time went\n\n"
        "Both laps sit on the same 5 m distance grid, so subtracting their `time_s` arrays gives the "
        "gap at every point of the lap. When the line climbs, the second driver is losing time."
    ),
    code(
        "delta = gb['time_s'] - ga['time_s']\n"
        "fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5.2), sharex=True, height_ratios=[2, 1])\n"
        "ax1.plot(dist / 1000, ga['speed'], lw=1.2, label=A.driver)\n"
        "ax1.plot(dist / 1000, gb['speed'], lw=1.2, label=B.driver)\n"
        "top = ga['speed'].max() + 12\n"
        "for c in corners.itertuples():\n"
        "    ax1.axvline(c.apex_m / 1000, color='0.88', lw=0.8, zorder=0)\n"
        "    ax1.text(c.apex_m / 1000, top, f'T{c.number}', ha='center', fontsize=7, color='0.4')\n"
        "ax1.set_ylabel('Speed (km/h)'); ax1.legend(loc='lower right')\n"
        "ax2.plot(dist / 1000, delta, color='0.25', lw=1.2)\n"
        "ax2.axhline(0, color='0.6', lw=0.8)\n"
        "ax2.set_ylabel(f'{B.driver} behind (s)'); ax2.set_xlabel('Distance from the timing line (km)')\n"
        "fig.suptitle(f'{A.driver} vs {B.driver}: speed and time gap along the lap', y=0.98)\n"
        "fig.savefig(FIG / 'teammates_lap_delta.png', bbox_inches='tight')"
    ),
    md(
        "## One corner up close\n\n"
        "A corner segment runs from 250 m before the apex to 150 m after it: 80 points on the grid. "
        "This is the unit the model learns from. The corner below is the one where push laps varied "
        "most in this session."
    ),
    code(
        "CORNER = int(con.sql(f'''select corner from segments where {W} and lap_class = 'push'\n"
        "                         group by corner order by stddev(segment_time_s) desc limit 1''').fetchone()[0])\n"
        "seg = con.sql(f'''select driver, speed, throttle, brake, time_s from segments\n"
        "                  where lap_id in ('{A.lap_id}', '{B.lap_id}') and corner = {CORNER}''').df()\n"
        "x = np.arange(80) * STEP - WINDOW_BEFORE_M\n"
        "fig, axes = plt.subplots(3, 1, figsize=(8, 5.8), sharex=True, height_ratios=[2.2, 1, 0.6])\n"
        "for r in seg.itertuples():\n"
        "    axes[0].plot(x, r.speed, lw=1.4, label=f'{r.driver}: {np.asarray(r.time_s)[-1]:.3f}s through the window')\n"
        "    axes[1].plot(x, r.throttle, lw=1.2)\n"
        "    axes[2].plot(x, r.brake, lw=1.2)\n"
        "for ax in axes: ax.axvline(0, color='0.6', lw=0.8, ls='--')\n"
        "axes[0].set_ylabel('Speed (km/h)'); axes[0].legend(loc='lower left', fontsize=8)\n"
        "axes[1].set_ylabel('Throttle %'); axes[2].set_ylabel('Brake'); axes[2].set_xlabel('Metres from the apex')\n"
        "fig.suptitle(f'Turn {CORNER}: {A.driver} vs {B.driver}', y=0.98)\n"
        "fig.savefig(FIG / 'teammates_corner.png', bbox_inches='tight')"
    ),
    md(
        "## What a labelled mistake looks like\n\n"
        "Race control deletes lap times for exceeding track limits at a named turn, which pins a "
        "mistake to one segment. Not every label is a real attempt: in this session Russell's "
        "*out-lap* was deleted for track limits at Turn 9, while he was warming his tyres. So labels "
        "only count on clean laps (`push` or `race`).\n\n"
        "Below, a labelled segment on a clean lap (red) against the same driver's other clean laps "
        "through the same corner (grey), preferring this session."
    ),
    code(
        "ev = con.sql(f'''\n"
        "    select e.year, e.round, e.session, e.driver, e.lap_number, e.turn, s2.event from events e\n"
        "    join laps l on l.year = e.year and l.round = e.round and l.session = e.session\n"
        "     and l.driver = e.driver and l.lap_number = e.lap_number\n"
        "    join segments s on s.lap_id = l.lap_id and s.corner = e.turn\n"
        "    join sessions s2 on s2.year = e.year and s2.round = e.round and s2.session = e.session\n"
        "    where e.kind = 'track_limits' and e.drivers_same_lap_turn = 1\n"
        "      and l.lap_class in ('push', 'race')\n"
        "    order by (e.year = {YEAR} and e.round = {ROUND} and e.session = '{SESSION}') desc,\n"
        "             e.year desc, e.round, e.driver limit 1''').df().iloc[0]\n"
        "W2 = f\"year = {ev.year} and round = {ev['round']} and session = '{ev.session}'\"\n"
        "same = con.sql(f'''select lap_number, speed, offset_m, throttle from segments\n"
        "                   where {W2} and driver = '{ev.driver}' and corner = {ev.turn}\n"
        "                   and (lap_class in ('push', 'race') or lap_number = {ev.lap_number})''').df()\n"
        "flag = same[same.lap_number == ev.lap_number].iloc[0]\n"
        "rest = same[same.lap_number != ev.lap_number]\n"
        "fig, axes = plt.subplots(3, 1, figsize=(8, 6), sharex=True, height_ratios=[1.6, 1.2, 1])\n"
        "for col, ax, label in [('speed', axes[0], 'Speed (km/h)'), ('offset_m', axes[1], 'Offset from line (m)'),\n"
        "                       ('throttle', axes[2], 'Throttle %')]:\n"
        "    for r in rest[col]: ax.plot(x, r, color='0.75', lw=0.9)\n"
        "    ax.plot(x, flag[col], color='crimson', lw=1.8)\n"
        "    ax.axvline(0, color='0.6', lw=0.8, ls='--'); ax.set_ylabel(label)\n"
        "axes[2].set_xlabel('Metres from the apex')\n"
        "fig.suptitle(f'{ev.event} {ev.year} ({ev.session}), {ev.driver} at turn {ev.turn}: lap {ev.lap_number} '\n"
        "             f'deleted for track limits (red) vs {len(rest)} other clean laps', y=0.98)\n"
        "fig.savefig(FIG / 'labelled_mistake.png', bbox_inches='tight')"
    ),
    md(
        "## Takeaways for modelling\n\n"
        "- The 5 m grid makes laps directly comparable: gaps, corner times and traces subtract cleanly.\n"
        "- Differences between teammates are small and local: a few hundredths per corner, from braking "
        "point, minimum speed and throttle pickup. That's the signal the style model has to pick up.\n"
        "- A mistake is a departure from the driver's own normal trace at that corner, especially in "
        "lateral offset and throttle, which is what the anomaly detector will score.\n"
        "- Only laps with deleted times are labelled, so most mistakes are unlabelled: evaluation needs "
        "the weak labels *and* a manual review."
    ),
]

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
out = Path(__file__).resolve().parents[1] / "notebooks" / "01_explore_telemetry.ipynb"
out.parent.mkdir(parents=True, exist_ok=True)
nbf.write(nb, out)
print(f"wrote {out}")

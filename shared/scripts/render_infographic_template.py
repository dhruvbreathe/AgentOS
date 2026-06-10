import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import date

# Vayu-ish dark palette
BG = "#0E1420"
PANEL = "#161E2E"
TEAL = "#4FD1C5"
AMBER = "#F6AD55"
BLUE = "#63B3ED"
GREY = "#8C9BB3"
WHITE = "#E6EDF7"

days = [date(2026, 6, d) for d in range(2, 9)]
dau = [109, 106, 113, 99, 123, 146, 146]
onboard = [19, 28, 27, 18, 36, 59, 48]
completed = [17, 18, 21, 8, 23, 23, 29]
paywall = [20, 18, 16, 22, 23, 31, 22]

fig = plt.figure(figsize=(11, 6.2), dpi=160)
fig.patch.set_facecolor(BG)

gs = fig.add_gridspec(2, 3, height_ratios=[1, 1.45], hspace=0.42, wspace=0.28,
                      left=0.06, right=0.97, top=0.86, bottom=0.10)

fig.text(0.06, 0.945, "VAYU  ·  COMPANY SIGNALS", color=WHITE, fontsize=17, fontweight="bold", family="DejaVu Sans")
fig.text(0.06, 0.905, "Week of Jun 2 – Jun 8, 2026  ·  pulled live from Supabase prod + Mixpanel", color=GREY, fontsize=9)

# --- KPI tiles (top row) ---
kpis = [
    ("ACTIVE SUBS", "83", "MRR ~$6K CAD", TEAL),
    ("SIGNUPS WoW", "220", "+20% vs 184", AMBER),
    ("iOS SESSIONS WoW", "450", "+39% vs 323", BLUE),
]
for i, (label, big, sub, color) in enumerate(kpis):
    ax = fig.add_subplot(gs[0, i])
    ax.set_facecolor(PANEL)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])
    ax.text(0.5, 0.72, label, ha="center", va="center", color=GREY, fontsize=9.5, transform=ax.transAxes)
    ax.text(0.5, 0.40, big, ha="center", va="center", color=color, fontsize=30, fontweight="bold", transform=ax.transAxes)
    ax.text(0.5, 0.12, sub, ha="center", va="center", color=WHITE, fontsize=10, transform=ax.transAxes)

# --- DAU trend (bottom left, spans 2 cols) ---
ax1 = fig.add_subplot(gs[1, :2])
ax1.set_facecolor(PANEL)
ax1.plot(days, dau, color=TEAL, linewidth=2.5, marker="o", markersize=5, label="DAU (app launched)")
ax1.fill_between(days, dau, color=TEAL, alpha=0.12)
ax1.plot(days, onboard, color=AMBER, linewidth=2, marker="o", markersize=4, label="Onboarding completed")
ax1.plot(days, completed, color=BLUE, linewidth=2, marker="o", markersize=4, label="Sessions completed")
ax1.set_title("Daily engagement, trending up into the week's end", color=WHITE, fontsize=11, loc="left", pad=10)
ax1.legend(facecolor=PANEL, edgecolor="none", labelcolor=WHITE, fontsize=8.5, loc="upper left")
ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
ax1.tick_params(colors=GREY, labelsize=8.5)
for s in ax1.spines.values():
    s.set_color("#2A3650")
ax1.grid(color="#22304A", linewidth=0.6, alpha=0.7)

# --- Funnel snapshot (bottom right) ---
ax2 = fig.add_subplot(gs[1, 2])
ax2.set_facecolor(PANEL)
stages = ["DAU peak", "Onboard", "Paywall", "Completed"]
vals = [146, 59, 31, 29]
colors = [TEAL, AMBER, BLUE, GREY]
bars = ax2.barh(stages[::-1], vals[::-1], color=colors[::-1], height=0.55)
for b, v in zip(bars, vals[::-1]):
    ax2.text(b.get_width() + 3, b.get_y() + b.get_height()/2, str(v), va="center", color=WHITE, fontsize=9, fontweight="bold")
ax2.set_title("Best-day funnel (Jun 7-8)", color=WHITE, fontsize=11, loc="left", pad=10)
ax2.tick_params(colors=GREY, labelsize=8.5)
ax2.set_xlim(0, 175)
for s in ax2.spines.values():
    s.set_visible(False)
ax2.grid(axis="x", color="#22304A", linewidth=0.6, alpha=0.7)

out = "/tmp/vayu-signals-2026-06-09.png"
fig.savefig(out, facecolor=BG, bbox_inches="tight")
print(out)

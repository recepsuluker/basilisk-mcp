import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.colors import LinearSegmentedColormap

for f in fm.findSystemFonts():
    if "Inter" in f:
        fm.fontManager.addfont(f)
plt.rcParams.update({"font.family": "Inter", "font.size": 13})

SURF = "#0f1420"; INK = "#e9edf5"; INK2 = "#9aa4b8"; GRID = "#263045"
SERIES = ["#3987e5", "#d95926", "#199e70"]
d = json.load(open("data.json"))

# ---------- attitude error, log scale ----------
fig, ax = plt.subplots(figsize=(8, 5), dpi=200, facecolor=SURF)
ax.set_facecolor(SURF)
labels = ["K=3.5, P=30", "K=7, P=40", "K=12, P=60"]
names = {"K=3.5, P=30": "Basilisk example  K=3.5 P=30", "K=7, P=40": "K=7 P=40", "K=12, P=60": "K=12 P=60"}
for c, k in zip(SERIES, labels):
    t, e = np.array(d[k]["t"]), np.array(d[k]["err"])
    ax.plot(t, e, color=c, lw=2, label=names[k], solid_capstyle="round")
    ax.text(606, e[-1], f"{e[-1]:.3f}°", color=INK, va="center", fontsize=12)
ax.axhline(1.0, color=INK2, lw=1, ls=(0, (4, 4)))
ax.text(8, 1.18, "1° settling threshold", color=INK2, fontsize=11)
ax.set_yscale("log"); ax.set_ylim(1e-3, 200); ax.set_xlim(0, 600)
ax.set_xlabel("time [s]", color=INK2); ax.set_ylabel("attitude error [deg]", color=INK2)
ax.tick_params(colors=INK2, length=0)
for s in ax.spines.values(): s.set_visible(False)
ax.grid(True, color=GRID, lw=0.8, which="major"); ax.set_axisbelow(True)
leg = ax.legend(loc="lower left", frameon=False, fontsize=11.5, labelcolor=INK)
ax.set_title("Reaction-wheel attitude control: three gain sets", color=INK, loc="left", fontsize=15, pad=12, weight="semibold")
fig.subplots_adjust(left=0.11, right=0.89, top=0.88, bottom=0.13)
fig.savefig("attitude_dark.png", facecolor=SURF)

print("ok")

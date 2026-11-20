import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.colors import LinearSegmentedColormap
for f in fm.findSystemFonts():
    if "Inter" in f: fm.fontManager.addfont(f)
plt.rcParams.update({"font.family": "Inter", "font.size": 13})
SURF = "#0f1420"; INK = "#e9edf5"; INK2 = "#9aa4b8"
d = json.load(open("data.json"))
r = np.array(d["orbit"]["r"]); t = np.array(d["orbit"]["t"])
R = 6378.0
period = 2 * np.pi * np.sqrt(7000.0**3 / 398600.436)  # [s]
elev, azim = np.radians(22), np.radians(-35)
# camera basis: view direction from the camera toward the origin
cview = np.array([np.cos(elev) * np.cos(azim), np.cos(elev) * np.sin(azim), np.sin(elev)])  # toward camera
right = np.cross([0, 0, 1], cview); right /= np.linalg.norm(right)
up = np.cross(cview, right)
def project(p):
    return p @ right, p @ up, p @ cview  # x, y, depth (positive = toward camera)

ramp = LinearSegmentedColormap.from_list("blue", ["#2a78d6", "#5598e7", "#9ec5f4", "#e6f0fd"])
fig, ax = plt.subplots(figsize=(7, 7), dpi=200, facecolor=SURF)
ax.set_facecolor(SURF)
days = range(0, 11)
segments = []
for day in days:
    sel = (t >= day * 86400 - 1) & (t <= day * 86400 + period)
    if day == 10:
        sel = t >= t[-1] - period
    x, y, z = project(r[sel])
    behind = (np.hypot(x, y) < R) & (z < 0)
    segments.append((day, x, y, z, behind))
# far side (behind Earth's center plane) first, then Earth, then near side
for day, x, y, z, behind in segments:
    col = ramp(day / 10)
    far = np.where((z < 0) & ~behind, 1.0, np.nan)
    ax.plot(x * far, y * far, color=col, lw=1.3, alpha=0.55, zorder=1)
ax.add_patch(plt.Circle((0, 0), R, color="#16365e", zorder=2))
ax.add_patch(plt.Circle((0, 0), R, fill=False, color="#2a5a92", lw=1.2, zorder=2))
# equator and pole axis for orientation
th = np.linspace(0, 2 * np.pi, 400)
eq = np.stack([R * np.cos(th), R * np.sin(th), 0 * th], 1)
ex, ey, ez = project(eq)
front = np.where(ez >= 0, 1.0, np.nan)
ax.plot(ex * front, ey * front, color="#2f6aa8", lw=0.9, ls=(0, (3, 3)), zorder=3)
for day, x, y, z, behind in segments:
    col = ramp(day / 10)
    near = np.where(z >= 0, 1.0, np.nan)
    ax.plot(x * near, y * near, color=col, lw=1.6, zorder=4)
lim = 7700
ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim); ax.set_aspect("equal"); ax.axis("off")
a = d["orbit_analysis"]
ax.text(-lim, lim * 0.98, "J2 turns the orbit plane", color=INK, fontsize=16, weight="semibold", va="top")
ax.text(-lim, lim * 0.89, f"one orbit per day, days 0–10  ·  RAAN {a['raan_rate']:.2f} °/day (analytic {a['raan_analytic']:.2f})",
        color=INK2, fontsize=11, va="top")
ax.text(-lim, -lim * 0.98, "7000 km, e = 0.05, i = 30°, Earth with J2 — Basilisk via basilisk-mcp", color=INK2, fontsize=10.5)
cax = fig.add_axes([0.72, 0.085, 0.22, 0.016])
cb = fig.colorbar(plt.cm.ScalarMappable(cmap=ramp, norm=plt.Normalize(0, 10)), cax=cax, orientation="horizontal")
cb.outline.set_visible(False); cb.set_ticks([0, 5, 10]); cb.ax.tick_params(colors=INK2, labelsize=9.5, length=0)
cb.set_label("day", color=INK2, fontsize=10)
fig.subplots_adjust(0.02, 0.02, 0.98, 0.98)
fig.savefig("orbit_planes_dark.png", facecolor=SURF)
print("ok")

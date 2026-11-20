# Visuals

Scripts that regenerate the images in `docs/images/` from real simulations.

```bash
cd tools/visuals
python make_data.py      # runs the simulations through basilisk-mcp -> data.json
python make_plots.py     # attitude_dark.png (+ an unused 3-D draft)
python make_orbit2.py    # orbit_planes_dark.png
# hero.png and the social cards are HTML rendered with headless Chrome, for example:
google-chrome --headless=new --hide-scrollbars --force-device-scale-factor=2 \
    --window-size=1600,900 --screenshot=hero.png "file://$PWD/hero.html"
google-chrome --headless=new --hide-scrollbars --force-device-scale-factor=2 \
    --window-size=1200,627 --screenshot=linkedin_card.png "file://$PWD/card.html"
```

Chart colors follow a colorblind-validated categorical palette on the dark surface `#0f1420`.

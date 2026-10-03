Read when: creating, checking or exporting a draw.io diagram (server `crepe-diagrams`, 3 tools; script `scripts/dg.py`).

# Diagrams

```
(write the .drawio file)  ->  lint_drawio(input_path)  ->  export_drawio(input_path, output_path, ...)
```

`inspect_drawio(input_path)` validates the file and lists its pages and their names. `lint_drawio` is the richer check: it decodes compressed diagram XML and validates the cell hierarchy without starting draw.io. Fix every issue before exporting.

## Export settings for slides and documents

```
export_drawio(input_path="/abs/diagram.drawio", output_path="/abs/diagram.png",
              output_format="png", transparent=True, scale=2.0, border=4)
```

- `output_format`: `png` (default), `svg` (vector), `pdf`, `jpg`, `html`, `xml`. `all_pages=true` is for pdf and html only; `page_index` is 1-based.
- Keep `transparent=True` (the default) for PNG and SVG: it embeds in slides without a white box. Turn it off only when a white background is wanted.
- `scale=2.0` gives high-DPI images for slides. `border` pads the drawing in pixels. `embed_diagram=true` keeps the source inside png, svg or pdf.
- Embed the result with an absolute path: `![Caption](/abs/diagram.png){width=80%}`.

## Structure of a hand-written `.drawio`

Every file needs the two base cells; content cells use `parent="1"`. Missing either gives empty exports.

```xml
<mxGraphModel><root>
  <mxCell id="0"/>
  <mxCell id="1" parent="0"/>
  <!-- your cells: parent="1" -->
</root></mxGraphModel>
```

`lint_drawio` also reports duplicate cell ids and layer hierarchy errors.

## Generating figures: `scripts/dg.py`

Use it for programmatic diagrams (flows, architectures). Two ways:

```
scripts/dg.py spec.json out.drawio              # JSON spec, run with: uv run <skill>/scripts/dg.py
```

```json
{"name": "arch", "width": 1200, "height": 400,
 "boxes": [{"id": "a", "label": "Client", "x": 40, "y": 40, "w": 200, "h": 60, "style": "blue"},
           {"id": "b", "label": "Server", "x": 400, "y": 40, "w": 200, "h": 60, "style": "green"}],
 "edges": [{"from": "a", "to": "b", "label": "request"}]}
```

Or from Python: `sys.path.insert(0, "<skill>/scripts"); from dg import Diagram`, then `d.box(...)`, `d.edge(a, b, "label")`, `d.save("fig.drawio")`. Styles are the names in `PALETTE` (`blue navy purple red green orange yellow grey white none`) or a raw draw.io style string. Then `lint_drawio` and `export_drawio` as above.

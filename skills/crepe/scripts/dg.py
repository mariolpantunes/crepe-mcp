#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""dg.py - minimal draw.io (.drawio) writer for report figures.

Usage (CLI, JSON spec -> .drawio):
    dg.py spec.json out.drawio
    spec: {"name", "width", "height", "boxes": [{"id", "label", "x", "y", "w", "h", "style", ...}],
           "edges": [{"from", "to", "label", "dashed", ...}]}; other keys are passed to Diagram.box/edge.

Usage (Python):
    from dg import Diagram
    d = Diagram("name", width=1200, height=700)
    a = d.box("A", 40, 40, 200, 60, "blue")
    b = d.box("B", 400, 40, 200, 60, "green")
    d.edge(a, b, "label")
    d.save("fig.drawio")

Styles are named palettes (see PALETTE) or raw draw.io style strings.
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape

FONT = "fontFamily=Helvetica;"

PALETTE = {
    "blue": "fillColor=#DAE8FC;strokeColor=#2B579A;",
    "navy": "fillColor=#2B579A;strokeColor=#1A3A6B;fontColor=#FFFFFF;",
    "purple": "fillColor=#E1D5E7;strokeColor=#9673A6;",
    "red": "fillColor=#F8CECC;strokeColor=#B85450;",
    "green": "fillColor=#D5E8D4;strokeColor=#82B366;",
    "orange": "fillColor=#FFE6CC;strokeColor=#D79B00;",
    "yellow": "fillColor=#FFF2CC;strokeColor=#D6B656;",
    "grey": "fillColor=#F5F5F5;strokeColor=#666666;",
    "white": "fillColor=#FFFFFF;strokeColor=#666666;",
    "none": "fillColor=none;strokeColor=none;",
}


class Diagram:
    def __init__(self, name, width=1200, height=700):
        self.name, self.w, self.h = name, width, height
        self.cells = []
        self.n = 1

    def _id(self):
        self.n += 1
        return f"c{self.n}"

    def box(self, label, x, y, w, h, style="grey", size=12, bold=False,
            rounded=True, align="center", valign="middle", extra=""):
        cid = self._id()
        st = PALETTE.get(style, style)
        st += (f"rounded={1 if rounded else 0};whiteSpace=wrap;html=1;{FONT}"
               f"fontSize={size};align={align};verticalAlign={valign};"
               f"fontStyle={1 if bold else 0};arcSize=8;spacing=6;{extra}")
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(label, {chr(34): "&quot;"})}" '
            f'style="{st}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def text(self, label, x, y, w, h, size=12, bold=False, align="center", color="#333333"):
        return self.box(label, x, y, w, h, "none", size, bold, False, align,
                        extra=f"fontColor={color};")

    def edge(self, src, dst, label="", dashed=False, color="#555555", size=11,
             exit_=None, entry=None, both=False, width=1.5):
        cid = self._id()
        st = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;{FONT}fontSize={size};"
              f"strokeColor={color};strokeWidth={width};endArrow=block;endFill=1;"
              f"{'dashed=1;' if dashed else ''}{'startArrow=block;startFill=1;' if both else ''}")
        if exit_:
            st += f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;"
        if entry:
            st += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(label)}" style="{st}" edge="1" parent="1" '
            f'source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry"/></mxCell>')
        return cid

    def save(self, path):
        xml = (f'<mxfile host="dg.py"><diagram id="{self.name}" name="{self.name}">'
               f'<mxGraphModel dx="{self.w}" dy="{self.h}" grid="0" gridSize="10" page="1" '
               f'pageWidth="{self.w}" pageHeight="{self.h}" math="0" shadow="0"><root>'
               '<mxCell id="0"/><mxCell id="1" parent="0"/>'
               + "".join(self.cells) + "</root></mxGraphModel></diagram></mxfile>")
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
        return path


def main(argv):
    import json

    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    d = Diagram(spec.get("name", "diagram"), spec.get("width", 1200), spec.get("height", 700))
    ids = {}
    for box in spec.get("boxes", []):
        box = dict(box)
        key = box.pop("id", None)
        cell = d.box(box.pop("label"), box.pop("x"), box.pop("y"), box.pop("w"), box.pop("h"), **box)
        if key is not None:
            ids[key] = cell
    for edge in spec.get("edges", []):
        edge = dict(edge)
        src, dst = edge.pop("from"), edge.pop("to")
        d.edge(ids.get(src, src), ids.get(dst, dst), **edge)
    print(d.save(argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

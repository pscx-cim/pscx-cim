"""One HTML page for a written catalog document.

``pscx catalog --html`` renders a catalog into a browsable reference:
an index of every declared model type, then one section per type with
its categories, parameter declarations, choice lists, help text,
visibility expressions and ports. The
renderer reads the WRITTEN document back through
:func:`pscx.catalog.read_catalog` -- the decode half of the catalog's
inverse pair -- so the page is a view of what the document states,
never of the library state that produced it, and it renders any
catalog, including one generated elsewhere. The page is fully
self-contained -- the type filter, the collapsible sections and the
light/dark color schemes are inline, and nothing external is fetched.

Each type is annotated with how ``pscx emit`` treats it, joined at
render time from the same rule tables the emitter reads
(:mod:`pscx.rules`) and the standing triage of unmapped kinds
(:data:`pscx.audit.TRIAGE`). The catalog document itself carries no
mapping statement: it states the library side only, and the join is
made here, against the rules of the pscx version doing the rendering.
The profile's class documentation and diagram stay with the published
vocabulary (``EMT.md`` beside ``EMT-AP-Voc-RDFS.rdf``) and are pointed
to, not embedded.
"""

from __future__ import annotations

import html
import io
import xml.etree.ElementTree as ET

from pscx.audit import TRIAGE
from pscx.catalog import EMT_LIBRARY_PROFILE_URI, read_catalog
from pscx.rules import (
    DEVICE_KIND_TO_CIM,
    GROUND_ROLES,
    MASTER_KIND_TO_CIM,
    MEASUREMENT_KINDS,
    OUTPUT_QUANTITY,
    STRUCTURAL_KINDS,
    ZERO_IMPEDANCE_KINDS,
)

_STYLE = """\
:root { color-scheme: light dark;
        --bg: #ffffff; --fg: #1a1a1a; --muted: #5a5a5a; --line: #d0d0d0;
        --panel: #f4f4ef; --head: #efefef; --code: #f5f5f5;
        --accent: #2f6f4f; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #14161a; --fg: #dfe2e6; --muted: #98a0a8;
          --line: #3a3f46; --panel: #1d2126; --head: #20242a;
          --code: #22262c; --accent: #7fbf9f; }
}
body { font: 15px/1.5 system-ui, sans-serif; margin: 1rem 1.5rem;
       background: var(--bg); color: var(--fg); }
h1 { font-size: 1.6rem; }
details.type { border-top: 1px solid var(--line);
               padding: .35rem 0 .9rem;
               scroll-margin-top: 4.5rem;
               content-visibility: auto;
               contain-intrinsic-size: auto 24rem; }
summary { cursor: pointer; padding: .35rem 0; }
summary h2 { display: inline; font-size: 1.15rem; margin: 0; }
.tag { margin-left: .75rem; font-size: .8rem; color: var(--accent);
       border: 1px solid var(--accent); border-radius: .6rem;
       padding: 0 .5rem; white-space: nowrap; }
table { border-collapse: collapse; width: 100%; margin: .75rem 0; }
th, td { border: 1px solid var(--line); padding: .25rem .5rem;
         text-align: left; vertical-align: top; }
th { background: var(--head); }
code { background: var(--code); padding: 0 .2rem; }
.muted { color: var(--muted); font-size: .9em; }
.scroll-body { overflow-x: auto; scrollbar-width: none; }
.scroll-body::-webkit-scrollbar { height: 0; }
.scroll-bar { position: sticky; bottom: 0; z-index: 2;
              overflow-x: auto; overflow-y: hidden; height: 16px;
              background: var(--bg); }
.scroll-bar > div { height: 1px; }
.annotation { background: var(--panel);
              border-left: 3px solid var(--accent);
              padding: .4rem .75rem; margin: .5rem 0; }
.toolbar { position: sticky; top: 0; background: var(--bg);
           display: flex; flex-wrap: wrap; gap: .6rem;
           align-items: center; padding: .6rem 0;
           border-bottom: 1px solid var(--line); }
.toolbar input { flex: 1; min-width: 14rem; font: inherit;
                 padding: .35rem .6rem;
                 background: var(--bg); color: var(--fg);
                 border: 1px solid var(--line); border-radius: .4rem; }
.toolbar button { font: inherit; font-size: .85em;
                  padding: .3rem .7rem; background: var(--head);
                  color: var(--fg); border: 1px solid var(--line);
                  border-radius: .4rem; cursor: pointer; }
"""

_SCRIPT = """\
const q = document.getElementById("q");
const types = Array.from(document.querySelectorAll("details.type"));
const rows = Array.from(document.querySelectorAll("tr[data-key]"));
const count = document.getElementById("count");
q.addEventListener("input", () => {
  const needle = q.value.trim().toLowerCase();
  let shown = 0;
  for (const el of types) {
    const hit = !needle || el.dataset.key.includes(needle);
    el.hidden = !hit;
    if (hit) shown += 1;
  }
  for (const row of rows) {
    row.hidden = needle && !row.dataset.key.includes(needle);
  }
  count.textContent = needle
    ? shown + " of " + types.length + " shown"
    : types.length + " model types";
});
document.getElementById("open-all").addEventListener("click", () => {
  for (const el of types) el.open = true;
});
document.getElementById("close-all").addEventListener("click", () => {
  for (const el of types) el.open = false;
});
for (const chip of document.querySelectorAll("button[data-q]")) {
  chip.addEventListener("click", () => {
    q.value = q.value === chip.dataset.q ? "" : chip.dataset.q;
    q.dispatchEvent(new Event("input"));
  });
}
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== q) {
    event.preventDefault();
    q.focus();
  } else if (event.key === "Escape" && q.value) {
    q.value = "";
    q.dispatchEvent(new Event("input"));
  }
});
for (const box of document.querySelectorAll(".scroll")) {
  const body = box.querySelector(".scroll-body");
  const bar = box.querySelector(".scroll-bar");
  const spacer = bar.firstElementChild;
  const table = body.querySelector("table");
  let lock = false;
  const follow = (from, to) => {
    if (lock) return;
    lock = true;
    to.scrollLeft = from.scrollLeft;
    lock = false;
  };
  const fit = () => {
    spacer.style.width = table.scrollWidth + "px";
    bar.hidden = table.scrollWidth <= body.clientWidth + 1;
  };
  bar.addEventListener("scroll", () => follow(bar, body));
  body.addEventListener("scroll", () => follow(body, bar));
  new ResizeObserver(fit).observe(table);
  fit();
}
"""


def _kind(qualified: str) -> str:
    """The bare kind a qualified ``definitionName`` states -- the same
    split :func:`pscx.rules.modeled_cim_class` reads."""
    return qualified.partition(":")[2] or qualified


def _annotation(qualified: str) -> str:
    """How ``pscx emit`` treats this kind, beyond the EMT statement
    every placement gets."""
    kind = _kind(qualified)
    cls = MASTER_KIND_TO_CIM.get(kind)
    if cls == "EquivalentBranch":
        shunt = GROUND_ROLES["shunt_resistor"].cim_class
        neutral = GROUND_ROLES["neutral_grounding_impedance"].cim_class
        return (
            f"Projected into the standard five as cim:{cls} when drawn "
            f"in series between two live nodes. Drawn to ground, the "
            f"drawing selects the role: cim:{shunt} on a phase "
            f"conductor, cim:{neutral} on a winding or machine neutral."
        )
    if cls == "Breaker":
        ground = GROUND_ROLES["ground_switch"].cim_class
        return (
            f"Projected into the standard five as cim:{cls}; on the "
            f"ground side it becomes cim:{ground}."
        )
    if cls is not None:
        return f"Projected into the standard five as cim:{cls}."
    cls = DEVICE_KIND_TO_CIM.get(kind)
    if cls is not None:
        return f"A hosting wire, projected as cim:{cls}."
    if kind in MEASUREMENT_KINDS:
        quantities = ", ".join(
            f"{param} as {mtype} in {unit}"
            for (k, param), (mtype, unit) in sorted(OUTPUT_QUANTITY.items())
            if k == kind)
        return (
            f"A measurement point: one cim:Analog per quantity its form "
            f"selectors leave active ({quantities}), stated in the OP "
            f"document; never conducting equipment."
        )
    if kind in STRUCTURAL_KINDS:
        return (
            "A topology role the flat graph itself expresses; no "
            "equipment is emitted for it."
        )
    if kind in ZERO_IMPEDANCE_KINDS:
        return (
            "An ideal zero-impedance connection: a shared "
            "cim:TopologicalNode in TP, not equipment."
        )
    triage = TRIAGE.get(kind)
    if triage is not None:
        destination, cls, reason = triage
        candidate = f" (candidate cim:{cls})" if cls else ""
        return f"No standard projection yet; triaged {destination}{candidate}: {reason}"
    return "No standard projection: exchanged through the EMT statement alone."


def _projection_label(qualified: str) -> str:
    """The index table's one-cell summary of the treatment; blank for
    the EMT-only default so the mapped rows stand out."""
    kind = _kind(qualified)
    cls = MASTER_KIND_TO_CIM.get(kind)
    if cls == "EquivalentBranch":
        return f"cim:{cls} (series; role-split at ground)"
    if cls is not None:
        return f"cim:{cls}"
    cls = DEVICE_KIND_TO_CIM.get(kind)
    if cls is not None:
        return f"cim:{cls} (hosting wire)"
    if kind in MEASUREMENT_KINDS:
        units = sorted({unit for (k, _param), (_mtype, unit)
                        in OUTPUT_QUANTITY.items() if k == kind})
        return f"cim:Analog per active output ({', '.join(units)})"
    if kind in STRUCTURAL_KINDS:
        return "topology role"
    if kind in ZERO_IMPEDANCE_KINDS:
        return "zero impedance"
    triage = TRIAGE.get(kind)
    if triage is not None:
        destination, cls, _reason = triage
        return f"triaged {destination}" + (f": cim:{cls}" if cls else "")
    return ""


#: Payload kinds with their own column. Anything else a declaration
#: retains stays in the details cell, verbatim.
_COLUMN_KINDS = frozenset({
    "choice", "help", "vis", "regex", "error_msg", "column", "row",
})

#: Form attributes with their own column. ``parameterType``, ``minimum``,
#: ``maximum`` and ``condition`` already head the main columns.
_COLUMN_ATTRIBUTES = frozenset({
    "parameterType", "group", "contentType", "dimension", "intent",
    "minimum", "maximum", "condition",
})


def _stated_text(content: str) -> str:
    """The text one retained payload element states. The fragment
    re-parses by the payload encoding's own round-trip guarantee, so a
    parse failure here is a defect, not data."""
    element = ET.fromstring(content)
    return (element.text or "").strip()


def _column_cell(declaration: dict, kind: str) -> str:
    """One column of payload text. An element that states no text
    contributes nothing, so an empty ``<vis/>``, ``<regex/>`` or
    ``<error_msg/>`` stays blank."""
    stated = [
        _stated_text(payload["content"])
        for payload in declaration["payloads"]
        if payload["payloadKind"] == kind
    ]
    return "<br>".join(html.escape(text) for text in stated if text)


def _table_cell(declaration: dict) -> str:
    """A Table parameter's ``<column>`` labels and ``<row>`` values, one
    line each. A column states its label and width as attributes."""
    lines = []
    for payload in declaration["payloads"]:
        kind = payload["payloadKind"]
        if kind == "column":
            element = ET.fromstring(payload["content"])
            label = element.get("label") or ""
            width = element.get("width") or ""
            lines.append(f"{label} ({width})" if width else label)
        elif kind == "row":
            text = _stated_text(payload["content"])
            if text:
                lines.append(text)
    return "<br>".join(html.escape(line) for line in lines if line)


def _details_cell(declaration: dict) -> str:
    """Form attributes and payloads that have no column of their own."""
    parts = []
    extra = {
        attribute: value
        for attribute, value in declaration["attributes"].items()
        if attribute not in _COLUMN_ATTRIBUTES
    }
    if extra:
        parts.append(
            "<span class=muted>"
            + ", ".join(
                f"{html.escape(k)}={html.escape(v)}" for k, v in sorted(extra.items())
            )
            + "</span>"
        )
    for payload in declaration["payloads"]:
        if payload["payloadKind"] not in _COLUMN_KINDS:
            parts.append(f"<code>{html.escape(payload['content'])}</code>")
    return " ".join(parts)


def _search_key(qualified: str, declared: dict) -> str:
    """What the page's filter matches against: the type's name,
    description and projection label, every parameter and port name it
    declares with the parameters' descriptions and help text, and a
    ``projected`` facet word for the chip. The filter then also answers
    which model states this parameter."""
    label = _projection_label(qualified)
    terms = [qualified, declared["description"] or "", label]
    if label.startswith("cim:") and "Analog" not in label:
        terms.append("projected")
    terms += [declaration["name"] for declaration in declared["descriptors"]]
    terms += [
        declaration["description"] or "" for declaration in declared["descriptors"]
    ]
    terms += [
        _stated_text(payload["content"])
        for declaration in declared["descriptors"]
        for payload in declaration["payloads"]
        if payload["payloadKind"] == "help"
    ]
    terms += [port["portName"] for port in declared["ports"]]
    return html.escape(" ".join(t for t in terms if t).lower(), quote=True)


def _counts(declared: dict) -> str:
    parts = []
    for amount, noun in (
        (len(declared["descriptors"]), "parameter"),
        (len(declared["ports"]), "port"),
    ):
        if amount:
            parts.append(f"{amount} {noun}" + ("s" if amount != 1 else ""))
    return ", ".join(parts)


def _write_definition(out: io.StringIO, qualified: str, declared: dict) -> None:
    label = _projection_label(qualified)
    counts = _counts(declared)
    out.write(
        f'<details class=type open id="{html.escape(qualified, quote=True)}"'
        f' data-key="{_search_key(qualified, declared)}">\n'
        f"<summary><h2>{html.escape(qualified)}</h2>"
        + (f"<span class=tag>{html.escape(label)}</span>" if label else "")
        + (f"<span class=muted> {counts}</span>" if counts else "")
        + "</summary>\n"
    )
    if declared["description"]:
        out.write(f"<p>{html.escape(declared['description'])}</p>\n")
    out.write(f"<p class=annotation>{html.escape(_annotation(qualified))}</p>\n")
    category_names = {
        category["sequenceNumber"]: category["name"]
        for category in declared["categories"]
    }
    gated = [
        category
        for category in declared["categories"]
        if category["condition"] or category["visible"] == "false"
    ]
    if gated:
        out.write(
            "<p class=muted>Gated categories: "
            + "; ".join(
                html.escape(
                    f"{category['name']}"
                    + (
                        f" when {category['condition']}"
                        if category["condition"]
                        else ""
                    )
                    + ("" if category["visible"] != "false" else " (hidden)")
                )
                for category in gated
            )
            + "</p>\n"
        )
    if declared["descriptors"]:
        out.write(
            "<div class=scroll><div class=scroll-body><table>\n<tr><th>#</th><th>parameter</th>"
            "<th>category</th><th>group</th><th>type</th>"
            "<th>content type</th><th>dimension</th><th>intent</th>"
            "<th>unit</th><th>default</th><th>min</th><th>max</th>"
            "<th>condition</th><th>visible when</th><th>description</th>"
            "<th>help</th><th>choices</th><th>pattern</th><th>error</th>"
            "<th>table</th><th>details</th></tr>\n"
        )
        for declaration in declared["descriptors"]:
            attributes = declaration["attributes"]
            cells = (
                declaration["sequenceNumber"],
                declaration["name"],
                category_names.get(declaration["category"], ""),
                attributes.get("group", ""),
                attributes.get("parameterType", ""),
                attributes.get("contentType", ""),
                attributes.get("dimension", ""),
                attributes.get("intent", ""),
                declaration["engineeringUnit"] or "",
                declaration["typicalValue"] or "",
                attributes.get("minimum", ""),
                attributes.get("maximum", ""),
                attributes.get("condition", ""),
            )
            out.write(
                "<tr>"
                + "".join(f"<td>{html.escape(cell)}</td>" for cell in cells)
                + f"<td>{_column_cell(declaration, 'vis')}</td>"
                + f"<td>{html.escape(declaration['description'] or '')}</td>"
                + f"<td>{_column_cell(declaration, 'help')}</td>"
                + f"<td>{_column_cell(declaration, 'choice')}</td>"
                + f"<td>{_column_cell(declaration, 'regex')}</td>"
                + f"<td>{_column_cell(declaration, 'error_msg')}</td>"
                + f"<td>{_table_cell(declaration)}</td>"
                + f"<td>{_details_cell(declaration)}</td></tr>\n"
            )
        out.write("</table></div><div class=scroll-bar aria-hidden=true><div></div></div></div>\n")
    if declared["ports"]:
        out.write(
            "<div class=scroll><div class=scroll-body><table>\n<tr><th>#</th><th>port</th><th>mode</th>"
            "<th>dimension</th><th>data type</th>"
            "<th>electrical type</th><th>internal</th>"
            "<th>condition</th></tr>\n"
        )
        for port in declared["ports"]:
            cells = (
                port["sequenceNumber"],
                port["portName"],
                port["mode"] or "",
                port["dimension"] or "",
                port["dataType"] or "",
                port["electricalType"] or "",
                port["internal"] or "",
                port["condition"] or "",
            )
            out.write(
                "<tr>"
                + "".join(f"<td>{html.escape(cell)}</td>" for cell in cells)
                + "</tr>\n"
            )
        out.write("</table></div><div class=scroll-bar aria-hidden=true><div></div></div></div>\n")
    out.write("</details>\n")


def catalog_html(surface: dict[str, dict]) -> str:
    """The whole page, from :func:`pscx.catalog.read_catalog`'s shape."""
    ordered = sorted(surface)
    first = surface[ordered[0]] if ordered else {}
    tool = first.get("modelingTool") or "library"
    version = first.get("toolVersion") or ""
    title = f"Library catalog — {tool} {version}".rstrip()
    out = io.StringIO()
    out.write(
        "<!doctype html>\n<html lang=en>\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name=viewport content="width=device-width, '
        'initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>\n{_STYLE}</style>\n</head>\n<body>\n"
    )
    out.write(f"<h1>{html.escape(title)}</h1>\n")
    descriptors = sum(len(d["descriptors"]) for d in surface.values())
    ports = sum(len(d["ports"]) for d in surface.values())
    out.write(
        f"<p>{len(surface)} model types, {descriptors} parameter "
        f"declarations, {ports} ports. A case document joins this "
        "catalog by <code>(modelingTool, toolVersion, "
        "definitionName)</code>, not by mRID. Every placement of a "
        "type below is written to the EMT document as a "
        "<code>cim:DetailedModelDynamics</code> naming that type. The "
        "annotation on each section states what <code>pscx emit</code> "
        "also writes into standard CIM, taken from the emitter's rule "
        "tables when the page is rendered. The catalog document itself "
        "states only the library side.</p>\n"
        "<p class=muted>Projection-column labels: <b>triaged "
        "STANDARD</b> = a standard class exists (named as the "
        "candidate) and the projection is planned; <b>triaged EMT "
        "CLASS</b> = standard CIM has no class, and a typed "
        "<code>emt:</code> class is planned; <b>triaged DETAILED "
        "MODEL</b> = the kind is specific to PSCAD and is exchanged as "
        "a detailed model only; <b>triaged STRUCTURAL</b> = a topology "
        "role the connectivity graph already expresses. A blank cell is "
        "the common case: the type is exchanged as a detailed model "
        "only.</p>\n"
        f"<p class=muted>Document profile: "
        f"<code>{html.escape(EMT_LIBRARY_PROFILE_URI)}</code>. The "
        "profile's class documentation and diagram ship with the "
        "published vocabulary (<code>EMT.md</code> beside "
        "<code>EMT-AP-Voc-RDFS.rdf</code>).</p>\n"
    )
    out.write(
        "<div class=toolbar>"
        "<input id=q type=search placeholder="
        '"Filter by name, description or projection">'
        "<button type=button data-q=projected>projected</button>"
        "<button type=button data-q=triaged>triaged</button>"
        '<button type=button data-q="cim:analog">meters</button>'
        "<button id=open-all type=button>expand all</button>"
        "<button id=close-all type=button>collapse all</button>"
        f"<span id=count class=muted>{len(surface)} model types</span>"
        "</div>\n"
    )
    out.write(
        "<div class=scroll><div class=scroll-body><table>\n"
        "<tr><th>model type</th><th>description</th>"
        "<th>standard projection</th>"
        "<th>parameters</th><th>ports</th></tr>\n"
    )
    for qualified in ordered:
        declared = surface[qualified]
        out.write(
            f'<tr data-key="{_search_key(qualified, declared)}">'
            f'<td><a href="#{html.escape(qualified, quote=True)}">'
            f"{html.escape(qualified)}</a></td>"
            f"<td>{html.escape(declared['description'] or '')}</td>"
            f"<td>{html.escape(_projection_label(qualified))}</td>"
            f"<td>{len(declared['descriptors'])}</td>"
            f"<td>{len(declared['ports'])}</td></tr>\n"
        )
    out.write("</table></div><div class=scroll-bar aria-hidden=true><div></div></div></div>\n")
    for qualified in ordered:
        _write_definition(out, qualified, surface[qualified])
    out.write("<script>\n" + _SCRIPT + "</script>\n</body>\n</html>\n")
    return out.getvalue()


def render_catalog_html(catalog_path: str, out_path: str) -> int:
    """Read a written catalog and write its page; the number of model
    types rendered comes back for the CLI's summary line."""
    surface = read_catalog(catalog_path)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(catalog_html(surface))
    return len(surface)

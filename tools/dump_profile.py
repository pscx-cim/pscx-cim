"""Dump the class/property structure of a CIM RDFS profile file.

    python tools/dump_profile.py PROFILE.rdf              # indented text
    python tools/dump_profile.py PROFILE.rdf --markdown   # Markdown + Mermaid

The Markdown form is what makes a schema change reviewable: an RDF/XML
diff is unreadable, while a diff of prose plus a class diagram shows what
actually moved. pyLODE is not used because it ignores the cims: extension
vocabulary (multiplicity, AssociationUsed, stereotype), which is where a
CIM profile keeps its meaning.
"""
import argparse
import sys
import xml.etree.ElementTree as ET

RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
RDFS = "{http://www.w3.org/2000/01/rdf-schema#}"
CIMS = "{http://iec.ch/TC57/1999/rdf-schema-extensions-19990926#}"


def res(el):
    return el.get(RDF + "resource") if el is not None else None


def frag(uri):
    if uri is None:
        return None
    return uri.split("#")[-1]


def parse(path):
    """``(classes, properties, header)`` of one RDFS profile file."""
    root = ET.parse(path).getroot()
    classes = {}   # name -> {kind, sub, st, comment}
    props = []     # (domain, name, range/datatype, mult, assoc_used, inverse)
    header = []

    for d in root:
        about = d.get(RDF + "about") or d.get(RDF + "ID") or ""
        types = [res(t) for t in d.findall(RDF + "type")]
        stereos = [res(s) or s.text for s in d.findall(CIMS + "stereotype")]
        comment = d.findtext(RDFS + "comment")
        if any(t and t.endswith("rdf-schema#Class") for t in types):
            kind = ("enum" if "enumeration" in [frag(s) for s in stereos if s]
                    else "class")
            classes.setdefault(frag(about), {}).update(
                kind=kind, sub=frag(res(d.find(RDFS + "subClassOf"))),
                st=stereos, comment=comment,
                sub_uri=res(d.find(RDFS + "subClassOf")))
        elif any(t and t.endswith("#Property") for t in types):
            dom = frag(res(d.find(RDFS + "domain")))
            rng_uri = res(d.find(RDFS + "range"))
            dt = frag(res(d.find(CIMS + "dataType")))
            mult = frag(res(d.find(CIMS + "multiplicity")))
            used = d.findtext(CIMS + "AssociationUsed")
            inv = frag(res(d.find(CIMS + "inverseRoleName")))
            props.append((dom, frag(about), frag(rng_uri) or dt, mult, used,
                          inv, rng_uri, comment))
        elif (about.endswith("Ontology")
              or ("Profile" in about and "Package" not in about)):
            for c in d:
                if c.text and c.text.strip():
                    header.append((c.tag.split("}")[-1], c.text.strip()))
                elif res(c):
                    header.append((c.tag.split("}")[-1], res(c)))
    return classes, props, header


HEADER_KEYS = ("baseURI", "versionIRI", "priorVersion", "backwardCompatibleWith",
               "keyword", "title", "identifier", "versionInfo", "conformsTo",
               "isProfileOf")


def dump_text(classes, props, header, out):
    print("=== HEADER (profile identity) ===", file=out)
    for k, v in header:
        if k in HEADER_KEYS:
            print(f"  {k}: {v}", file=out)

    print("\n=== CLASSES ===", file=out)
    for name, info in classes.items():
        if info.get("kind") == "enum":
            continue
        sts = ",".join(frag(s) or "?" for s in info.get("st") or [])
        print(f"  {name}  (subClassOf {info.get('sub')})  [{sts}]", file=out)
        for dom, p, rng, mult, used, inv, _uri, _c in props:
            if dom == name:
                m = (mult or "").replace("M:", "")
                arrow = f"-> {rng}" if used != "No" else f": {rng}"
                tag = " (assoc)" if used == "Yes" else (
                    "" if used is None else " (inverse-only)")
                print(f"      {p:55s} {arrow:30s} [{m}]{tag}", file=out)

    external = _external_properties(classes, props)
    if external:
        print("\n=== ATTRIBUTES ON FOREIGN CLASSES ===", file=out)
        for dom, p, rng, mult, _used, _inv, _uri, _c in external:
            m = (mult or "").replace("M:", "")
            print(f"  {dom}: {p:45s} : {rng:20s} [{m}]", file=out)

    enums = [n for n, i in classes.items() if i.get("kind") == "enum"]
    print("\n=== ENUMS ===", ", ".join(enums), file=out)


def _external_properties(classes, props):
    """Properties whose domain is a class this profile does not declare.

    An attribute that genuinely belongs on an existing CIM class is
    declared in our namespace with ClassName.attribute naming, so its
    domain is foreign. It has no owning section in the class listing, and
    a schema change nobody can read in the diff is the one thing this
    renderer exists to prevent.
    """
    return [p for p in sorted(props)
            if p[0] is not None and p[0] not in classes]


def _one_line(text):
    return " ".join((text or "").split())


def _is_concrete(info):
    return any((frag(s) or "") == "concrete" for s in info.get("st") or [])


def _mermaid_name(uri, local):
    """A class node label. Foreign classes (another namespace) keep a
    prefix so the diagram shows where the profile ends."""
    if uri and not uri.startswith("#"):
        return "cim_" + local if "CIM100" in uri else local
    return local


def dump_markdown(classes, props, header, out, title="Profile"):
    seen = {k: v for k, v in header}
    print(f"# {seen.get('title', title)}\n", file=out)
    print("<!-- GENERATED by tools/dump_profile.py --markdown; do not edit "
          "by hand. -->\n", file=out)
    for key in HEADER_KEYS:
        if key in seen:
            print(f"- **{key}**: {seen[key]}", file=out)
    if "description" in seen:
        print(f"\n{_one_line(seen['description'])}", file=out)

    print("\n## Class diagram\n", file=out)
    print("```mermaid", file=out)
    print("classDiagram", file=out)
    for name, info in sorted(classes.items()):
        if info.get("kind") == "enum":
            continue
        if info.get("sub"):
            parent = _mermaid_name(info.get("sub_uri"), info["sub"])
            print(f"    {parent} <|-- {name}", file=out)
        if not _is_concrete(info):
            print(f"    <<abstract>> {name}", file=out)
    for dom, p, rng, mult, used, _inv, rng_uri, _c in sorted(props):
        if used != "Yes" or dom is None:
            continue
        target = _mermaid_name(rng_uri, rng)
        bound = (mult or "").replace("M:", "")
        print(f'    {dom} --> "{bound}" {target} : {p.split(".", 1)[-1]}',
              file=out)
    print("```\n", file=out)

    print("## Classes\n", file=out)
    for name, info in sorted(classes.items()):
        if info.get("kind") == "enum":
            continue
        stereo = "concrete" if _is_concrete(info) else "abstract"
        parent = info.get("sub") or "-"
        print(f"### `{name}` ({stereo}, subClassOf `{parent}`)\n", file=out)
        if info.get("comment"):
            print(f"{_one_line(info['comment'])}\n", file=out)
        owned = [p for p in sorted(props) if p[0] == name]
        if not owned:
            print("_No own properties._\n", file=out)
            continue
        print("| property | type | multiplicity | kind |", file=out)
        print("|---|---|---|---|", file=out)
        for _dom, p, rng, mult, used, _inv, _uri, comment in owned:
            kind = "association" if used == "Yes" else "attribute"
            bound = (mult or "").replace("M:", "")
            print(f"| `{p.split('.', 1)[-1]}` | `{rng}` | {bound} | {kind} |",
                  file=out)
        print(file=out)
        for _dom, p, _rng, _mult, _used, _inv, _uri, comment in owned:
            if comment:
                short = p.split(".", 1)[-1]
                print(f"- **{short}**: {_one_line(comment)}", file=out)
        print(file=out)

    external = _external_properties(classes, props)
    if external:
        print("## Attributes on standard CIM classes\n", file=out)
        print("Declared in this namespace with `ClassName.attribute` naming "
              "(the idiom `eu:`, `entsoe:` and `nc:` use), stated in the "
              "add-on document and joined by IRI. Nothing is added to `cim:` "
              "and no standard document is written to.\n", file=out)
        print("| class | property | type | multiplicity | kind |", file=out)
        print("|---|---|---|---|---|", file=out)
        for dom, p, rng, mult, used, _inv, _uri, _comment in external:
            kind = "association" if used == "Yes" else "attribute"
            bound = (mult or "").replace("M:", "")
            print(f"| `cim:{dom}` | `{p.split('.', 1)[-1]}` | `{rng}` | "
                  f"{bound} | {kind} |", file=out)
        print(file=out)
        for dom, p, _rng, _mult, _used, _inv, _uri, comment in external:
            if comment:
                print(f"- **{p}**: {_one_line(comment)}", file=out)
        print(file=out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dump a CIM RDFS profile.")
    parser.add_argument("profile", help="path to the RDFS file")
    parser.add_argument("--markdown", action="store_true",
                        help="render Markdown with a Mermaid class diagram")
    parser.add_argument("--out", help="write to this file instead of stdout")
    args = parser.parse_args(argv)

    classes, props, header = parse(args.profile)
    render = dump_markdown if args.markdown else dump_text
    if args.out:
        with open(args.out, "w") as stream:
            render(classes, props, header, stream)
    else:
        render(classes, props, header, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

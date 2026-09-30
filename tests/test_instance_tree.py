"""Which pages the instance tree descends into.

Descent into a sibling library page or a workspace project is a property
of the walk, so it is asserted on `flatten`'s output.
"""

_LIBRARY = """\
<project name="shelf" version="5.0.1" schema="" Target="Library">
  <definitions>
    <Definition classid="UserCmpDefn" name="Marker" id="1">
      <graphics/>
    </Definition>
    <Definition classid="UserCmpDefn" name="Box" id="2">
      <schematic classid="UserCanvas">
        <User classid="UserCmp" id="10" x="0" y="0" orient="0"
              defn="shelf:Marker" disable="false"/>
      </schematic>
    </Definition>
    <Definition classid="UserCmpDefn" name="Geometry" id="3">
      <schematic classid="RowCanvas">
        <User classid="UserCmp" id="11" x="0" y="0" orient="0"
              defn="shelf:Marker" disable="false"/>
      </schematic>
    </Definition>
    <Definition classid="UserCmpDefn" name="Spare" id="4">
      <schematic classid="UserCanvas">
        <User classid="UserCmp" id="12" x="0" y="0" orient="0"
              defn="shelf:Marker" disable="false"/>
      </schematic>
    </Definition>
    <Definition classid="UserCmpDefn" name="Main" id="5">
      <schematic classid="UserCanvas">
        <User classid="UserCmp" id="13" x="0" y="0" orient="0"
              defn="shelf:Marker" disable="false"/>
      </schematic>
    </Definition>
  </definitions>
</project>
"""

_CASE = """\
<project name="plant" version="5.0.1" schema="" Target="EMTDC">
  <definitions>
    <Definition classid="StationDefn" name="DS" id="1">
      <schematic classid="StationCanvas">
        <Wire classid="WireBranch" id="90" name="STUB" x="0" y="0"
              orient="0" defn="plant:Main" disable="false">
          <vertex x="0" y="0"/>
          <vertex x="0" y="18"/>
          <User classid="UserCmp" id="90" x="0" y="0" orient="0"
                defn="plant:Main" disable="false"/>
        </Wire>
      </schematic>
    </Definition>
    <Definition classid="UserCmpDefn" name="Main" id="2">
      <schematic classid="UserCanvas">
        <User classid="UserCmp" id="21" x="0" y="0" orient="0"
              defn="shelf:Box" disable="false"/>
        <User classid="UserCmp" id="22" x="36" y="0" orient="0"
              defn="shelf:Box" disable="false"/>
        <User classid="UserCmp" id="23" x="72" y="0" orient="0"
              defn="shelf:Geometry" disable="false"/>
      </schematic>
    </Definition>
    <Definition classid="UserCmpDefn" name="Dead" id="3">
      <schematic classid="UserCanvas">
        <User classid="UserCmp" id="24" x="0" y="0" orient="0"
              defn="shelf:Marker" disable="false"/>
      </schematic>
    </Definition>
  </definitions>
</project>
"""


def test_a_sibling_library_page_is_an_instance(tmp_path):
    # A placed library UserCanvas is a child instance. A RowCanvas in the
    # same library is not, an unplaced library page is not built, and the
    # library's own Main does not replace the case's.
    from pscx.diagnostics import DIAGNOSTICS
    from pscx.elaborate import flatten

    DIAGNOSTICS.clear()
    (tmp_path / "shelf.pslx").write_text(_LIBRARY)
    case = tmp_path / "plant.pscx"
    case.write_text(_CASE)
    flat = flatten(str(case))

    assert [inst.canvas for inst in flat.instances] == ["Main", "Box", "Box"]
    boxes = [inst for inst in flat.instances if inst.canvas == "Box"]
    assert boxes[0].netlist is boxes[1].netlist
    assert {comp.defn for comp in boxes[0].netlist.components} == {
        "shelf:Marker"
    }
    assert boxes[0].netlist.namespace == "shelf"
    root = flat.instances[0]
    assert {comp.defn for comp in root.netlist.components} == {
        "shelf:Box", "shelf:Geometry",
    }
    assert len(root.module_component_ids) == 2
    assert flat.netlists["Main"] is root.netlist
    assert flat.orphan_canvases == ["Dead"]
    assert ("shelf", "Spare") not in flat.pages
    assert ("shelf", "Main") not in flat.pages
    assert ("shelf", "Geometry") not in flat.pages
    assert flat.page("shelf:Box") is boxes[0].netlist


_REMOTE = """\
<project name="remote" version="5.0.1" schema="" Target="EMTDC">
  <definitions>
    <Definition classid="UserCmpDefn" name="Marker" id="1">
      <graphics/>
    </Definition>
    <Definition classid="UserCmpDefn" name="Box" id="2">
      <schematic classid="UserCanvas">
        <User classid="UserCmp" id="10" x="0" y="0" orient="0"
              defn="remote:Marker" disable="false"/>
      </schematic>
    </Definition>
    <Definition classid="UserCmpDefn" name="Main" id="3">
      <schematic classid="UserCanvas">
        <User classid="UserCmp" id="11" x="0" y="0" orient="0"
              defn="remote:Marker" disable="false"/>
      </schematic>
    </Definition>
  </definitions>
</project>
"""

_PLANT = """\
<project name="plant" version="5.0.1" schema="" Target="EMTDC">
  <definitions>
    <Definition classid="StationDefn" name="DS" id="1">
      <schematic classid="StationCanvas">
        <Wire classid="WireBranch" id="90" name="STUB" x="0" y="0"
              orient="0" defn="plant:Main" disable="false">
          <vertex x="0" y="0"/>
          <vertex x="0" y="18"/>
          <User classid="UserCmp" id="90" x="0" y="0" orient="0"
                defn="plant:Main" disable="false"/>
        </Wire>
      </schematic>
    </Definition>
    <Definition classid="UserCmpDefn" name="Main" id="2">
      <schematic classid="UserCanvas">
        <User classid="UserCmp" id="21" x="0" y="0" orient="0"
              defn="remote:Box" disable="false"/>
      </schematic>
    </Definition>
  </definitions>
</project>
"""


def test_a_workspace_filepath_is_a_loaded_project(tmp_path, monkeypatch):
    # The page's project is not beside the case. The workspace names it,
    # names a file that is not on disk, and names the case itself.
    import os

    from pscx import hir
    from pscx.diagnostics import DIAGNOSTICS
    from pscx.elaborate import flatten

    plant = tmp_path / "plant"
    studio = tmp_path / "studio"
    plant.mkdir()
    studio.mkdir()
    case = plant / "plant.pscx"
    case.write_text(_PLANT)
    (studio / "remote.pscx").write_text(_REMOTE)
    # Windows separators, as a workspace written by PSCAD uses them.
    (studio / "studio.pswx").write_text(
        "<workspace name=\"studio\" version=\"5.0.1\">\n"
        "  <projects>\n"
        "    <project type=\"project\" name=\"remote\" "
        "filepath=\".\\remote.pscx\" />\n"
        "    <project type=\"library\" name=\"gone\" "
        "filepath=\".\\gone.pslx\" />\n"
        "    <project type=\"project\" name=\"plant\" "
        "filepath=\"..\\plant\\plant.pscx\" />\n"
        "  </projects>\n"
        "</workspace>\n"
    )

    DIAGNOSTICS.clear()
    alone = flatten(str(case))
    assert [inst.canvas for inst in alone.instances] == ["Main"]
    assert ("remote", "Box") not in alone.pages

    counts: dict[str, int] = {}
    real = hir.load_project

    def spy(path, bus=None):
        key = os.path.realpath(path)
        counts[key] = counts.get(key, 0) + 1
        return real(path, bus)

    monkeypatch.setattr(hir, "load_project", spy)
    DIAGNOSTICS.clear()
    flat = flatten(str(case), workspaces=[str(studio / "studio.pswx")])

    assert counts[os.path.realpath(case)] == 1
    assert [inst.canvas for inst in flat.instances] == ["Main", "Box"]
    assert flat.instances[0].netlist.namespace == "plant"
    box = flat.instances[1]
    assert box.netlist.namespace == "remote"
    assert {comp.defn for comp in box.netlist.components} == {"remote:Marker"}
    assert ("remote", "Main") not in flat.pages
    assert any(record.code == "unparseable_library"
               and record.detail == "gone.pslx"
               for record in DIAGNOSTICS.records())

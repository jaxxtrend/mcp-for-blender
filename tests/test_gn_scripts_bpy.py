"""The Geometry Nodes scripts against a real Blender, through the bpy module.

Skipped unless bpy is importable (pip install bpy, on the Python version that
Blender release targets). Each test runs a script the way execute_code does.
"""
import contextlib
import copy
import io
import json

import pytest

bpy = pytest.importorskip("bpy")

from blender_mcp import blender_scripts  # noqa: E402


def run(script, **args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(blender_scripts.build(script, args), {})
    return blender_scripts.parse_result(out.getvalue())


def get(**args):
    return run(blender_scripts.GN_TREE, **args)


def comparable(tree):
    """A tree dump without the ids a rebuild may renumber: sockets by name, links by socket name."""
    tree = copy.deepcopy(tree)
    tree.pop("name")
    names = {}
    for node in tree["nodes"]:
        for side in ("inputs", "outputs"):
            for socket in node.get(side, []):
                names[(node["name"], side, socket["id"])] = socket.pop("name", socket["id"])
                socket.pop("id")
    for item in tree["interface"]:
        item.pop("id", None)
    for link in tree["links"]:
        link["from"] = [link["from"][0], names.get((link["from"][0], "outputs", link["from"][1]), link["from"][1])]
        link["to"] = [link["to"][0], names.get((link["to"][0], "inputs", link["to"][1]), link["to"][1])]
    tree["links"].sort(key=json.dumps)
    return tree


@pytest.fixture(autouse=True)
def empty_file():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def scatter_scene():
    bpy.ops.mesh.primitive_plane_add(size=4)
    ground = bpy.context.active_object
    ground.name = "Ground"
    bpy.ops.mesh.primitive_cube_add(size=0.2)
    bpy.context.active_object.name = "Rock"
    tree = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
    tree.is_modifier = True
    iface = tree.interface
    iface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    density = iface.new_socket("Density", in_out="INPUT", socket_type="NodeSocketFloat")
    density.default_value = 10.0
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes = tree.nodes
    group_in, group_out = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    distribute = nodes.new("GeometryNodeDistributePointsOnFaces")
    cube = nodes.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (0.1, 0.1, 0.1)
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    join = nodes.new("GeometryNodeJoinGeometry")
    repeat_in, repeat_out = nodes.new("GeometryNodeRepeatInput"), nodes.new("GeometryNodeRepeatOutput")
    repeat_in.pair_with_output(repeat_out)
    repeat_out.repeat_items.new("FLOAT", "Offset")
    link = tree.links.new
    link(group_in.outputs["Geometry"], distribute.inputs["Mesh"])
    link(group_in.outputs["Density"], distribute.inputs["Density"])
    link(distribute.outputs["Points"], instance.inputs["Points"])
    link(cube.outputs["Mesh"], instance.inputs["Instance"])
    link(instance.outputs["Instances"], join.inputs["Geometry"])
    link(group_in.outputs["Geometry"], join.inputs["Geometry"])
    link(join.outputs["Geometry"], group_out.inputs["Geometry"])
    modifier = ground.modifiers.new("Scatter", "NODES")
    modifier.node_group = tree
    modifier[density.identifier] = 25.0
    return ground, density.identifier


def test_every_node_type_survives_a_round_trip():
    tree = bpy.data.node_groups.new("All", "GeometryNodeTree")
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name)
        if isinstance(cls, type) and issubclass(cls, bpy.types.Node) and cls is not bpy.types.Node:
            try:
                tree.nodes.new(name)
            except RuntimeError:
                pass
    for node in tree.nodes:
        for key in ("repeat_items", "state_items", "capture_items", "main_items", "generation_items"):
            if hasattr(node, key):
                getattr(node, key).new("FLOAT", "Extra")
        if node.bl_idname == "GeometryNodeMenuSwitch":
            node.enum_items.new("Third")
    dump = get(tree="All")["tree"]
    assert run(blender_scripts.GN_BUILD, spec=dump | {"name": "All2"}, tree="All2")["nodes"] == len(dump["nodes"])
    assert comparable(get(tree="All2")["tree"]) == comparable(dump)


def test_rebuild_keeps_modifier_values_and_adds_inputs():
    ground, density_id = scatter_scene()
    spec = get(object="Ground")["tree"]
    spec["interface"].append({"in_out": "INPUT", "name": "Seed", "type": "NodeSocketInt", "default": 3})
    result = run(blender_scripts.GN_BUILD, spec=spec, object="Ground")
    assert "error" not in result
    inputs = get(object="Ground")["modifier"]["inputs"]
    assert inputs[density_id]["value"] == 25.0
    assert {v["name"]: v["value"] for v in inputs.values()}["Seed"] == 3
    assert sorted(g.name for g in bpy.data.node_groups) == ["Scatter"]


def test_a_failed_build_leaves_the_tree_untouched():
    scatter_scene()
    before = get(object="Ground")
    bad = before["tree"] | {"links": before["tree"]["links"] + [{"from": ["Cube", "Nope"], "to": ["Join Geometry", "Geometry"]}]}
    assert "has no output 'Nope'" in run(blender_scripts.GN_BUILD, spec=bad, object="Ground")["error"]
    assert get(object="Ground") == before
    assert sorted(g.name for g in bpy.data.node_groups) == ["Scatter"]


def test_set_inputs_and_evaluated_info():
    _, density_id = scatter_scene()
    result = run(blender_scripts.GN_SET_INPUTS, object="Ground", modifier_inputs={"Density": 4.0},
                 node_inputs={"Cube": {"Size": [0.2, 0.2, 0.2]}},
                 node_props={"Distribute Points on Faces": {"distribute_method": "RANDOM"}})
    assert result == {"changed": ["Distribute Points on Faces.distribute_method", "Cube.Size", f"modifier.{density_id}"]}
    info = run(blender_scripts.GN_EVALUATED, object="Ground")
    assert info["mesh"]["faces"] == 1
    assert info["instances"]["count"] == 64
    assert info["size"][2] == pytest.approx(0.2, abs=0.01)
    reply = run(blender_scripts.GN_SET_INPUTS, object="Ground", node_inputs={"Cube": {"Nope": 1}})
    assert "has no input 'Nope'" in reply["error"] and reply["changed"] == []


def test_node_types():
    listed = run(blender_scripts.GN_NODE_TYPES, query="points distribute")
    assert ["GeometryNodeDistributePointsOnFaces", "Distribute Points on Faces"] in listed["types"]
    compare = run(blender_scripts.GN_NODE_TYPES, node_type="FunctionNodeCompare", props={"data_type": "VECTOR"})
    assert "mode" in {s["name"] for s in compare["settings"]}
    assert {s["type"] for s in compare["inputs"] if s["name"] == "A"} == {"VECTOR"}
    assert "error" in run(blender_scripts.GN_NODE_TYPES, node_type="GeometryNodeNope")
    assert "__mcp_gn_probe__" not in bpy.data.node_groups


def test_probe_measures_a_socket_without_changing_the_tree():
    ground, _ = scatter_scene()
    before = get(object="Ground")
    points = run(blender_scripts.GN_PROBE, object="Ground", node="Distribute Points on Faces", socket="Points")
    assert points["socket_type"] == "GEOMETRY" and points["pointcloud"]["points"] == 400
    tree = bpy.data.node_groups["Scatter"]
    index = tree.nodes.new("GeometryNodeInputIndex")
    index.name = "Index"
    capture = tree.nodes.new("GeometryNodeSetID")
    tree.links.new(tree.nodes["Distribute Points on Faces"].outputs["Points"], capture.inputs["Geometry"])
    tree.links.new(index.outputs["Index"], capture.inputs["ID"])
    before = get(object="Ground")
    field = run(blender_scripts.GN_PROBE, object="Ground", node="Index", socket="Index", sample=3)
    assert field["evaluated_on"] == ["Distribute Points on Faces", "Points"]
    assert field["components"]["pointcloud"]["sample"] == [0, 1, 2]
    assert field["components"]["pointcloud"]["max"] == 399
    assert get(object="Ground") == before
    assert sorted(g.name for g in bpy.data.node_groups) == ["Scatter"]


def test_read_attribute():
    scatter_scene()
    position = run(blender_scripts.GN_READ_ATTRIBUTE, object="Ground", name="position")["components"]
    assert position["mesh"]["count"] == 4 and position["mesh"]["min"] == [-2.0, -2.0, 0.0]
    assert "no attribute 'nope'" in run(blender_scripts.GN_READ_ATTRIBUTE, object="Ground", name="nope")["error"]


def test_patch_applies_ops_in_order_or_not_at_all():
    scatter_scene()
    reply = run(blender_scripts.GN_PATCH, object="Ground", ops=[
        {"op": "add_node", "name": "Realize", "type": "GeometryNodeRealizeInstances"},
        {"op": "link", "from": ["Join Geometry", "Geometry"], "to": ["Realize", "Geometry"]},
        {"op": "link", "from": ["Realize", "Geometry"], "to": ["Group Output", "Geometry"]},
        {"op": "add_socket", "in_out": "INPUT", "name": "Seed", "type": "NodeSocketInt", "default": 7},
    ])
    assert reply["ops"] == 4 and "Seed" in reply["added_sockets"]
    info = run(blender_scripts.GN_EVALUATED, object="Ground")
    assert "instances" not in info and info["mesh"]["faces"] == 1 + 400 * 6
    before = get(object="Ground")
    reply = run(blender_scripts.GN_PATCH, object="Ground", ops=[
        {"op": "remove_node", "name": "Realize"}, {"op": "rename", "name": "Cube", "new_name": "Join Geometry"}])
    assert reply["error"] == "op 1 (rename): 'Scatter' already has a node named 'Join Geometry'"
    assert get(object="Ground") == before
    assert sorted(g.name for g in bpy.data.node_groups) == ["Scatter"]

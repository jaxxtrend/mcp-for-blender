"""Scripts the server runs inside Blender through the addon's execute_code command.

Building observation tools out of execute_code instead of new addon commands
means they work with every addon already installed; only this package has to
update. Each script reads its arguments from an `ARGS` dict and prints one
RESULT_MARKER line carrying its JSON result, which `parse_result` picks out of
whatever else the script printed.
"""

import json

RESULT_MARKER = "__MCP_RESULT__"


def build(script: str, args: dict) -> str:
    """Wrap a script body in a function, so it can `return` its result early.

    The addon catches Exception around execute_code, but not SystemExit, so an
    early exit must never be spelled as one.
    """
    body = "\n".join("    " + line if line.strip() else "" for line in script.strip("\n").splitlines())
    # json.loads of a Python string literal, since JSON's true/null aren't Python.
    return (
        "import json as _json\n"
        f"ARGS = _json.loads({json.dumps(json.dumps(args))})\n"
        f"def _main():\n{body}\n"
        f"print({RESULT_MARKER!r} + _json.dumps(_main()))\n"
    )


def parse_result(output: str) -> dict:
    for line in reversed((output or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER):])
    raise ValueError("Blender script finished without a result")


# One line per object, carrying only the fields the caller asked for, so the
# default listing stays a few tokens per object. Dimensions come from the
# evaluated world bounding box, which is what matters for placement (modifiers
# and parent scale included), and are only computed when asked for.
# In the order they appear on a line.
SCENE_FIELDS = ("location", "rotation", "scale", "size", "ground", "parent", "details", "materials",
                "modifiers", "animation", "hidden", "children", "topology", "weights", "settings")
SCENE_DEFAULT_FIELDS = ("location", "size", "children", "hidden")

SCENE_SUMMARY = r'''
import bpy, math
from mathutils import Vector

scene = bpy.context.scene
F = set(ARGS.get("fields") or ())
depsgraph = bpy.context.evaluated_depsgraph_get() if F & {"size", "ground"} else None
limit = max(1, min(int(ARGS.get("limit") or 20), 500))
query = (ARGS.get("query") or "").strip().lower()
root_name = ARGS.get("root")

def r(v):
    return round(float(v), 2)

def bounds(obj):
    if obj.type in {"EMPTY", "LIGHT", "CAMERA"}:
        return None
    try:
        ev = obj.evaluated_get(depsgraph)
        pts = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    except Exception:
        return None
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return lo, hi

def line(obj, depth=0):
    parts = [("  " * depth) + obj.name, obj.type.lower()]
    if "location" in F:
        loc = obj.matrix_world.translation
        parts.append(f"at ({r(loc.x)}, {r(loc.y)}, {r(loc.z)})")
    if "rotation" in F:
        e = obj.matrix_world.to_euler()
        parts.append(f"rot ({round(math.degrees(e.x))}, {round(math.degrees(e.y))}, {round(math.degrees(e.z))})")
    if "scale" in F:
        s = obj.matrix_world.to_scale()
        parts.append(f"scale ({r(s.x)}, {r(s.y)}, {r(s.z)})")
    b = bounds(obj) if F & {"size", "ground"} else None
    if b and "size" in F:
        parts.append(f"size {r(b[1][0] - b[0][0])}x{r(b[1][1] - b[0][1])}x{r(b[1][2] - b[0][2])}")
    if b and "ground" in F:
        if abs(b[0][2]) < 0.005:
            parts.append("on ground")
        elif b[0][2] < -0.005:
            parts.append(f"below ground by {r(-b[0][2])}")
        else:
            parts.append(f"floating {r(b[0][2])}")
    if "parent" in F and obj.parent:
        parts.append(f"parent {obj.parent.name}")
    if "details" in F:
        if obj.type == "MESH":
            parts.append(f"{len(obj.data.polygons)} faces")
        elif obj.type == "ARMATURE":
            parts.append(f"{len(obj.data.bones)} bones")
        elif obj.type == "LIGHT":
            parts.append(f"{obj.data.type.lower()} {r(obj.data.energy)}W")
    if "materials" in F:
        mats = [s.material.name for s in getattr(obj, "material_slots", []) if s.material]
        if mats:
            parts.append("mat " + ", ".join(mats[:3]) + ("..." if len(mats) > 3 else ""))
    if "modifiers" in F and obj.modifiers:
        parts.append("mods " + ", ".join(m.type.lower() for m in obj.modifiers))
    if "animation" in F and obj.animation_data and obj.animation_data.action:
        parts.append(f"anim {obj.animation_data.action.name}")
    if "hidden" in F and (obj.hide_get() or obj.hide_render):
        parts.append("hidden")
    if "children" in F and obj.children:
        parts.append(f"{len(obj.children)} children")
    if "topology" in F and obj.type == "MESH":
        parts.append(topology(obj))
    if "weights" in F and obj.type == "MESH":
        w = weights(obj)
        if w:
            parts.append(w)
    return " | ".join(parts)

def topology(obj):
    import bmesh
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        sides = [len(f.verts) for f in bm.faces]
        non_manifold = sum(1 for e in bm.edges if not e.is_manifold and not e.is_boundary)
        boundary = sum(1 for e in bm.edges if e.is_boundary)
        loose = sum(1 for v in bm.verts if not v.link_edges)
        poles = sum(1 for v in bm.verts if len(v.link_edges) not in (0, 2, 4) and not v.is_boundary)
    finally:
        bm.free()
    return (f"{sides.count(4)} quads, {sides.count(3)} tris, {sum(1 for n in sides if n > 4)} ngons; "
            f"{non_manifold} non-manifold, {boundary} boundary edges, {loose} loose verts, {poles} poles")

def weights(obj):
    arm = next((m.object for m in obj.modifiers if m.type == "ARMATURE" and m.object), None)
    if arm is None:
        return None
    deform = {b.name for b in arm.data.bones if b.use_deform}
    groups = {g.index for g in obj.vertex_groups if g.name in deform}
    unweighted = sum(1 for v in obj.data.vertices if not any(g.group in groups and g.weight > 0 for g in v.groups))
    missing = sorted(deform - {g.name for g in obj.vertex_groups})
    return (f"{unweighted} of {len(obj.data.vertices)} verts unweighted by {arm.name}"
            + (f"; deform bones with no group: {', '.join(missing[:10])}" + ("..." if len(missing) > 10 else "") if missing else ""))

lines = []
total = 0
if root_name:
    root = bpy.data.objects.get(root_name)
    if root is None:
        return {"error": f"No object named {root_name!r}"}
    def walk(obj, depth):
        nonlocal total
        total += 1
        if len(lines) < limit:
            lines.append(line(obj, depth))
        for child in obj.children:
            walk(child, depth + 1)
    walk(root, 0)
else:
    if query:
        objs = [o for o in scene.objects if query in o.name.lower()]
    else:
        objs = [o for o in scene.objects if o.parent is None]
    total = len(objs)
    lines = [line(o) for o in objs[:limit]]

counts = {}
for o in scene.objects:
    counts[o.type.lower()] = counts.get(o.type.lower(), 0) + 1

active = bpy.context.view_layer.objects.active
selected = [o.name for o in bpy.context.selected_objects]
header = {
    "scene": scene.name,
    "object_counts": counts,
    "selected": selected[:5],
    "selected_count": len(selected),
    "active": active.name if active else None,
    "mode": bpy.context.mode,
}
if "settings" in F:
    world = scene.world
    hdri = None
    if world and world.use_nodes:
        for n in world.node_tree.nodes:
            if n.type == "TEX_ENVIRONMENT" and n.image:
                hdri = n.image.name
    header["settings"] = {
        "file": bpy.data.filepath or "(unsaved)",
        "engine": scene.render.engine,
        "frames": [scene.frame_start, scene.frame_end, scene.frame_current],
        "fps": scene.render.fps,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "camera": scene.camera.name if scene.camera else None,
        "world_hdri": hdri,
        "unit_scale": scene.unit_settings.scale_length,
    }
return {"header": header, "lines": lines, "total": total, "shown": len(lines)}
'''


# Renders the 3D viewport offscreen with chosen camera matrices and shading,
# tiles the results into one PNG, and restores every setting it touched. The
# offscreen path matches the addon's own screenshot, which works while the
# Blender window is in the background.
LOOK = r'''
import bpy, math
import gpu
import numpy as np
from mathutils import Vector, Matrix

scene = bpy.context.scene
depsgraph = bpy.context.evaluated_depsgraph_get()
mode = ARGS["mode"]
max_size = int(ARGS.get("max_size") or 768)

if mode == "image":
    # An image in the file ("Render Result", a texture) or on disk. Float images and
    # render results go through save_render so the scene's view transform applies,
    # as it would to the final render; byte images are shown as they are.
    import os
    ref = ARGS.get("image") or ""
    src = bpy.data.images.get(ref)
    temp = []  # images this look created, removed at the end
    if src is None:
        path = bpy.path.abspath(ref)
        if not os.path.isfile(path):
            return {"error": f"No image named {ref!r} in the file and no file at {path!r}"}
        try:
            src = bpy.data.images.load(path, check_existing=False)
        except Exception as e:
            return {"error": f"Couldn't load {path!r} as an image: {e}"}
        temp.append(src)
    out = ARGS["filepath"]
    settings = scene.render.image_settings
    saved_settings = [(a, getattr(settings, a)) for a in ("file_format", "color_mode", "color_depth")]
    try:
        if src.type == "RENDER_RESULT" or src.is_float:
            settings.file_format = "PNG"
            settings.color_mode = "RGBA"
            settings.color_depth = "8"
            try:
                src.save_render(out, scene=scene)
            except Exception as e:
                return {"error": f"Couldn't read {src.name!r}" + (" (nothing rendered yet?)" if src.type == "RENDER_RESULT" else "") + f": {e}"}
            img = bpy.data.images.load(out, check_existing=False)
            temp.append(img)
        elif src in temp:
            img = src
        else:
            img = src.copy()  # never resize the user's own image
            temp.append(img)
        w0, h0 = img.size
        if not w0 or not h0:
            return {"error": f"{src.name!r} has no pixels"}
        s = min(1.0, max_size / max(w0, h0))
        if s < 1.0:
            img.scale(max(1, int(w0 * s)), max(1, int(h0 * s)))
        img.filepath_raw = out
        img.file_format = "PNG"
        img.save()
        w, h = img.size
    finally:
        for attr, value in saved_settings:
            try:
                setattr(settings, attr, value)
            except Exception:
                pass
        for im in temp:
            try:
                bpy.data.images.remove(im)
            except Exception:
                pass
    return {"mode": "image", "image": ref, "original_size": [w0, h0], "width": w, "height": h}

area = space = region = None
for a in bpy.context.screen.areas:
    if a.type == "VIEW_3D":
        area, space = a, a.spaces.active
        region = next((rg for rg in a.regions if rg.type == "WINDOW"), None)
        break
if region is None:
    return {"error": "No 3D viewport is open in Blender"}

names = ARGS.get("target") or []
if names:
    missing = [n for n in names if n not in bpy.data.objects]
    if missing:
        return {"error": "No object named " + ", ".join(repr(m) for m in missing)}
    targets = []
    def add(o):
        if o not in targets:
            targets.append(o)
            for c in o.children:
                add(c)
    for n in names:
        add(bpy.data.objects[n])
else:
    targets = [o for o in scene.objects if o.visible_get() and o.type not in {"CAMERA", "LIGHT"}]

def world_points(objs):
    pts = []
    for o in objs:
        if o.type in {"EMPTY"} and not o.children:
            pts.append(o.matrix_world.translation.copy())
            continue
        try:
            ev = o.evaluated_get(depsgraph)
            pts.extend(ev.matrix_world @ Vector(c) for c in ev.bound_box)
        except Exception:
            pts.append(o.matrix_world.translation.copy())
    return pts

pts = world_points(targets) or [Vector((0, 0, 0))]
lo = Vector([min(p[i] for p in pts) for i in range(3)])
hi = Vector([max(p[i] for p in pts) for i in range(3)])
center = (lo + hi) / 2
radius = max((hi - lo).length / 2, 0.05)

FOV = math.radians(35)

def perspective(aspect, near, far):
    f = 1 / math.tan(FOV / 2)
    return Matrix((
        (f / aspect, 0, 0, 0),
        (0, f, 0, 0),
        (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
        (0, 0, -1, 0),
    ))

def orbit(direction, aspect):
    direction = Vector(direction).normalized()
    fit = FOV / 2 if aspect >= 1 else math.atan(math.tan(FOV / 2) * aspect)
    dist = float(ARGS.get("distance") or 0) or radius / math.sin(fit) * 1.1
    eye = center + direction * dist
    # The camera looks down its local -Z with local Y up; to_track_quat keeps
    # that Y as close to world Z as the direction allows, so the horizon stays level.
    rot = (center - eye).to_track_quat("-Z", "Y").to_matrix().to_4x4()
    view = (Matrix.Translation(eye) @ rot).inverted()
    return view, perspective(aspect, max(dist - radius * 3, dist * 0.01), dist + radius * 3)

ANGLES = {
    "front": (0, -1, 0), "back": (0, 1, 0), "right": (1, 0, 0), "left": (-1, 0, 0),
    # Top is tilted a hair toward -Y so "up" in the image is +Y, as in Blender's top view.
    "top": (0, -0.001, 1), "three_quarter": (1, -1, 0.7),
}

def direction_of(v):
    """A named angle, or any [x, y, z] direction from the target towards the eye."""
    if isinstance(v, str):
        return ANGLES.get(v)
    if isinstance(v, (list, tuple)) and len(v) == 3 and any(v):
        return tuple(float(c) for c in v)
    return None

def label(v):
    return v if isinstance(v, str) else "(" + ", ".join(f"{float(c):g}" for c in v) + ")"

def current_view():
    r3d = space.region_3d
    return r3d.view_matrix.copy(), r3d.window_matrix.copy()

def camera_view(w, h):
    cam = scene.camera
    if cam is None:
        return None
    win = cam.calc_matrix_camera(depsgraph, x=w, y=h,
                                 scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y)
    return cam.matrix_world.inverted(), win

def draw(view, win, w, h):
    off = gpu.types.GPUOffScreen(w, h)
    try:
        off.draw_view3d(scene, bpy.context.view_layer, space, region, view, win,
                        do_color_management=True)
        buf = off.texture_color.read()
    finally:
        off.free()
    buf.dimensions = w * h * 4
    return np.asarray(buf, dtype=np.float32).reshape(h, w, 4) / 255.0

def sheet(tiles, cols):
    h, w = tiles[0].shape[:2]
    rows = math.ceil(len(tiles) / cols)
    gap = 4
    out = np.ones((rows * h + (rows - 1) * gap, cols * w + (cols - 1) * gap, 4), dtype=np.float32)
    out[..., :3] = 0.08
    # Blender images are stored bottom row first, so the first tile goes top-left.
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        y = (rows - 1 - r) * (h + gap)
        x = c * (w + gap)
        out[y:y + h, x:x + w] = t
    return out

# Settings each mode changes, restored afterwards.
shading, overlay = space.shading, space.overlay
saved = []
def set_attr(obj, attr, value):
    if not hasattr(obj, attr):
        return
    saved.append((obj, attr, getattr(obj, attr)))
    try:
        setattr(obj, attr, value)
    except Exception:
        saved.pop()

requested = ARGS.get("shading")
shading_types = {"solid": "SOLID", "material": "MATERIAL", "rendered": "RENDERED", "wireframe": "SOLID", "xray": "SOLID"}
if requested:
    set_attr(shading, "type", shading_types[requested])

info = {"mode": mode, "targets": len(targets), "center": [round(v, 2) for v in center],
        "size": [round(v, 2) for v in (hi - lo)]}

if mode != "viewport":
    # The 3D cursor, light and camera gizmos and parent lines sit across generated views
    # and read as part of the scene.
    set_attr(overlay, "show_cursor", False)
    set_attr(overlay, "show_extras", False)
    set_attr(overlay, "show_relationship_lines", False)
    if mode == "camera" or ARGS.get("view") == "camera" or shading.type in {"RENDERED", "MATERIAL"}:
        # Grid, light and camera gizmos aren't part of what's being judged.
        set_attr(overlay, "show_overlays", False)
    engine = scene.render.engine
    if shading.type == "RENDERED" and "EEVEE" not in engine and engine != "BLENDER_WORKBENCH":
        # Progressive engines like Cycles draw nothing into an offscreen view.
        return {"error": f"Rendered shading can't be captured with {engine}. Render and look at "
                         "image=\"Render Result\", use material shading, or switch the engine."}
frame_before = scene.frame_current

try:
    if requested == "wireframe":
        # Edges over a plain matcap surface: topology and form in one picture.
        set_attr(shading, "light", "MATCAP")
        set_attr(shading, "color_type", "SINGLE")
        set_attr(overlay, "show_overlays", True)
        set_attr(overlay, "show_wireframes", True)
        set_attr(overlay, "wireframe_threshold", 1.0)
    elif requested == "xray":
        set_attr(shading, "show_xray", True)
        set_attr(shading, "xray_alpha", 0.35)
        set_attr(overlay, "show_overlays", True)
        set_attr(overlay, "show_bones", True)
        rigs = {o for o in targets if o.type == "ARMATURE"}
        rigs |= {m.object for o in targets for m in getattr(o, "modifiers", [])
                 if m.type == "ARMATURE" and m.object}
        for o in rigs:
            set_attr(o, "show_in_front", True)

    if mode == "viewport":
        w, h = region.width, region.height
        s = min(1.0, max_size / max(w, h))
        w, h = max(1, int(w * s)), max(1, int(h * s))
        view, win = current_view()
        image = draw(view, win, w, h)
    elif mode == "camera":
        rx, ry = scene.render.resolution_x, scene.render.resolution_y
        s = max_size / max(rx, ry)
        w, h = max(1, int(rx * s)), max(1, int(ry * s))
        cv = camera_view(w, h)
        if cv is None:
            return {"error": "The scene has no camera. Add one or use mode='angles'."}
        info["camera"] = scene.camera.name
        image = draw(cv[0], cv[1], w, h)
    elif mode == "frames":
        start, end = scene.frame_start, scene.frame_end
        frames = ARGS.get("frames") or []
        if not frames:
            n = max(2, min(int(ARGS.get("frame_count") or 6), 12))
            frames = sorted({round(start + (end - start) * i / (n - 1)) for i in range(n)})
        frames = frames[:12]
        cols = 3 if len(frames) > 4 else 2
        w = max(64, max_size // cols)
        h = int(w * 0.75)
        use_camera = scene.camera is not None and ARGS.get("view") == "camera"
        tiles = []
        for f in frames:
            scene.frame_set(int(f))
            if use_camera:
                view, win = camera_view(w, h)
            elif direction_of(ARGS.get("view")):
                view, win = orbit(direction_of(ARGS["view"]), w / h)
            else:
                view, win = current_view()
            tiles.append(draw(view, win, w, h))
        info["frames"] = [int(f) for f in frames]
        image = sheet(tiles, cols)
    else:
        views = ARGS.get("views") or ["front", "right", "top", "three_quarter"]
        views = [v for v in views if direction_of(v)][:6] or ["three_quarter"]
        cols = 1 if len(views) == 1 else (3 if len(views) > 4 else 2)
        w = max(64, max_size // cols)
        h = w
        tiles = [draw(*orbit(direction_of(v), 1.0), w, h) for v in views]
        info["views"] = [label(v) for v in views]
        image = sheet(tiles, cols)
finally:
    if scene.frame_current != frame_before:
        scene.frame_set(frame_before)
    for obj, attr, value in reversed(saved):
        try:
            setattr(obj, attr, value)
        except Exception:
            pass

h, w = image.shape[:2]
img = bpy.data.images.new("mcp_look", w, h, alpha=True)
try:
    img.pixels.foreach_set(image.ravel())
    img.filepath_raw = ARGS["filepath"]
    img.file_format = "PNG"
    img.save()
finally:
    bpy.data.images.remove(img)
info["width"], info["height"] = w, h
return info
'''


# Bounding box of freshly imported objects, so a generation or import result
# says how big the thing is and whether it sits on the ground.
BOUNDS = r'''
import bpy
from mathutils import Vector
dg = bpy.context.evaluated_depsgraph_get()
out = []
for name in ARGS["names"]:
    o = bpy.data.objects.get(name)
    if o is None:
        continue
    objs = [o] + list(o.children_recursive)
    pts = []
    for x in objs:
        if x.type in {"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD", "VOLUME"}:
            ev = x.evaluated_get(dg)
            pts.extend(ev.matrix_world @ Vector(c) for c in ev.bound_box)
    if not pts:
        continue
    lo = [round(min(p[i] for p in pts), 3) for i in range(3)]
    hi = [round(max(p[i] for p in pts), 3) for i in range(3)]
    out.append({"name": o.name, "world_bounding_box": [lo, hi],
                "size": [round(hi[i] - lo[i], 3) for i in range(3)]})
return out
'''

# Shared by the Geometry Nodes scripts below: turning bpy values into JSON and
# back, and finding the tree, nodes and sockets a call names. Request mistakes
# raise GNError, which each script turns into {"error": ...}.
_GN_COMMON = r'''
import bpy
import mathutils

if bpy.app.version < (4, 0, 0):
    return {"error": f"Blender {bpy.app.version_string} is not supported: needs 4.0 or newer"}

class GNError(Exception):
    pass

# Base node properties, plus which list item is selected in the sidebar: UI state, not part of the tree.
BASE_NODE_PROPS = set(bpy.types.Node.bl_rna.properties.keys()) | {"active_index", "active_item", "inspection_index"}
# How a property the tree dump can't express is shown, e.g. "(TexMapping, not shown)".
NOT_SHOWN = ", not shown)"
FLT_MAX = 3.4028234663852886e+38
ID_SOCKETS = {"OBJECT": "Object", "COLLECTION": "Collection", "MATERIAL": "Material", "IMAGE": "Image",
              "TEXTURE": "Texture"}

def value(v):
    if isinstance(v, bpy.types.ID):
        return v.name
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, mathutils.Matrix):
        return [[round(x, 4) for x in row] for row in v]
    return [value(x) for x in v]

def id_by_name(struct_id, name):
    """Object "Cube" -> bpy.data.objects["Cube"]; finds the bpy.data collection from its RNA type."""
    if name is None:
        return None
    for prop in bpy.data.bl_rna.properties:
        if prop.type == "COLLECTION" and prop.fixed_type.identifier == struct_id:
            found = getattr(bpy.data, prop.identifier).get(name)
            if found is None:
                raise GNError(f"no {struct_id} named {name!r}")
            return found
    raise GNError(f"no bpy.data collection holds {struct_id}")

def to_socket_value(socket_type, v):
    """A JSON value for a socket of this type ("OBJECT" or "NodeSocketObject"): names become IDs."""
    id_type = ID_SOCKETS.get(socket_type.upper().replace("NODESOCKET", ""))
    return id_by_name(id_type, v) if id_type else v

def find_object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise GNError(f"no object named {name!r}")
    return obj

def gn_modifiers(obj):
    return [m for m in obj.modifiers if m.type == "NODES"]

def find_modifier(obj, mod_name):
    mods = gn_modifiers(obj)
    if mod_name:
        for m in mods:
            if m.name == mod_name:
                return m
        raise GNError(f"{obj.name!r} has no Geometry Nodes modifier {mod_name!r}; it has: {[m.name for m in mods]}")
    if len(mods) == 1:
        return mods[0]
    raise GNError(f"{obj.name!r} has {len(mods)} Geometry Nodes modifiers, pass modifier=: {[m.name for m in mods]}")

def check_gn_tree(tree):
    if tree.bl_idname != "GeometryNodeTree":
        raise GNError(f"{tree.name!r} is a {tree.bl_idname}, not a Geometry Nodes tree")
    return tree

def find_tree(args):
    """(tree, object, modifier) for a call naming either an object or a node group."""
    obj_name, tree_name = args.get("object"), args.get("tree")
    if bool(obj_name) == bool(tree_name):
        raise GNError("give exactly one of object or tree")
    if tree_name:
        tree = bpy.data.node_groups.get(tree_name)
        if tree is None:
            raise GNError(f"no node group named {tree_name!r}")
        return check_gn_tree(tree), None, None
    obj = find_object(obj_name)
    mod = find_modifier(obj, args.get("modifier"))
    if mod.node_group is None:
        raise GNError(f"modifier {mod.name!r} on {obj.name!r} has no node group")
    return check_gn_tree(mod.node_group), obj, mod

def find_node(tree, name):
    node = tree.nodes.get(name)
    if node is None:
        raise GNError(f"{tree.name!r} has no node named {name!r}")
    return node

def find_socket(node, key, output=False):
    """A socket by identifier, or by name when that name is unique among the enabled sockets."""
    side = "output" if output else "input"
    # NodeSocketVirtual is the empty "add a socket" slot on group input/output nodes.
    sockets = [s for s in (node.outputs if output else node.inputs) if s.bl_idname != "NodeSocketVirtual"]
    for s in sockets:
        if s.identifier == key:
            if not s.enabled:
                raise GNError(f"{side} {key!r} of {node.name!r} is disabled in the node's current mode")
            return s
    named = [s for s in sockets if s.enabled and s.name == key]
    if len(named) == 1:
        return named[0]
    available = [s.identifier if s.name == s.identifier else f"{s.identifier} ({s.name})"
                 for s in sockets if s.enabled]
    if named:
        raise GNError(f"{side} name {key!r} is ambiguous on {node.name!r}, use an id: {available}")
    raise GNError(f"{node.name!r} has no {side} {key!r}; it has: {available}")

def set_socket_value(socket, v):
    if not hasattr(socket, "default_value"):
        raise GNError(f"input {socket.identifier!r} of {socket.node.name!r} has no value to set")
    socket.default_value = to_socket_value(socket.type, v)

def set_color_ramp(ramp, v):
    want = v["elements"]
    if not want:
        raise GNError("a color ramp needs at least one element")
    ramp.color_mode = v["color_mode"]
    ramp.interpolation = v["interpolation"]
    elements = ramp.elements
    while len(elements) > 1:
        elements.remove(elements[-1])
    elements[0].position, elements[0].color = want[0]
    for position, color in want[1:]:
        # new() inserts sorted by position; set the color before the next insert moves things.
        elements.new(position).color = color

def set_curve_mapping(mapping, v):
    curves = v["curves"]
    if len(curves) != len(mapping.curves):
        raise GNError(f"this mapping has {len(mapping.curves)} curves, got {len(curves)}")
    for curve, points in zip(mapping.curves, curves):
        if len(points) < 2:
            raise GNError("a curve needs at least two points")
        while len(curve.points) > len(points):
            curve.points.remove(curve.points[-1])
        while len(curve.points) < len(points):
            curve.points.new(0.5, 0.5)
        for point, location in zip(curve.points, points):
            point.location = location
    mapping.update()

def set_items(node, key, items):
    """Recreate a node's item list (repeat_items, capture_items, enum_items ...) from dicts."""
    coll = getattr(node, key)
    functions = node.bl_rna.properties[key].srna.functions
    if "new" not in functions:
        raise GNError(f"{key} of {node.name!r} can't be changed")
    params = [p.identifier for p in functions["new"].parameters if not p.is_output]
    coll.clear()
    for item in items:
        args = {p: item[p] for p in params if p in item}
        if "socket_type" in params and "socket_type" not in args and "data_type" in item:
            # Capture and grid items are typed by data_type, which is set below, but new() still wants a socket_type.
            args["socket_type"] = "FLOAT"
        missing = [p for p in params if p not in args]
        if missing:
            raise GNError(f"{key} items of {node.name!r} need {missing}")
        new = coll.new(**args)
        for k, v in item.items():
            if k not in args:
                setattr(new, k, v)

def set_node_prop(node, key, v):
    prop = node.bl_rna.properties.get(key)
    if prop is None or key in BASE_NODE_PROPS:
        raise GNError(f"{node.bl_idname} has no property {key!r}")
    if isinstance(v, str) and v.endswith(NOT_SHOWN):
        return
    try:
        if prop.type == "COLLECTION":
            set_items(node, key, v)
        elif prop.type == "POINTER":
            target = getattr(node, key)
            if isinstance(target, bpy.types.ColorRamp):
                set_color_ramp(target, v)
            elif isinstance(target, bpy.types.CurveMapping):
                set_curve_mapping(target, v)
            elif prop.is_readonly:
                raise GNError("it can't be set")
            else:
                setattr(node, key, id_by_name(prop.fixed_type.identifier, v))
        elif prop.type == "ENUM" and prop.is_enum_flag:
            setattr(node, key, set(v))
        else:
            setattr(node, key, v)
    except (GNError, TypeError, ValueError, AttributeError, RuntimeError, KeyError) as e:
        raise GNError(f"node {node.name!r}, property {key!r}: {e}")

def simple_props(struct, skip=()):
    out = {}
    for prop in struct.bl_rna.properties:
        key = prop.identifier
        if key in skip or key == "rna_type" or prop.type in {"POINTER", "COLLECTION"} or prop.is_readonly:
            continue
        v = getattr(struct, key)
        out[key] = sorted(v) if isinstance(v, set) else value(v)
    return out

def node_props(node):
    out = simple_props(node, BASE_NODE_PROPS)
    for prop in node.bl_rna.properties:
        key = prop.identifier
        if key in BASE_NODE_PROPS:
            continue
        if prop.type == "POINTER":
            v = getattr(node, key)
            if isinstance(v, bpy.types.Node):
                # Zone inputs point at their output; other node pointers (enum_definition) point back at the node.
                if key == "paired_output":
                    out[key] = v.name
            elif v is None or isinstance(v, bpy.types.ID):
                out[key] = None if v is None else v.name
            elif isinstance(v, bpy.types.ColorRamp):
                out[key] = {"interpolation": v.interpolation, "color_mode": v.color_mode,
                            "elements": [[round(e.position, 4), value(e.color)] for e in v.elements]}
            elif isinstance(v, bpy.types.CurveMapping):
                out[key] = {"curves": [[[round(p.location.x, 4), round(p.location.y, 4)] for p in c.points]
                                       for c in v.curves]}
            else:
                out[key] = f"({type(v).__name__}{NOT_SHOWN}"
        elif prop.type == "COLLECTION":
            out[key] = [simple_props(item) for item in getattr(node, key)]
    return out

def socket_entry(socket, with_value):
    entry = {"id": socket.identifier}
    if socket.name != socket.identifier:
        entry["name"] = socket.name
    if with_value and not socket.is_linked and not socket.hide_value and hasattr(socket, "default_value"):
        entry["value"] = value(socket.default_value)
    return entry

def shown_sockets(sockets):
    return [s for s in sockets if s.enabled and s.bl_idname != "NodeSocketVirtual"]

def dump_interface(tree):
    items = []
    for item in tree.interface.items_tree:
        parent = item.parent.name if item.parent and item.parent.parent else None
        if item.item_type == "PANEL":
            entry = {"panel": item.name}
        else:
            entry = {"in_out": item.in_out, "name": item.name, "id": item.identifier,
                     "type": item.socket_type}
            for key in ("default_value", "min_value", "max_value"):
                # min/max at the float limits just mean "no limit".
                if hasattr(item, key) and not (key != "default_value" and abs(getattr(item, key)) > 1e30):
                    entry[key.replace("_value", "")] = value(getattr(item, key))
            if getattr(item, "default_input", "VALUE") != "VALUE":
                entry["default_input"] = item.default_input
        if parent:
            entry["in_panel"] = parent
        items.append(entry)
    return items

def dump_tree(tree):
    nodes = []
    for node in tree.nodes:
        entry = {"name": node.name, "type": node.bl_idname,
                 "location": [round(node.location.x), round(node.location.y)]}
        if node.label:
            entry["label"] = node.label
        if node.parent:
            entry["parent"] = node.parent.name
        if node.mute:
            entry["mute"] = True
        props = node_props(node)
        if props:
            entry["props"] = props
        inputs = [s for s in (socket_entry(s, True) for s in shown_sockets(node.inputs)) if len(s) > 1]
        if inputs:
            entry["inputs"] = inputs
        outputs = [s for s in (socket_entry(s, False) for s in shown_sockets(node.outputs)) if len(s) > 1]
        if outputs:
            entry["outputs"] = outputs
        nodes.append(entry)
    links = []
    for link in tree.links:
        entry = {"from": [link.from_node.name, link.from_socket.identifier],
                 "to": [link.to_node.name, link.to_socket.identifier]}
        if link.to_socket.is_multi_input:
            entry["multi_input_sort_id"] = link.multi_input_sort_id
        if not link.is_valid:
            entry["invalid"] = True
        if link.is_muted:
            entry["muted"] = True
        links.append(entry)
    groups = sorted({n.node_tree.name for n in tree.nodes
                     if n.bl_idname == "GeometryNodeGroup" and n.node_tree})
    out = {"name": tree.name, "is_modifier": tree.is_modifier, "interface": dump_interface(tree),
           "nodes": nodes, "links": links}
    if tree.description:
        out["description"] = tree.description
    if groups:
        out["uses_groups"] = groups
    return out

def node_classes():
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name)
        if isinstance(cls, type) and issubclass(cls, bpy.types.Node) and cls is not bpy.types.Node:
            yield name
'''


# A Geometry Nodes tree as JSON: interface, nodes with their settings and the
# values of unlinked inputs, and links by socket identifier. Given an object it
# also reports the values set on the modifier. Hidden (disabled) sockets are left
# out, since the node's current mode doesn't use them.
GN_TREE = _GN_COMMON + r'''
try:
    tree, obj, mod = find_tree(ARGS)
except GNError as e:
    return {"error": str(e)}

result = {}
if mod is not None:
    inputs = {}
    for item in tree.interface.items_tree:
        if item.item_type != "SOCKET" or item.in_out != "INPUT" or item.identifier not in mod.keys():
            continue
        entry = {"name": item.name, "value": value(mod[item.identifier])}
        if mod.get(item.identifier + "_use_attribute"):
            entry["attribute"] = mod[item.identifier + "_attribute_name"]
        inputs[item.identifier] = entry
    result["modifier"] = {"object": obj.name, "name": mod.name, "inputs": inputs}

result["tree"] = dump_tree(tree)
if ARGS.get("include_nested"):
    nested, queue = {}, list(result["tree"].get("uses_groups", []))
    while queue:
        name = queue.pop(0)
        if name in nested or name == tree.name:
            continue
        nested[name] = dump_tree(bpy.data.node_groups[name])
        queue.extend(nested[name].get("uses_groups", []))
    result["nested"] = list(nested.values())
return result
'''


# Builds a whole tree from a spec in gn_get_tree's format. The build runs on a
# copy, which replaces the original only once everything succeeded, so a bad
# spec leaves the user's tree untouched. Interface sockets whose id matches an
# existing one are kept (so values set on modifiers survive); links to group
# input/output sockets are translated to the ids the interface ends up with.
GN_BUILD = _GN_COMMON + r'''
import re

spec = ARGS["spec"]

def sync_interface(tree, items):
    iface = tree.interface
    sockets = {i.identifier: i for i in iface.items_tree if i.item_type == "SOCKET"}
    keep = {e["id"] for e in items if "panel" not in e and e.get("id") in sockets
            and sockets[e["id"]].in_out == e["in_out"]}
    panel_names = {e["panel"] for e in items if "panel" in e}
    for item in [i for i in iface.items_tree if i.item_type == "SOCKET" and i.identifier not in keep]:
        iface.remove(item)
    for item in [i for i in iface.items_tree if i.item_type == "PANEL" and i.name not in panel_names]:
        iface.remove(item)
    sockets = {i.identifier: i for i in iface.items_tree if i.item_type == "SOCKET"}
    panels = {i.name: i for i in iface.items_tree if i.item_type == "PANEL"}
    id_map, placed = {}, []
    for e in items:
        if "panel" in e:
            item = panels.get(e["panel"]) or iface.new_panel(e["panel"])
            panels[e["panel"]] = item
        else:
            item = sockets.get(e.get("id"))
            if item is None:
                item = iface.new_socket(e["name"], in_out=e["in_out"], socket_type=e["type"])
            else:
                item.name = e["name"]
                if item.socket_type != e["type"]:
                    item.socket_type = e["type"]
            if "default" in e:
                item.default_value = to_socket_value(e["type"], e["default"])
            # A kept socket must not keep old settings the spec leaves out (it omits "no limit" and VALUE).
            if hasattr(item, "min_value"):
                limit = 2**31 - 1 if isinstance(item.min_value, int) else FLT_MAX
                item.min_value = e.get("min", -limit - isinstance(item.min_value, int))
                item.max_value = e.get("max", limit)
            if hasattr(item, "default_input"):
                item.default_input = e.get("default_input", "VALUE")
            if e.get("id"):
                id_map[e["id"]] = item.identifier
        placed.append((e, item))
    if placed:
        root = placed[0][1]
        while root.parent is not None:
            root = root.parent
        positions = {}
        for e, item in placed:
            if e.get("in_panel") and e["in_panel"] not in panels:
                raise GNError(f"interface item {e.get('name', e.get('panel'))!r} is in unknown panel {e['in_panel']!r}")
            parent = panels[e["in_panel"]] if e.get("in_panel") else root
            position = positions.get(parent.as_pointer(), 0)
            iface.move_to_parent(item, parent, position)
            positions[parent.as_pointer()] = position + 1
    return id_map

def build(tree):
    id_map = sync_interface(tree, spec.get("interface", []))
    specs = spec.get("nodes", [])
    names = [e["name"] for e in specs]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise GNError(f"node names must be unique: {duplicates}")
    tree.nodes.clear()
    nodes = {}
    for e in specs:
        try:
            node = tree.nodes.new(e["type"])
        except RuntimeError as ex:
            raise GNError(f"node {e['name']!r}: can't add a {e['type']!r} to a Geometry Nodes tree ({ex})")
        node.name = e["name"]
        node.label = e.get("label", "")
        node.mute = e.get("mute", False)
        nodes[e["name"]] = node

    def node_named(name, used_by):
        if name not in nodes:
            raise GNError(f"{used_by} names unknown node {name!r}")
        return nodes[name]

    def props_of(e):
        return (e.get("props") or {}).items()

    # Socket ids can come out different from the spec's: recreated item lists
    # (zones, capture, menu switch) number their sockets on. The spec names
    # every socket whose name differs from its id, so a socket whose id now
    # belongs to a differently named socket is found by its name instead.
    spec_names = {(e["name"], side): {s["id"]: s["name"] for s in e.get(side, []) if "name" in s}
                  for e in specs for side in ("inputs", "outputs")}

    def socket(node, key, output=False):
        if node.bl_idname in {"NodeGroupInput", "NodeGroupOutput"} and key in id_map:
            return find_socket(node, id_map[key], output)
        name = spec_names[(node.name, "outputs" if output else "inputs")].get(key)
        sockets = node.outputs if output else node.inputs
        by_id = next((s for s in sockets if s.identifier == key), None)
        if name is None or (by_id is not None and by_id.name == name):
            return find_socket(node, key, output)
        # Item lists of one node can share names (a For Each zone's Main and
        # Generation items), so prefer the socket from the same list: same id prefix.
        named = [s for s in shown_sockets(sockets) if s.name == name]
        same_list = [s for s in named if re.sub(r"_\d+$", "", s.identifier) == re.sub(r"_\d+$", "", key)]
        if len(named) > 1 and len(same_list) == 1:
            return same_list[0]
        return find_socket(node, name, output)

    # Settings first (they decide which sockets exist), then zone pairs and
    # item lists, then layout, input values and links.
    for e in specs:
        node = nodes[e["name"]]
        for key, v in props_of(e):
            prop = node.bl_rna.properties.get(key)
            if key != "paired_output" and not (prop and prop.type == "COLLECTION"):
                set_node_prop(node, key, v)
    for e in specs:
        output = (e.get("props") or {}).get("paired_output")
        if output and not nodes[e["name"]].pair_with_output(node_named(output, f"zone {e['name']!r}")):
            raise GNError(f"can't pair zone input {e['name']!r} with {output!r}")
    for e in specs:
        node = nodes[e["name"]]
        for key, v in props_of(e):
            prop = node.bl_rna.properties.get(key)
            if prop and prop.type == "COLLECTION":
                set_node_prop(node, key, v)
    for e in specs:
        node = nodes[e["name"]]
        if e.get("parent"):
            node.parent = node_named(e["parent"], f"node {e['name']!r}")
        if "location" in e:
            node.location = e["location"]
    for e in specs:
        node = nodes[e["name"]]
        for s in e.get("inputs", []):
            if "value" in s:
                target = socket(node, s["id"])
                try:
                    set_socket_value(target, s["value"])
                except (TypeError, ValueError, AttributeError) as ex:
                    raise GNError(f"node {node.name!r}, input {s['id']!r}: {ex}")
    links = spec.get("links", [])
    # Multi-input sockets order their links by when they were made.
    for _, e in sorted(enumerate(links), key=lambda il: (il[1].get("multi_input_sort_id", 0), il[0])):
        (from_name, from_id), (to_name, to_id) = e["from"], e["to"]
        from_node = node_named(from_name, f"link {e['from']} -> {e['to']}")
        to_node = node_named(to_name, f"link {e['from']} -> {e['to']}")
        link = tree.links.new(socket(from_node, from_id, output=True), socket(to_node, to_id))
        if e.get("muted"):
            link.is_muted = True
    return id_map

obj_name, mod_name, tree_name = ARGS.get("object"), ARGS.get("modifier"), ARGS.get("tree")
try:
    if bool(obj_name) == bool(tree_name):
        raise GNError("give exactly one of object or tree")
    obj = mod = None
    if obj_name:
        obj = find_object(obj_name)
        mods = gn_modifiers(obj)
        if mod_name:
            mod = next((m for m in mods if m.name == mod_name), None)
        elif len(mods) == 1:
            mod = mods[0]
        elif mods:
            raise GNError(f"{obj.name!r} has {len(mods)} Geometry Nodes modifiers, pass modifier=: "
                          f"{[m.name for m in mods]}")
        original = mod.node_group if mod else None
    else:
        original = bpy.data.node_groups.get(tree_name)
    if original is not None:
        check_gn_tree(original)
    name = spec.get("name") or (original.name if original else tree_name or "Geometry Nodes")
    work = original.copy() if original else bpy.data.node_groups.new(name, "GeometryNodeTree")
    try:
        id_map = build(work)
    except BaseException:
        bpy.data.node_groups.remove(work)
        raise
except GNError as e:
    return {"error": str(e)}

if original is not None:
    original.user_remap(work)
    bpy.data.node_groups.remove(original)
work.name = name
if "is_modifier" in spec:
    work.is_modifier = spec["is_modifier"]
elif original is None:
    work.is_modifier = obj is not None
if obj is not None:
    if mod is None:
        mod = obj.modifiers.new(mod_name or "GeometryNodes", "NODES")
    if mod.node_group != work:
        mod.node_group = work
# Modifiers using the tree add properties for new interface inputs only when told.
work.interface_update(bpy.context)

result = {"tree": work.name, "nodes": len(work.nodes), "links": len(work.links),
          "interface": [{"in_out": i.in_out, "name": i.name, "id": i.identifier}
                        for i in work.interface.items_tree if i.item_type == "SOCKET"]}
changed_ids = {old: new for old, new in id_map.items() if old != new}
if changed_ids:
    result["interface_id_changes"] = changed_ids
invalid = [[l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier]
           for l in work.links if not l.is_valid]
if invalid:
    result["invalid_links"] = invalid
if obj is not None:
    result["modifier"] = {"object": obj.name, "name": mod.name}
return result
'''


# Changes values in place: node settings, unlinked node inputs, and the input
# values on a Geometry Nodes modifier (a plain value, or {"attribute": name}).
GN_SET_INPUTS = _GN_COMMON + r'''
changed = []
try:
    tree, obj, mod = find_tree(ARGS)
    for name, props in (ARGS.get("node_props") or {}).items():
        node = find_node(tree, name)
        for key, v in props.items():
            set_node_prop(node, key, v)
            changed.append(f"{name}.{key}")
    for name, inputs in (ARGS.get("node_inputs") or {}).items():
        node = find_node(tree, name)
        for key, v in inputs.items():
            socket = find_socket(node, key)
            try:
                set_socket_value(socket, v)
            except (TypeError, ValueError, AttributeError) as e:
                raise GNError(f"node {name!r}, input {key!r}: {e}")
            changed.append(f"{name}.{socket.identifier}")
    modifier_inputs = ARGS.get("modifier_inputs") or {}
    if modifier_inputs and mod is None:
        raise GNError("modifier_inputs need object=, the object whose modifier to change")
    items = [i for i in tree.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"]
    for key, v in modifier_inputs.items():
        matches = [i for i in items if i.identifier == key] or [i for i in items if i.name == key]
        if len(matches) != 1:
            available = [f"{i.identifier} ({i.name})" for i in items]
            raise GNError(f"{'ambiguous' if matches else 'no'} modifier input {key!r}; inputs: {available}")
        item = matches[0]
        ident = item.identifier
        if ident not in mod.keys():
            raise GNError(f"input {item.name!r} ({item.socket_type}) has no value on the modifier")
        if isinstance(v, dict):
            if set(v) != {"attribute"} or ident + "_use_attribute" not in mod.keys():
                raise GNError(f"input {item.name!r}: a dict value must be {{\"attribute\": name}} on a field input")
            mod[ident + "_use_attribute"] = True
            mod[ident + "_attribute_name"] = v["attribute"]
        else:
            if ident + "_use_attribute" in mod.keys():
                mod[ident + "_use_attribute"] = False
            try:
                mod[ident] = to_socket_value(item.socket_type, v)
            except (TypeError, ValueError) as e:
                raise GNError(f"modifier input {item.name!r}: {e}")
        changed.append(f"modifier.{ident}")
    if modifier_inputs:
        # Setting modifier properties from Python doesn't re-evaluate the object by itself.
        obj.update_tag()
except GNError as e:
    return {"error": str(e), "changed": changed}
return {"changed": changed}
'''


# Node types a Geometry Nodes tree can hold in this Blender, or one type's
# settings (with enum choices) and sockets. Sockets depend on mode-like
# settings, so `props` are applied first. Works on a scratch tree it removes.
GN_NODE_TYPES = _GN_COMMON + r'''
probe = bpy.data.node_groups.new("__mcp_gn_probe__", "GeometryNodeTree")
try:
    node_type = ARGS.get("node_type")
    if not node_type:
        words = (ARGS.get("query") or "").lower().split()
        found = []
        for name in node_classes():
            try:
                node = probe.nodes.new(name)
            except RuntimeError:
                continue  # not a node type a Geometry Nodes tree can hold
            label = node.bl_label
            probe.nodes.remove(node)
            if all(w in name.lower() or w in label.lower() for w in words):
                found.append([name, label])
        return {"blender": bpy.app.version_string, "count": len(found), "types": found}
    try:
        node = probe.nodes.new(node_type)
    except RuntimeError:
        guesses = [n for n in node_classes() if node_type.lower() in n.lower()]
        return {"error": f"{node_type!r} is not a node type a Geometry Nodes tree can hold",
                "did_you_mean": sorted(guesses)[:15]}
    for key, v in (ARGS.get("props") or {}).items():
        set_node_prop(node, key, v)
    current = node_props(node)
    settings = []
    for prop in node.bl_rna.properties:
        if prop.identifier not in current:
            continue
        entry = {"name": prop.identifier, "type": prop.type, "value": current[prop.identifier]}
        if prop.type == "ENUM":
            entry["items"] = [i.identifier for i in prop.enum_items]
        settings.append(entry)

    def sockets(socks):
        out = []
        for s in shown_sockets(socks):
            entry = {"id": s.identifier, "name": s.name, "type": s.type}
            if hasattr(s, "default_value") and not s.hide_value:
                entry["default"] = value(s.default_value)
            if s.is_multi_input:
                entry["multi_input"] = True
            out.append(entry)
        return out

    return {"type": node_type, "label": node.bl_label, "blender": bpy.app.version_string,
            "settings": settings, "inputs": sockets(node.inputs), "outputs": sockets(node.outputs)}
except GNError as e:
    return {"error": str(e)}
finally:
    bpy.data.node_groups.remove(probe)
'''


# What an object's modifier stack produced: counts and attributes per geometry
# component, instances by what they instance, and the world bounding box.
GN_EVALUATED = _GN_COMMON + r'''
if bpy.app.version < (4, 3, 0):
    return {"error": f"needs Blender 4.3 or newer (Object.evaluated_geometry), this is {bpy.app.version_string}"}
try:
    obj = find_object(ARGS["object"])
except GNError as e:
    return {"error": str(e)}
from mathutils import Vector

def attributes(data):
    # Dot-prefixed attributes are Blender's internal ones.
    return [[a.name, a.domain, a.data_type] for a in data.attributes if not a.name.startswith(".")]

def components(geo):
    out = {}
    if geo.mesh:
        m = geo.mesh
        out["mesh"] = {"vertices": len(m.vertices), "edges": len(m.edges), "faces": len(m.polygons),
                       "corners": len(m.loops), "attributes": attributes(m),
                       "materials": [s.name if s else None for s in m.materials]}
    if geo.pointcloud:
        out["pointcloud"] = {"points": len(geo.pointcloud.points), "attributes": attributes(geo.pointcloud)}
    if geo.curves:
        out["curves"] = {"curves": len(geo.curves.curves), "points": len(geo.curves.points),
                         "attributes": attributes(geo.curves)}
    if geo.volume:
        out["volume"] = {"grids": len(geo.volume.grids)}
    instances = geo.instances_pointcloud()
    if instances:
        refs = geo.instance_references()
        index = [0] * len(instances.points)
        instances.attributes[".reference_index"].data.foreach_get("value", index)
        counts = [0] * len(refs)
        for i in index:
            counts[i] += 1
        listed = []
        for ref, count in list(zip(refs, counts))[:20]:
            if isinstance(ref, bpy.types.ID):
                what = {"type": type(ref).__name__, "name": ref.name}
            else:
                what = {"type": "geometry", "name": ref.name, "contents": components(ref)}
            listed.append({"count": count, **what})
        out["instances"] = {"count": len(instances.points), "references": listed,
                            "attributes": attributes(instances)}
        if len(refs) > 20:
            out["instances"]["more_references"] = len(refs) - 20
    return out

depsgraph = bpy.context.evaluated_depsgraph_get()
evaluated = obj.evaluated_get(depsgraph)
result = {"object": obj.name, "type": obj.type, **components(evaluated.evaluated_geometry())}
# The object's own bound_box leaves instances out, so take every depsgraph
# instance the object produced (its realized geometry is one of them too).
corners = []
for inst in depsgraph.object_instances:
    owner = inst.parent if inst.is_instance else inst.object
    if owner is not None and owner.original == obj:
        corners.extend(inst.matrix_world @ Vector(c) for c in inst.object.bound_box)
if corners:
    lo = [round(min(c[i] for c in corners), 3) for i in range(3)]
    hi = [round(max(c[i] for c in corners), 3) for i in range(3)]
    result["world_bounding_box"] = [lo, hi]
    result["size"] = [round(hi[i] - lo[i], 3) for i in range(3)]
return result
'''

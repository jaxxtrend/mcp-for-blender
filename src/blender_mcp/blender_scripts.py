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
            if item.description:
                entry["description"] = item.description
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


# Shared by the scripts that change a tree: adding nodes and links from specs in
# gn_get_tree's format, syncing the interface, and editing on a copy that
# replaces the original only once the whole edit succeeded.
_GN_EDIT = r'''
import re

def set_interface_socket(item, e):
    if "default" in e:
        item.default_value = to_socket_value(e["type"], e["default"])
    # Settings the spec leaves out go back to Blender's defaults, so a kept
    # socket doesn't keep old ones (the dump omits "no limit" and VALUE).
    if hasattr(item, "min_value"):
        is_int = isinstance(item.min_value, int)
        limit = 2**31 - 1 if is_int else FLT_MAX
        item.min_value = e.get("min", -limit - is_int)
        item.max_value = e.get("max", limit)
    if hasattr(item, "default_input"):
        item.default_input = e.get("default_input", "VALUE")
    item.description = e.get("description", "")

def interface_root(tree):
    for item in tree.interface.items_tree:
        root = item
        while root.parent is not None:
            root = root.parent
        return root
    return None

def interface_panel(tree, name):
    for item in tree.interface.items_tree:
        if item.item_type == "PANEL" and item.name == name:
            return item
    raise GNError(f"{tree.name!r} has no interface panel {name!r}")

def sync_interface(tree, items):
    """Make the interface match the spec; sockets keep their identifier when the spec gives it.
    Returns {spec id: identifier}."""
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
            set_interface_socket(item, e)
            if e.get("id"):
                id_map[e["id"]] = item.identifier
        placed.append((e, item))
    if placed:
        root = interface_root(tree)
        positions = {}
        for e, item in placed:
            if e.get("in_panel") and e["in_panel"] not in panels:
                raise GNError(f"interface item {e.get('name', e.get('panel'))!r} is in unknown panel {e['in_panel']!r}")
            parent = panels[e["in_panel"]] if e.get("in_panel") else root
            position = positions.get(parent.as_pointer(), 0)
            iface.move_to_parent(item, parent, position)
            positions[parent.as_pointer()] = position + 1
    return id_map

class Sockets:
    """Finds the sockets that node and link specs name.

    Socket ids can come out different from a spec's: recreated item lists
    (zones, capture, menu switch) number their sockets on, and a rebuilt
    interface may renumber group sockets (id_map). A spec names every socket
    whose name differs from its id, so a socket whose id now belongs to a
    differently named socket is found by its name instead.
    """

    def __init__(self, id_map=None):
        self.id_map = id_map or {}
        self.names = {}

    def add(self, specs):
        for e in specs:
            for side in ("inputs", "outputs"):
                self.names[(e["name"], side)] = {s["id"]: s["name"] for s in e.get(side, []) if "name" in s}

    def get(self, node, key, output=False):
        if node.bl_idname in {"NodeGroupInput", "NodeGroupOutput"} and key in self.id_map:
            return find_socket(node, self.id_map[key], output)
        name = self.names.get((node.name, "outputs" if output else "inputs"), {}).get(key)
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

def set_input_values(node, inputs, sockets):
    for s in inputs:
        if "value" in s:
            target = sockets.get(node, s["id"])
            try:
                set_socket_value(target, s["value"])
            except (TypeError, ValueError, AttributeError) as ex:
                raise GNError(f"node {node.name!r}, input {s['id']!r}: {ex}")

def add_nodes(tree, specs, sockets):
    """Add nodes given as specs to the tree; returns them by name."""
    names = [e["name"] for e in specs]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise GNError(f"node names must be unique: {duplicates}")
    taken = [n for n in names if n in tree.nodes]
    if taken:
        raise GNError(f"{tree.name!r} already has nodes named {taken}")
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
    sockets.add(specs)

    def props_of(e):
        return (e.get("props") or {}).items()

    # Settings first (they decide which sockets exist), then zone pairs and
    # item lists, then layout and input values.
    for e in specs:
        node = nodes[e["name"]]
        for key, v in props_of(e):
            prop = node.bl_rna.properties.get(key)
            if key != "paired_output" and not (prop and prop.type == "COLLECTION"):
                set_node_prop(node, key, v)
    for e in specs:
        output = (e.get("props") or {}).get("paired_output")
        if output and not nodes[e["name"]].pair_with_output(find_node(tree, output)):
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
            node.parent = find_node(tree, e["parent"])
        if "location" in e:
            node.location = e["location"]
    for e in specs:
        set_input_values(nodes[e["name"]], e.get("inputs", []), sockets)
    return nodes

def add_link(tree, e, sockets):
    (from_name, from_id), (to_name, to_id) = e["from"], e["to"]
    link = tree.links.new(sockets.get(find_node(tree, from_name), from_id, output=True),
                          sockets.get(find_node(tree, to_name), to_id))
    if e.get("muted"):
        link.is_muted = True
    return link

def add_links(tree, links, sockets):
    # Multi-input sockets order their links by when they were made.
    for _, e in sorted(enumerate(links), key=lambda il: (il[1].get("multi_input_sort_id", 0), il[0])):
        add_link(tree, e, sockets)

def edit_summary(tree):
    result = {"tree": tree.name, "nodes": len(tree.nodes), "links": len(tree.links)}
    invalid = [[l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier]
               for l in tree.links if not l.is_valid]
    if invalid:
        result["invalid_links"] = invalid
    return result

def replace_tree(original, work):
    """Put work in original's place everywhere it is used, under its name."""
    name = original.name
    original.user_remap(work)
    bpy.data.node_groups.remove(original)
    work.name = name

def edit_on_copy(original, edit):
    """Run edit(copy); the copy replaces original only if edit returned."""
    work = original.copy()
    try:
        result = edit(work)
    except GNError as e:
        # Errors name the tree; the user knows it by the original's name, not the copy's.
        message = str(e).replace(repr(work.name), repr(original.name))
        bpy.data.node_groups.remove(work)
        raise GNError(message)
    except BaseException:
        bpy.data.node_groups.remove(work)
        raise
    replace_tree(original, work)
    # Modifiers using the tree add properties for new interface inputs only when told.
    work.interface_update(bpy.context)
    return work, result
'''


# Builds a whole tree from a spec in gn_get_tree's format, on a copy that
# replaces the original only once everything succeeded, so a bad spec leaves the
# user's tree untouched. Interface sockets whose id matches an existing one are
# kept, so values set on modifiers survive.
GN_BUILD = _GN_COMMON + _GN_EDIT + r'''
spec = ARGS["spec"]

def build(tree):
    id_map = sync_interface(tree, spec.get("interface", []))
    tree.nodes.clear()
    sockets = Sockets(id_map)
    add_nodes(tree, spec.get("nodes", []), sockets)
    add_links(tree, spec.get("links", []), sockets)
    tree.description = spec.get("description", "")
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
        work, id_map = edit_on_copy(check_gn_tree(original), build)
    else:
        work = bpy.data.node_groups.new(tree_name or "Geometry Nodes", "GeometryNodeTree")
        try:
            id_map = build(work)
        except BaseException:
            bpy.data.node_groups.remove(work)
            raise
except GNError as e:
    return {"error": str(e)}

if spec.get("name"):
    work.name = spec["name"]
if "is_modifier" in spec:
    work.is_modifier = spec["is_modifier"]
elif original is None:
    work.is_modifier = obj is not None
if obj is not None:
    if mod is None:
        mod = obj.modifiers.new(mod_name or "GeometryNodes", "NODES")
    if mod.node_group != work:
        mod.node_group = work

result = edit_summary(work)
result["interface"] = [{"in_out": i.in_out, "name": i.name, "id": i.identifier}
                       for i in work.interface.items_tree if i.item_type == "SOCKET"]
changed_ids = {old: new for old, new in id_map.items() if old != new}
if changed_ids:
    result["interface_id_changes"] = changed_ids
if obj is not None:
    result["modifier"] = {"object": obj.name, "name": mod.name}
return result
'''


# Small edits to an existing tree, as a list of operations applied in order on a
# copy that replaces the original only if every operation worked.
GN_PATCH = _GN_COMMON + _GN_EDIT + r'''
OPS = ("add_node", "remove_node", "link", "unlink", "set", "rename", "add_socket", "remove_socket")

def interface_socket(tree, key):
    items = [i for i in tree.interface.items_tree if i.item_type == "SOCKET"]
    matches = [i for i in items if i.identifier == key] or [i for i in items if i.name == key]
    if len(matches) != 1:
        available = [f"{i.identifier} ({i.in_out.lower()} {i.name})" for i in items]
        raise GNError(f"{'ambiguous' if matches else 'no'} interface socket {key!r}; sockets: {available}")
    return matches[0]

def apply(tree, op, sockets, added):
    kind = op["op"]
    if kind == "remove_node":
        tree.nodes.remove(find_node(tree, op["name"]))
    elif kind == "link":
        add_link(tree, op, sockets)
    elif kind == "unlink":
        target = sockets.get(find_node(tree, op["to"][0]), op["to"][1])
        source = sockets.get(find_node(tree, op["from"][0]), op["from"][1], output=True) if op.get("from") else None
        links = [l for l in target.links if source is None or l.from_socket == source]
        if not links:
            raise GNError(f"no link into {op['to']}" + (f" from {op['from']}" if source else ""))
        for link in links:
            tree.links.remove(link)
    elif kind == "set":
        node = find_node(tree, op["name"])
        if "label" in op:
            node.label = op["label"]
        if "mute" in op:
            node.mute = op["mute"]
        if "parent" in op:
            node.parent = find_node(tree, op["parent"]) if op["parent"] else None
        if "location" in op:
            node.location = op["location"]
        for key, v in (op.get("props") or {}).items():
            set_node_prop(node, key, v)
        set_input_values(node, op.get("inputs", []), sockets)
    elif kind == "rename":
        node = find_node(tree, op["name"])
        if op["new_name"] in tree.nodes:
            raise GNError(f"{tree.name!r} already has a node named {op['new_name']!r}")
        node.name = op["new_name"]
    elif kind == "add_socket":
        item = tree.interface.new_socket(op["name"], in_out=op["in_out"], socket_type=op["type"])
        set_interface_socket(item, op)
        if op.get("in_panel"):
            panel = interface_panel(tree, op["in_panel"])
            size = sum(1 for i in tree.interface.items_tree if i.parent == panel)
            tree.interface.move_to_parent(item, panel, size)
        added[item.name] = item.identifier
    elif kind == "remove_socket":
        tree.interface.remove(interface_socket(tree, op["id"]))
    else:
        raise GNError(f"unknown op {kind!r}; ops are {list(OPS)}")

def patch(tree):
    sockets, added = Sockets(), {}
    pending = []  # consecutive add_node ops, added together so zones can pair up
    def flush():
        if pending:
            first = pending[0][0]
            try:
                add_nodes(tree, [spec for _, spec in pending], sockets)
            except GNError as e:
                raise GNError(f"op {first} (add_node): {e}")
            pending.clear()
    for index, op in enumerate(ARGS["ops"]):
        if op.get("op") == "add_node":
            pending.append((index, {k: v for k, v in op.items() if k != "op"}))
            continue
        flush()
        try:
            apply(tree, op, sockets, added)
        except (GNError, KeyError, TypeError, ValueError, RuntimeError) as e:
            raise GNError(f"op {index} ({op.get('op')}): {e}")
    flush()
    return added

try:
    tree, obj, mod = find_tree(ARGS)
    work, added = edit_on_copy(tree, patch)
except GNError as e:
    return {"error": str(e)}
result = edit_summary(work)
result["ops"] = len(ARGS["ops"])
if added:
    result["added_sockets"] = added
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


# Shared by the scripts that read evaluated geometry (Blender 4.3+, where
# Object.evaluated_geometry exists): what each component holds, attribute
# statistics, and the world bounding box including instances.
_GN_GEOMETRY = r'''
if bpy.app.version < (4, 3, 0):
    return {"error": f"needs Blender 4.3 or newer (Object.evaluated_geometry), this is {bpy.app.version_string}"}
import numpy as np
from mathutils import Vector

# Attribute data type -> (foreach_get key, values per element, buffer dtype).
ATTRIBUTE_LAYOUT = {
    "FLOAT": ("value", 1, np.float32), "INT": ("value", 1, np.int32), "BOOLEAN": ("value", 1, np.bool_),
    "INT8": ("value", 1, np.int32), "INT16_2D": ("value", 2, np.int32), "INT32_2D": ("value", 2, np.int32),
    "FLOAT2": ("vector", 2, np.float32), "FLOAT_VECTOR": ("vector", 3, np.float32),
    "FLOAT_COLOR": ("color", 4, np.float32), "BYTE_COLOR": ("color", 4, np.float32),
    "QUATERNION": ("value", 4, np.float32), "FLOAT4X4": ("value", 16, np.float32),
}

def geometry_parts(geo):
    """(component name, data) for each non-empty component; instances as their point cloud."""
    parts = [("mesh", geo.mesh), ("pointcloud", geo.pointcloud), ("curves", geo.curves)]
    parts.append(("instances", geo.instances_pointcloud()))
    return [(name, data) for name, data in parts if data]

def attribute_list(data):
    # Dot-prefixed attributes are Blender's internal ones.
    return [[a.name, a.domain, a.data_type] for a in data.attributes if not a.name.startswith(".")]

def attribute_values(attr):
    """An (elements, channels) array, or a list of strings for STRING attributes."""
    if attr.data_type == "STRING":
        # String attributes hold bytes.
        return [d.value.decode("utf-8", "replace") for d in attr.data]
    if attr.data_type not in ATTRIBUTE_LAYOUT:
        raise GNError(f"can't read {attr.data_type} attribute {attr.name!r}")
    key, width, dtype = ATTRIBUTE_LAYOUT[attr.data_type]
    buf = np.empty(len(attr.data) * width, dtype=dtype)
    attr.data.foreach_get(key, buf)
    return buf.reshape(len(attr.data), width)

def attribute_stats(attr, sample=10):
    values = attribute_values(attr)
    out = {"name": attr.name, "domain": attr.domain, "data_type": attr.data_type, "count": len(values)}
    if isinstance(values, list):
        distinct = sorted(set(values))
        out["distinct"] = len(distinct)
        out["sample"] = values[:sample]
        return out
    out["sample"] = [value(row[0].item()) if values.shape[1] == 1 else [value(x.item()) for x in row]
                     for row in values[:sample]]
    if not len(values):
        return out
    if values.dtype == np.bool_:
        out["true"] = int(values.sum())
        return out
    finite = np.isfinite(values) if values.dtype.kind == "f" else np.ones(values.shape, dtype=bool)
    if not finite.all():
        out["non_finite"] = int((~finite).any(axis=1).sum())
    clean = values[finite.all(axis=1)]
    if len(clean):
        single = values.shape[1] == 1
        for stat, fn in (("min", np.min), ("max", np.max), ("mean", np.mean)):
            got = [int(x) if stat != "mean" and values.dtype.kind == "i" else round(float(x), 4)
                   for x in fn(clean, axis=0)]
            out[stat] = got[0] if single else got
    if values.dtype.kind == "i" and values.shape[1] == 1:
        distinct, counts = np.unique(values, return_counts=True)
        out["distinct"] = len(distinct)
        if len(distinct) <= 20:
            out["counts"] = {str(int(v)): int(c) for v, c in zip(distinct, counts)}
    return out

def components(geo):
    out = {}
    if geo.mesh:
        m = geo.mesh
        out["mesh"] = {"vertices": len(m.vertices), "edges": len(m.edges), "faces": len(m.polygons),
                       "corners": len(m.loops), "attributes": attribute_list(m),
                       "materials": [s.name if s else None for s in m.materials]}
    if geo.pointcloud:
        out["pointcloud"] = {"points": len(geo.pointcloud.points), "attributes": attribute_list(geo.pointcloud)}
    if geo.curves:
        out["curves"] = {"curves": len(geo.curves.curves), "points": len(geo.curves.points),
                         "attributes": attribute_list(geo.curves)}
    if geo.volume:
        out["volume"] = {"grids": len(geo.volume.grids)}
    instances = geo.instances_pointcloud()
    if instances:
        refs = geo.instance_references()
        index = np.empty(len(instances.points), dtype=np.int32)
        instances.attributes[".reference_index"].data.foreach_get("value", index)
        counts = np.bincount(index, minlength=len(refs))
        listed = []
        for ref, count in list(zip(refs, counts))[:20]:
            if isinstance(ref, bpy.types.ID):
                what = {"type": type(ref).__name__, "name": ref.name}
            else:
                what = {"type": "geometry", "name": ref.name, "contents": components(ref)}
            listed.append({"count": int(count), **what})
        out["instances"] = {"count": len(instances.points), "references": listed,
                            "attributes": attribute_list(instances)}
        if len(refs) > 20:
            out["instances"]["more_references"] = len(refs) - 20
    return out

def world_bounds(obj, depsgraph):
    """World bounding box of everything the object produced. Its own bound_box
    leaves instances out, so this takes every depsgraph instance it made (its
    realized geometry is one of them too)."""
    corners = []
    for inst in depsgraph.object_instances:
        owner = inst.parent if inst.is_instance else inst.object
        if owner is not None and owner.original == obj:
            corners.extend(inst.matrix_world @ Vector(c) for c in inst.object.bound_box)
    if not corners:
        return {}
    lo = [round(min(c[i] for c in corners), 3) for i in range(3)]
    hi = [round(max(c[i] for c in corners), 3) for i in range(3)]
    return {"world_bounding_box": [lo, hi], "size": [round(hi[i] - lo[i], 3) for i in range(3)]}

def find_attribute(geo, name, component=None):
    """[(component name, attribute)] for every component holding the attribute."""
    found = []
    for part, data in geometry_parts(geo):
        if component and part != component:
            continue
        attr = data.attributes.get(name)
        if attr is not None:
            found.append((part, attr))
    return found
'''


# What an object's modifier stack produced: counts and attributes per geometry
# component, instances by what they instance, and the world bounding box.
GN_EVALUATED = _GN_COMMON + _GN_GEOMETRY + r'''
try:
    obj = find_object(ARGS["object"])
except GNError as e:
    return {"error": str(e)}
depsgraph = bpy.context.evaluated_depsgraph_get()
evaluated = obj.evaluated_get(depsgraph)
return {"object": obj.name, "type": obj.type, **components(evaluated.evaluated_geometry()),
        **world_bounds(obj, depsgraph)}
'''


# Values of one attribute on an object's evaluated geometry, like the
# Spreadsheet: count, min/max/mean per channel, distinct values, a sample.
GN_READ_ATTRIBUTE = _GN_COMMON + _GN_GEOMETRY + r'''
try:
    obj = find_object(ARGS["object"])
    geo = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).evaluated_geometry()
    found = find_attribute(geo, ARGS["name"], ARGS.get("component"))
    if not found:
        available = {part: [a[0] for a in attribute_list(data)] for part, data in geometry_parts(geo)}
        raise GNError(f"no attribute {ARGS['name']!r} on {obj.name!r}"
                      + (f" in {ARGS['component']}" if ARGS.get("component") else "") + f"; it has: {available}")
    sample = max(0, min(int(ARGS.get("sample", 10)), 100))
    return {"object": obj.name, "components": {part: attribute_stats(attr, sample) for part, attr in found}}
except GNError as e:
    return {"error": str(e)}
'''


# What one node output produces in the context of an object's modifier. The
# probe runs on a copy of the tree, wired so the socket feeds the group output,
# which the modifier uses only while it is measured; the user's tree is never
# changed. A geometry output is measured as geometry. A field is stored as an
# attribute on the geometry it is evaluated on (the geometry input of the node it
# feeds, or `context`) and its values are summarized.
GN_PROBE = _GN_COMMON + _GN_GEOMETRY + r'''
STORE_TYPES = {"VALUE": "FLOAT", "INT": "INT", "BOOLEAN": "BOOLEAN", "VECTOR": "FLOAT_VECTOR",
               "RGBA": "FLOAT_COLOR", "ROTATION": "QUATERNION", "MATRIX": "FLOAT4X4"}
PROBE_NAME = "__mcp_probe__"

def field_context(probe, socket, context):
    """The geometry socket a field is evaluated on."""
    if context:
        return find_socket(find_node(probe, context[0]), context[1], output=True)
    for link in socket.links:
        for target in link.to_node.inputs:
            if target.type == "GEOMETRY" and target.is_linked:
                return target.links[0].from_socket
    raise GNError(f"can't tell which geometry {socket.node.name!r} {socket.identifier!r} is evaluated on: "
                  "it feeds no node with a linked geometry input. Pass context=[node, geometry output].")

try:
    if not ARGS.get("object"):
        raise GNError("needs object=: a probe measures the tree on that object's modifier")
    tree, obj, mod = find_tree(ARGS)
    node = find_node(tree, ARGS["node"])
    socket_id = find_socket(node, ARGS["socket"], output=True).identifier
except GNError as e:
    return {"error": str(e)}

probe = tree.copy()
later = [m for m in obj.modifiers[list(obj.modifiers).index(mod) + 1:] if m.show_viewport]
try:
    socket = find_socket(probe.nodes[node.name], socket_id, output=True)
    outputs = [n for n in probe.nodes if n.bl_idname == "NodeGroupOutput" and n.is_active_output]
    geometry_out = next((s for n in outputs for s in n.inputs if s.type == "GEOMETRY"), None)
    if geometry_out is None:
        raise GNError(f"{tree.name!r} has no geometry output to measure through")
    if socket.type == "GEOMETRY":
        source, field = socket, None
    else:
        if socket.type not in STORE_TYPES:
            raise GNError(f"can't measure a {socket.type} socket")
        store = probe.nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = STORE_TYPES[socket.type]
        store.domain = ARGS.get("domain") or "POINT"
        store.inputs["Name"].default_value = PROBE_NAME
        probe.links.new(field_context(probe, socket, ARGS.get("context")), store.inputs["Geometry"])
        probe.links.new(socket, store.inputs["Value"])
        source, field = store.outputs["Geometry"], store
    probe.links.new(source, geometry_out)
    # Measure this modifier's output, not what later modifiers make of it.
    for m in later:
        m.show_viewport = False
    mod.node_group = probe
    depsgraph = bpy.context.evaluated_depsgraph_get()
    geo = obj.evaluated_get(depsgraph).evaluated_geometry()
    result = {"node": node.name, "socket": socket_id, "socket_type": socket.type}
    if field is None:
        result.update(components(geo))
        result.update(world_bounds(obj, depsgraph))
    else:
        found = find_attribute(geo, PROBE_NAME)
        if not found:
            raise GNError(f"the field produced no values on the {field.domain} domain of that geometry")
        sample = max(0, min(int(ARGS.get("sample", 10)), 100))
        result["domain"] = field.domain
        result["evaluated_on"] = [field.inputs["Geometry"].links[0].from_node.name,
                                  field.inputs["Geometry"].links[0].from_socket.identifier]
        result["components"] = {}
        for part, attr in found:
            stats = attribute_stats(attr, sample)
            for key in ("name", "domain"):
                stats.pop(key)
            result["components"][part] = stats
    return result
except GNError as e:
    return {"error": str(e)}
finally:
    mod.node_group = tree
    for m in later:
        m.show_viewport = True
    bpy.data.node_groups.remove(probe)
'''


# Arranges a tree's nodes left to right along the data flow: each node's column
# is its longest path to the end of the tree, and within a column nodes follow
# the height of the nodes they feed. Node sizes are estimated, since Blender
# only knows them once a node editor has drawn them. Frames are left alone.
GN_LAYOUT = _GN_COMMON + r'''
try:
    tree, obj, mod = find_tree(ARGS)
except GNError as e:
    return {"error": str(e)}
gap_x, gap_y = ARGS.get("spacing") or [60, 30]
nodes = [n for n in tree.nodes if n.bl_idname != "NodeFrame"]
feeds = {n.name: set() for n in nodes}
for link in tree.links:
    if link.from_node.name in feeds and link.to_node.name in feeds:
        feeds[link.from_node.name].add(link.to_node.name)
linked = {n for n, targets in feeds.items() if targets} | {t for targets in feeds.values() for t in targets}

columns = {}
def column(name):
    if name not in columns:
        columns[name] = 0  # zones make no cycles; this only guards against looping forever
        columns[name] = 1 + max((column(t) for t in feeds[name]), default=-1)
    return columns[name]
for n in nodes:
    column(n.name)
last = max(columns.values(), default=0)
for n in nodes:
    if n.name not in linked:
        columns[n.name] = last + 1  # unconnected nodes go to the far left

def height(node):
    if node.hide:
        return 32
    rows = len(shown_sockets(node.inputs)) + len(shown_sockets(node.outputs))
    settings = sum(1 for p in node.bl_rna.properties if p.identifier not in BASE_NODE_PROPS
                   and not p.is_readonly and p.type in {"ENUM", "BOOLEAN"})
    return 44 + 22 * rows + 26 * settings

by_column = {}
for n in nodes:
    by_column.setdefault(columns[n.name], []).append(n)
placed, x = {}, 0.0
for c in sorted(by_column):
    members = by_column[c]
    width = max(n.width for n in members)
    if c:
        x -= width + gap_x
    def target_y(n):
        ys = [placed[t] for t in feeds[n.name] if t in placed]
        return sum(ys) / len(ys) if ys else n.location.y
    members.sort(key=target_y, reverse=True)
    total = sum(height(n) for n in members) + gap_y * (len(members) - 1)
    centre = sum(target_y(n) for n in members) / len(members) if c else 0.0
    y = centre + total / 2
    for n in members:
        location = (x, y)
        if hasattr(n, "location_absolute"):
            n.location_absolute = location
        else:
            n.location = location
        placed[n.name] = y - height(n) / 2
        y -= height(n) + gap_y
return {"tree": tree.name, "nodes": len(nodes), "columns": len(by_column)}
'''


# Every Geometry Nodes group in the file: its interface, flags, and who uses it
# (object modifiers, other groups).
GN_LIST_TREES = _GN_COMMON + r'''
by_modifier, by_group = {}, {}
for obj in bpy.data.objects:
    for mod in gn_modifiers(obj):
        if mod.node_group:
            by_modifier.setdefault(mod.node_group.name, []).append([obj.name, mod.name])
trees = [g for g in bpy.data.node_groups if g.bl_idname == "GeometryNodeTree"]
for tree in trees:
    for node in tree.nodes:
        if node.bl_idname == "GeometryNodeGroup" and node.node_tree:
            by_group.setdefault(node.node_tree.name, set()).add(tree.name)
out = []
for tree in trees:
    sockets = [i for i in tree.interface.items_tree if i.item_type == "SOCKET"]
    entry = {"name": tree.name, "nodes": len(tree.nodes),
             "inputs": [i.name for i in sockets if i.in_out == "INPUT"],
             "outputs": [i.name for i in sockets if i.in_out == "OUTPUT"],
             "is_modifier": tree.is_modifier, "is_tool": tree.is_tool,
             "modifiers": by_modifier.get(tree.name, []), "used_in": sorted(by_group.get(tree.name, ()))}
    if tree.description:
        entry["description"] = tree.description
    if tree.asset_data:
        entry["asset"] = True
    if tree.library:
        entry["library"] = tree.library.filepath
    if tree.use_fake_user:
        entry["fake_user"] = True
    out.append(entry)
return {"trees": out}
'''


# Bakes (Bake nodes and simulation zones) of a Geometry Nodes modifier, freeing
# them, or applying the modifier. These are Blender operators, run with the
# object as context.
GN_BAKE = _GN_COMMON + r'''
try:
    obj = find_object(ARGS["object"])
    mod = find_modifier(obj, ARGS.get("modifier"))
    action = ARGS["action"]
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        if action == "apply":
            name = mod.name
            try:
                bpy.ops.object.modifier_apply(modifier=name)
            except RuntimeError as e:
                raise GNError(f"can't apply {name!r}: {e}")
            return {"action": action, "applied": name, "modifiers": [m.name for m in obj.modifiers]}
        if action not in {"bake", "free"}:
            raise GNError(f"unknown action {action!r}: bake, free or apply")
        bakes = [b for b in mod.bakes if not ARGS.get("node") or (b.node and b.node.name == ARGS["node"])]
        if not bakes:
            raise GNError(f"{mod.name!r} has no " + (f"bake node {ARGS['node']!r}" if ARGS.get("node") else
                          "Bake nodes or simulation zones"))
        if ARGS.get("target"):
            mod.bake_target = ARGS["target"]
        done = []
        for b in bakes:
            op = bpy.ops.object.geometry_node_bake_single if action == "bake" else bpy.ops.object.geometry_node_bake_delete_single
            try:
                op(session_uid=obj.session_uid, modifier_name=mod.name, bake_id=b.bake_id)
            except RuntimeError as e:
                raise GNError(f"can't {action} {b.node.name if b.node else b.bake_id!r}: {e}")
            done.append({"node": b.node.name if b.node else None, "mode": b.bake_mode})
    target = mod.bake_target
    return {"action": action, "bakes": done, "target": target}
except GNError as e:
    return {"error": str(e)}
'''


# Collapses nodes into a new node group (linked through its interface) or
# expands a group node back into the tree. Runs on a copy like a patch, so a
# failure changes nothing.
GN_GROUP_NODES = _GN_COMMON + _GN_EDIT + r'''
import copy

GROUP_IO = {"NodeGroupInput", "NodeGroupOutput"}
INTERFACE_TYPES = {
    "VALUE": "NodeSocketFloat", "INT": "NodeSocketInt", "BOOLEAN": "NodeSocketBool", "VECTOR": "NodeSocketVector",
    "RGBA": "NodeSocketColor", "STRING": "NodeSocketString", "ROTATION": "NodeSocketRotation",
    "MATRIX": "NodeSocketMatrix", "MENU": "NodeSocketMenu", "GEOMETRY": "NodeSocketGeometry",
    "OBJECT": "NodeSocketObject", "COLLECTION": "NodeSocketCollection", "MATERIAL": "NodeSocketMaterial",
    "IMAGE": "NodeSocketImage", "TEXTURE": "NodeSocketTexture", "BUNDLE": "NodeSocketBundle",
    "CLOSURE": "NodeSocketClosure", "SHADER": "NodeSocketShader",
}

def interface_type(socket):
    if socket.type not in INTERFACE_TYPES:
        raise GNError(f"can't pass a {socket.type} socket through a group interface")
    return INTERFACE_TYPES[socket.type]

def center(specs):
    xs = [e["location"][0] for e in specs]
    ys = [e["location"][1] for e in specs]
    return [sum(xs) / len(xs), sum(ys) / len(ys)], min(xs), max(xs)

def make_group(tree):
    names = ARGS.get("nodes") or []
    if not names:
        raise GNError("give the names of the nodes to group in nodes=")
    chosen = set(names)
    for name in names:
        if find_node(tree, name).bl_idname in GROUP_IO:
            raise GNError(f"{name!r} is a group input/output node and can't go into a group")
    for node in tree.nodes:
        paired = getattr(node, "paired_output", None)
        if paired is not None and (node.name in chosen) != (paired.name in chosen):
            raise GNError(f"zone {node.name!r} / {paired.name!r}: group both ends of a zone or neither")
    dump = dump_tree(tree)
    specs = [copy.deepcopy(e) for e in dump["nodes"] if e["name"] in chosen]
    for e in specs:
        if e.get("parent") not in chosen:
            e.pop("parent", None)
    inside = [e for e in dump["links"] if e["from"][0] in chosen and e["to"][0] in chosen]

    group = bpy.data.node_groups.new(ARGS.get("name") or "Group", "GeometryNodeTree")
    try:
        inputs, outputs, incoming, outgoing = {}, {}, [], []
        for link in tree.links:
            src_in, dst_in = link.from_node.name in chosen, link.to_node.name in chosen
            key = (link.from_node.name, link.from_socket.identifier)
            if dst_in and not src_in:
                if key not in inputs:
                    # Named after the tree input it passes on, else after the input it feeds.
                    name = link.from_socket.name if link.from_node.bl_idname == "NodeGroupInput" else link.to_socket.name
                    inputs[key] = group.interface.new_socket(
                        name, in_out="INPUT", socket_type=interface_type(link.to_socket)).identifier
                incoming.append((key, link.to_node.name, link.to_socket.identifier))
            elif src_in and not dst_in:
                if key not in outputs:
                    outputs[key] = group.interface.new_socket(
                        link.from_socket.name, in_out="OUTPUT", socket_type=interface_type(link.from_socket)).identifier
                outgoing.append((key, link.to_node.name, link.to_socket.identifier))
        (cx, cy), lo, hi = center(specs)
        group_in, group_out = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
        group_in.location, group_out.location = (lo - 250, cy), (hi + 250, cy)
        sockets = Sockets()
        add_nodes(group, specs, sockets)
        add_links(group, inside, sockets)
        for key, to_node, to_id in incoming:
            group.links.new(find_socket(group_in, inputs[key], output=True), sockets.get(group.nodes[to_node], to_id))
        for key, to_node, to_id in outgoing:
            group.links.new(sockets.get(group.nodes[key[0]], key[1], output=True), find_socket(group_out, outputs[key]))

        node = tree.nodes.new("GeometryNodeGroup")
        node.node_tree = group
        node.name = group.name
        node.location = (cx, cy)
        parents = {e.get("parent") for e in dump["nodes"] if e["name"] in chosen}
        if len(parents) == 1 and None not in parents and parents <= set(tree.nodes.keys()) - chosen:
            node.parent = tree.nodes[parents.pop()]
        for key, _, _ in incoming:
            if not find_socket(node, inputs[key]).is_linked:
                tree.links.new(find_socket(tree.nodes[key[0]], key[1], output=True), find_socket(node, inputs[key]))
        for key, to_node, to_id in outgoing:
            tree.links.new(find_socket(node, outputs[key], output=True), find_socket(tree.nodes[to_node], to_id))
        for name in names:
            tree.nodes.remove(tree.nodes[name])
    except BaseException:
        bpy.data.node_groups.remove(group)
        raise
    sockets = [i for i in group.interface.items_tree if i.item_type == "SOCKET"]
    return {"group": group.name, "node": node.name,
            "inputs": [i.name for i in sockets if i.in_out == "INPUT"],
            "outputs": [i.name for i in sockets if i.in_out == "OUTPUT"]}

def ungroup(tree):
    node = find_node(tree, ARGS.get("node") or "")
    if node.bl_idname != "GeometryNodeGroup" or node.node_tree is None:
        raise GNError(f"{node.name!r} is not a group node")
    inner = node.node_tree
    dump = dump_tree(inner)
    io = {n.name for n in inner.nodes if n.bl_idname in GROUP_IO}
    specs = [copy.deepcopy(e) for e in dump["nodes"] if e["name"] not in io]
    renamed, taken = {}, set(tree.nodes.keys()) - {node.name}
    for e in specs:
        new = e["name"]
        count = 0
        while new in taken:
            count += 1
            new = f"{e['name']}.{node.name}" + (f".{count}" if count > 1 else "")
        renamed[e["name"]] = new
        taken.add(new)
    if specs:
        (cx, cy), _, _ = center(specs)
        dx, dy = node.location.x - cx, node.location.y - cy
    for e in specs:
        e["name"] = renamed[e["name"]]
        if e.get("parent"):
            e["parent"] = renamed[e["parent"]]
        elif node.parent:
            e["parent"] = node.parent.name
        if (e.get("props") or {}).get("paired_output"):
            e["props"]["paired_output"] = renamed[e["props"]["paired_output"]]
        e["location"] = [e["location"][0] + dx, e["location"][1] + dy]
    sockets = Sockets()
    add_nodes(tree, specs, sockets)
    add_links(tree, [{**e, "from": [renamed[e["from"][0]], e["from"][1]], "to": [renamed[e["to"][0]], e["to"][1]]}
                     for e in dump["links"] if e["from"][0] not in io and e["to"][0] not in io], sockets)
    sources = {s.identifier: (s.links[0].from_socket if s.is_linked else None, s) for s in node.inputs}
    targets = {s.identifier: [l.to_socket for l in s.links] for s in node.outputs}
    for link in inner.links:
        from_io, to_io = link.from_node.name in io, link.to_node.name in io
        if from_io and link.from_socket.identifier not in sources:
            continue
        if from_io and to_io:
            source = sources[link.from_socket.identifier][0]
            for target in targets.get(link.to_socket.identifier, []):
                if source is not None:
                    tree.links.new(source, target)
        elif from_io:
            target = sockets.get(tree.nodes[renamed[link.to_node.name]], link.to_socket.identifier)
            source, group_socket = sources[link.from_socket.identifier]
            if source is not None:
                tree.links.new(source, target)
            elif hasattr(group_socket, "default_value") and hasattr(target, "default_value"):
                # The group node's own value for that input moves to the node that used it.
                set_socket_value(target, value(group_socket.default_value))
        elif to_io:
            source = sockets.get(tree.nodes[renamed[link.from_node.name]], link.from_socket.identifier, output=True)
            for target in targets.get(link.to_socket.identifier, []):
                tree.links.new(source, target)
    tree.nodes.remove(node)
    return {"ungrouped": inner.name, "added_nodes": list(renamed.values())}

try:
    tree, obj, mod = find_tree(ARGS)
    action = ARGS.get("action")
    if action not in {"group", "ungroup"}:
        raise GNError(f"unknown action {action!r}: group or ungroup")
    work, result = edit_on_copy(tree, make_group if action == "group" else ungroup)
except GNError as e:
    return {"error": str(e)}
return {**edit_summary(work), **result}
'''


# Geometry Nodes groups in asset files: Blender's own Essentials, or the user's
# asset libraries from Preferences. Listing links each file for a moment to read
# its groups' types and descriptions, then removes the link again; files the
# user has linked already are skipped, so their links stay as they are.
GN_ASSETS = _GN_COMMON + r'''
import glob
import os

def asset_files(source):
    if source not in {"essentials", "user", "all"}:
        raise GNError(f"unknown source {source!r}: essentials, user or all")
    roots = []
    if source in {"essentials", "all"}:
        roots.append(("essentials", bpy.utils.system_resource("DATAFILES", path="assets")))
    if source in {"user", "all"}:
        roots += [(lib.name, lib.path) for lib in bpy.context.preferences.filepaths.asset_libraries]
    for label, root in roots:
        if root and os.path.isdir(root):
            for path in sorted(glob.glob(os.path.join(root, "**", "*.blend"), recursive=True)):
                yield label, path

def loaded_library(path):
    for lib in bpy.data.libraries:
        if os.path.exists(bpy.path.abspath(lib.filepath)) and os.path.samefile(bpy.path.abspath(lib.filepath), path):
            return lib
    return None

def list_assets(source, words):
    found, skipped = [], []
    for label, path in asset_files(source):
        with bpy.data.libraries.load(path, assets_only=True) as (src, dst):
            if not src.node_groups:
                continue
        if loaded_library(path):
            skipped.append(path)
            continue
        with bpy.data.libraries.load(path, link=True, assets_only=True) as (src, dst):
            dst.node_groups = list(src.node_groups)
        try:
            for group in dst.node_groups:
                if group is None or group.bl_idname != "GeometryNodeTree":
                    continue
                description = group.asset_data.description if group.asset_data else ""
                text = f"{group.name} {description}".lower()
                if all(w in text for w in words):
                    found.append({"name": group.name, "description": description, "library": label,
                                  "file": os.path.basename(path)})
        finally:
            bpy.data.libraries.remove(loaded_library(path))
    out = {"assets": found}
    if source in {"user", "all"}:
        out["user_libraries"] = [[lib.name, lib.path] for lib in bpy.context.preferences.filepaths.asset_libraries]
    if skipped:
        out["skipped_linked_files"] = skipped
    return out

def append_asset(source, name, file_name):
    matches = []
    for label, path in asset_files(source):
        if file_name and os.path.basename(path) != file_name:
            continue
        with bpy.data.libraries.load(path, assets_only=True) as (src, dst):
            if name in src.node_groups:
                matches.append(path)
    if not matches:
        raise GNError(f"no asset node group {name!r} in {source} asset files")
    if len(matches) > 1:
        raise GNError(f"{name!r} is in several files, pass file=: {[os.path.basename(p) for p in matches]}")
    before = set(bpy.data.node_groups)
    with bpy.data.libraries.load(matches[0], link=False) as (src, dst):
        dst.node_groups = [name]
    group = dst.node_groups[0]
    added = sorted(g.name for g in set(bpy.data.node_groups) - before)
    sockets = [i for i in group.interface.items_tree if i.item_type == "SOCKET"]
    return {"appended": group.name, "file": os.path.basename(matches[0]), "added_groups": added,
            "inputs": [i.name for i in sockets if i.in_out == "INPUT"],
            "outputs": [i.name for i in sockets if i.in_out == "OUTPUT"]}

try:
    source = ARGS.get("source") or "essentials"
    if ARGS.get("name"):
        return append_asset(source, ARGS["name"], ARGS.get("file"))
    return list_assets(source, (ARGS.get("query") or "").lower().split())
except GNError as e:
    return {"error": str(e)}
'''

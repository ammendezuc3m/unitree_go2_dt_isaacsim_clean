import math
import omni.usd
from pxr import UsdGeom, UsdShade, Sdf, Gf

stage = omni.usd.get_context().get_stage()
if stage is None:
    raise RuntimeError("No hay stage abierto en Isaac Sim")


# ============================================================
# CONFIG
# ============================================================

ROOT_PATH = "/World/SensorVisuals"

# Ajusta estas posiciones si quieres mover los visuales.
# Están en coordenadas del escenario de Isaac.

MMWAVE_AP_1_POS = (-1.48042, 0.73858, 0.82844)
MMWAVE_AP_1_YAW_DEG = -180.0

MMWAVE_AP_2_POS = (2.49758, 1.86735, 0.82844)
MMWAVE_AP_2_YAW_DEG = 0.0

CAMERA_POS = (-0.50910, 1.77860, 2.18236)
CAMERA_YAW_DEG = 174.912

# Parches de suelo decorativos / cobertura
MMWAVE_PATCH_1_CENTER = (0.95, 1.35, 0.002)
MMWAVE_PATCH_1_SIZE = (1.65, 1.15)

MMWAVE_PATCH_2_CENTER = (1.65, 2.25, 0.002)
MMWAVE_PATCH_2_SIZE = (1.35, 1.15)

CAMERA_PATCH_CENTER = (-0.15, 1.95, 0.002)
CAMERA_PATCH_SIZE = (1.10, 2.25)

# Alturas / tamaños visuales
DISC_THICKNESS = 0.012
BEAM_HEIGHT = 0.01
PULSE_SPHERE_RADIUS = 0.045

# Colores
COLOR_MMWAVE = (0.00, 0.85, 1.00)
COLOR_MMWAVE_SOFT = (0.45, 0.95, 1.00)
COLOR_CAMERA = (1.00, 0.55, 0.00)
COLOR_CAMERA_SOFT = (1.00, 0.78, 0.35)
COLOR_WHITE = (1.0, 1.0, 1.0)
COLOR_RED = (1.0, 0.2, 0.2)


# ============================================================
# HELPERS
# ============================================================

def ensure_xform(path: str):
    prim = stage.GetPrimAtPath(path)
    if prim.IsValid():
        return prim
    return UsdGeom.Xform.Define(stage, path).GetPrim()


def remove_if_exists(path: str):
    prim = stage.GetPrimAtPath(path)
    if prim.IsValid():
        stage.RemovePrim(path)


def clear_root(path: str):
    remove_if_exists(path)
    return ensure_xform(path)


def get_or_create_xform_ops(prim):
    xformable = UsdGeom.Xformable(prim)
    translate_op = None
    orient_op = None
    scale_op = None

    for op in xformable.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            translate_op = op
        elif op.GetOpType() == UsdGeom.XformOp.TypeOrient:
            orient_op = op
        elif op.GetOpType() == UsdGeom.XformOp.TypeScale:
            scale_op = op

    if translate_op is None:
        translate_op = xformable.AddTranslateOp()
    if orient_op is None:
        orient_op = xformable.AddOrientOp(UsdGeom.XformOp.PrecisionFloat)
    if scale_op is None:
        scale_op = xformable.AddScaleOp()

    return translate_op, orient_op, scale_op


def quatf_identity():
    return Gf.Quatf(1.0, Gf.Vec3f(0.0, 0.0, 0.0))


def quatf_from_yaw_deg(yaw_deg: float) -> Gf.Quatf:
    yaw_rad = math.radians(yaw_deg)
    cy = math.cos(yaw_rad * 0.5)
    sy = math.sin(yaw_rad * 0.5)
    return Gf.Quatf(float(cy), Gf.Vec3f(0.0, 0.0, float(sy)))


def quat_from_z_to_vec(direction: Gf.Vec3d) -> Gf.Quatf:
    d = Gf.Vec3d(direction[0], direction[1], direction[2])
    if d.GetLength() < 1e-9:
        return Gf.Quatf(1.0, Gf.Vec3f(0.0, 0.0, 0.0))
    d.Normalize()
    rot = Gf.Rotation(Gf.Vec3d(0.0, 0.0, 1.0), d)
    q = rot.GetQuat()
    return Gf.Quatf(
        float(q.GetReal()),
        Gf.Vec3f(
            float(q.GetImaginary()[0]),
            float(q.GetImaginary()[1]),
            float(q.GetImaginary()[2]),
        ),
    )


def set_xform(prim, translate=None, orient=None, scale=None):
    t_op, o_op, s_op = get_or_create_xform_ops(prim)

    if translate is not None:
        t_op.Set(Gf.Vec3d(*translate))

    if orient is not None:
        if isinstance(orient, Gf.Quatd):
            orient = Gf.Quatf(
                float(orient.GetReal()),
                Gf.Vec3f(
                    float(orient.GetImaginary()[0]),
                    float(orient.GetImaginary()[1]),
                    float(orient.GetImaginary()[2]),
                ),
            )
        o_op.Set(orient)

    if scale is not None:
        s_op.Set(Gf.Vec3f(*scale))


def create_material(mat_path: str, color=(1, 1, 1), opacity=1.0, emissive=0.0):
    mat = UsdShade.Material.Define(stage, mat_path)
    shader = UsdShade.Shader.Define(stage, mat_path + "/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")

    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(float(color[0]), float(color[1]), float(color[2]))
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.25)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(opacity))
    shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(float(color[0] * emissive), float(color[1] * emissive), float(color[2] * emissive))
    )

    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def bind_material(prim, material):
    UsdShade.MaterialBindingAPI(prim).Bind(material)


def create_cube(path, size=(1, 1, 1), translate=(0, 0, 0), orient=None, color=(1, 1, 1), opacity=1.0, emissive=0.0):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    prim = cube.GetPrim()
    set_xform(prim, translate=translate, orient=orient or quatf_identity(), scale=size)
    mat = create_material(path + "/Looks/Mat", color=color, opacity=opacity, emissive=emissive)
    bind_material(prim, mat)
    return prim


def create_cylinder(path, radius=0.1, height=0.1, axis="Z", translate=(0, 0, 0), orient=None, color=(1, 1, 1), opacity=1.0, emissive=0.0):
    cyl = UsdGeom.Cylinder.Define(stage, path)
    cyl.CreateRadiusAttr(float(radius))
    cyl.CreateHeightAttr(float(height))
    cyl.CreateAxisAttr(axis)
    prim = cyl.GetPrim()
    set_xform(prim, translate=translate, orient=orient or quatf_identity(), scale=(1, 1, 1))
    mat = create_material(path + "/Looks/Mat", color=color, opacity=opacity, emissive=emissive)
    bind_material(prim, mat)
    return prim


def create_sphere(path, radius=0.1, translate=(0, 0, 0), orient=None, color=(1, 1, 1), opacity=1.0, emissive=0.0):
    sph = UsdGeom.Sphere.Define(stage, path)
    sph.CreateRadiusAttr(float(radius))
    prim = sph.GetPrim()
    set_xform(prim, translate=translate, orient=orient or quatf_identity(), scale=(1, 1, 1))
    mat = create_material(path + "/Looks/Mat", color=color, opacity=opacity, emissive=emissive)
    bind_material(prim, mat)
    return prim


def create_flat_disc(path, center=(0, 0, 0), radius=0.5, color=(1, 1, 1), opacity=0.2, emissive=0.15):
    return create_cylinder(
        path=path,
        radius=radius,
        height=DISC_THICKNESS,
        axis="Z",
        translate=center,
        orient=Gf.Quatf(1.0, Gf.Vec3f(0.0, 0.0, 0.0)),
        color=color,
        opacity=opacity,
        emissive=emissive,
    )


def create_floor_patch(path, center=(0, 0, 0), size=(1, 1), yaw_deg=0.0, color=(1, 1, 1), opacity=0.15, emissive=0.08):
    quat = quatf_from_yaw_deg(yaw_deg)
    return create_cube(
        path=path,
        size=(float(size[0]), float(size[1]), 0.004),
        translate=center,
        orient=quat,
        color=color,
        opacity=opacity,
        emissive=emissive,
    )


def create_line_beam(path, start=(0, 0, 0), end=(1, 0, 0), radius=0.015, color=(1, 1, 1), opacity=0.35, emissive=0.4):
    s = Gf.Vec3d(*start)
    e = Gf.Vec3d(*end)
    d = e - s
    length = d.GetLength()
    if length < 1e-6:
        return None

    center = (s + e) * 0.5
    orient = quat_from_z_to_vec(d)

    return create_cylinder(
        path=path,
        radius=radius,
        height=float(length),
        axis="Z",
        translate=(float(center[0]), float(center[1]), float(center[2])),
        orient=orient,
        color=color,
        opacity=opacity,
        emissive=emissive,
    )


def create_pulse_sphere(path, center=(0, 0, 0), radius=0.05, color=(1, 1, 1), opacity=0.6, emissive=0.8):
    return create_sphere(
        path=path,
        radius=radius,
        translate=center,
        orient=Gf.Quatf(1.0, Gf.Vec3f(0.0, 0.0, 0.0)),
        color=color,
        opacity=opacity,
        emissive=emissive,
    )


# ============================================================
# BUILD VISUALS
# ============================================================

root = clear_root(ROOT_PATH)

mmwave_root = ensure_xform(ROOT_PATH + "/MMWave")
camera_root = ensure_xform(ROOT_PATH + "/Camera")

# ----------------------------
# MMWAVE AP 1
# ----------------------------
ap1_root = ensure_xform(ROOT_PATH + "/MMWave/AP_1")
set_xform(ap1_root, translate=MMWAVE_AP_1_POS, orient=quatf_from_yaw_deg(MMWAVE_AP_1_YAW_DEG), scale=(1, 1, 1))

create_flat_disc(
    ROOT_PATH + "/MMWave/AP_1/ap_halo_inner",
    center=(MMWAVE_AP_1_POS[0], MMWAVE_AP_1_POS[1], MMWAVE_AP_1_POS[2]),
    radius=0.28,
    color=COLOR_MMWAVE,
    opacity=0.18,
    emissive=0.55,
)

create_flat_disc(
    ROOT_PATH + "/MMWave/AP_1/ap_halo_outer",
    center=(MMWAVE_AP_1_POS[0], MMWAVE_AP_1_POS[1], MMWAVE_AP_1_POS[2]),
    radius=0.42,
    color=COLOR_MMWAVE_SOFT,
    opacity=0.12,
    emissive=0.35,
)

create_floor_patch(
    ROOT_PATH + "/MMWave/AP_1/floor_patch",
    center=MMWAVE_PATCH_1_CENTER,
    size=MMWAVE_PATCH_1_SIZE,
    yaw_deg=0.0,
    color=COLOR_MMWAVE,
    opacity=0.10,
    emissive=0.20,
)

create_line_beam(
    ROOT_PATH + "/MMWave/AP_1/beam_to_patch",
    start=MMWAVE_AP_1_POS,
    end=(MMWAVE_PATCH_1_CENTER[0], MMWAVE_PATCH_1_CENTER[1], MMWAVE_AP_1_POS[2] - 0.35),
    radius=0.012,
    color=COLOR_MMWAVE_SOFT,
    opacity=0.25,
    emissive=0.45,
)

create_pulse_sphere(
    ROOT_PATH + "/MMWave/AP_1/pulse",
    center=(MMWAVE_PATCH_1_CENTER[0], MMWAVE_PATCH_1_CENTER[1], 0.09),
    radius=PULSE_SPHERE_RADIUS,
    color=COLOR_MMWAVE,
    opacity=0.65,
    emissive=0.9,
)

# ----------------------------
# MMWAVE AP 2
# ----------------------------
ap2_root = ensure_xform(ROOT_PATH + "/MMWave/AP_2")
set_xform(ap2_root, translate=MMWAVE_AP_2_POS, orient=quatf_from_yaw_deg(MMWAVE_AP_2_YAW_DEG), scale=(1, 1, 1))

create_flat_disc(
    ROOT_PATH + "/MMWave/AP_2/ap_halo_inner",
    center=(MMWAVE_AP_2_POS[0], MMWAVE_AP_2_POS[1], MMWAVE_AP_2_POS[2]),
    radius=0.28,
    color=COLOR_MMWAVE,
    opacity=0.18,
    emissive=0.55,
)

create_flat_disc(
    ROOT_PATH + "/MMWave/AP_2/ap_halo_outer",
    center=(MMWAVE_AP_2_POS[0], MMWAVE_AP_2_POS[1], MMWAVE_AP_2_POS[2]),
    radius=0.42,
    color=COLOR_MMWAVE_SOFT,
    opacity=0.12,
    emissive=0.35,
)

create_floor_patch(
    ROOT_PATH + "/MMWave/AP_2/floor_patch",
    center=MMWAVE_PATCH_2_CENTER,
    size=MMWAVE_PATCH_2_SIZE,
    yaw_deg=0.0,
    color=COLOR_MMWAVE_SOFT,
    opacity=0.10,
    emissive=0.20,
)

create_line_beam(
    ROOT_PATH + "/MMWave/AP_2/beam_to_patch",
    start=MMWAVE_AP_2_POS,
    end=(MMWAVE_PATCH_2_CENTER[0], MMWAVE_PATCH_2_CENTER[1], MMWAVE_AP_2_POS[2] - 0.35),
    radius=0.012,
    color=COLOR_MMWAVE_SOFT,
    opacity=0.25,
    emissive=0.45,
)

create_pulse_sphere(
    ROOT_PATH + "/MMWave/AP_2/pulse",
    center=(MMWAVE_PATCH_2_CENTER[0], MMWAVE_PATCH_2_CENTER[1], 0.09),
    radius=PULSE_SPHERE_RADIUS,
    color=COLOR_MMWAVE,
    opacity=0.65,
    emissive=0.9,
)

# ----------------------------
# CAMERA
# ----------------------------
cam_root = ensure_xform(ROOT_PATH + "/Camera/Main")
set_xform(cam_root, translate=CAMERA_POS, orient=quatf_from_yaw_deg(CAMERA_YAW_DEG), scale=(1, 1, 1))

create_flat_disc(
    ROOT_PATH + "/Camera/Main/cam_halo_inner",
    center=(CAMERA_POS[0], CAMERA_POS[1], CAMERA_POS[2]),
    radius=0.22,
    color=COLOR_CAMERA,
    opacity=0.16,
    emissive=0.55,
)

create_flat_disc(
    ROOT_PATH + "/Camera/Main/cam_halo_outer",
    center=(CAMERA_POS[0], CAMERA_POS[1], CAMERA_POS[2]),
    radius=0.34,
    color=COLOR_CAMERA_SOFT,
    opacity=0.10,
    emissive=0.35,
)

create_floor_patch(
    ROOT_PATH + "/Camera/Main/floor_patch",
    center=CAMERA_PATCH_CENTER,
    size=CAMERA_PATCH_SIZE,
    yaw_deg=0.0,
    color=COLOR_CAMERA,
    opacity=0.08,
    emissive=0.18,
)

create_line_beam(
    ROOT_PATH + "/Camera/Main/beam_to_patch_center",
    start=CAMERA_POS,
    end=(CAMERA_PATCH_CENTER[0], CAMERA_PATCH_CENTER[1], 0.15),
    radius=0.010,
    color=COLOR_CAMERA_SOFT,
    opacity=0.18,
    emissive=0.45,
)

create_line_beam(
    ROOT_PATH + "/Camera/Main/beam_to_patch_top",
    start=CAMERA_POS,
    end=(CAMERA_PATCH_CENTER[0], CAMERA_PATCH_CENTER[1] + 0.85, 0.12),
    radius=0.008,
    color=COLOR_CAMERA_SOFT,
    opacity=0.12,
    emissive=0.30,
)

create_line_beam(
    ROOT_PATH + "/Camera/Main/beam_to_patch_bottom",
    start=CAMERA_POS,
    end=(CAMERA_PATCH_CENTER[0], CAMERA_PATCH_CENTER[1] - 0.85, 0.12),
    radius=0.008,
    color=COLOR_CAMERA_SOFT,
    opacity=0.12,
    emissive=0.30,
)

create_pulse_sphere(
    ROOT_PATH + "/Camera/Main/pulse",
    center=(CAMERA_PATCH_CENTER[0], CAMERA_PATCH_CENTER[1] + 0.25, 0.11),
    radius=0.04,
    color=COLOR_CAMERA,
    opacity=0.65,
    emissive=0.95,
)

# ----------------------------
# CENTRAL STATUS MARKERS (solo decorativos)
# ----------------------------
create_pulse_sphere(
    ROOT_PATH + "/Markers/mmwave_status_marker",
    center=(1.15, 1.55, 0.10),
    radius=0.035,
    color=COLOR_MMWAVE,
    opacity=0.75,
    emissive=1.0,
)

create_pulse_sphere(
    ROOT_PATH + "/Markers/camera_status_marker",
    center=(-0.10, 1.20, 0.10),
    radius=0.035,
    color=COLOR_CAMERA,
    opacity=0.75,
    emissive=1.0,
)

print("Sensor visuals created successfully at:", ROOT_PATH)

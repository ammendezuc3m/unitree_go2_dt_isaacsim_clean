# go2_assets

This folder contains USD scenes and visual assets used by Isaac Sim.

At the moment, this repository may not include all heavy Isaac assets or all complete scenario files, because large assets can quickly make the Git repository difficult to clone. If a complete scenario is not included, this README explains how to create or add one.

---

## 1. Expected role of this folder

The digital twin sync script expects the Isaac Sim stage to contain a Go2 robot and several optional person/marker prims. The folder `go2_assets/` is intended to store:

- USD scenes (`.usd`, `.usda`, `.usdc`);
- lightweight reusable assets;
- demo scenario folders;
- references to external or generated assets;
- documentation explaining how to recreate the scenario.

Recommended structure:

```text
go2_assets/
├── README.md
├── go2/
│   └── prueba_demo2_new_scenario.usd
├── scenarios/
│   └── demo_room.usd
└── external_assets/
    └── README.md
```

The validated scene used during development was:

```text
go2_assets/go2/prueba_demo2_new_scenario.usd
```

Inside Isaac Docker, if the repository is mounted as `/workspace`, open:

```text
/workspace/go2_assets/go2/prueba_demo2_new_scenario.usd
```

If that file is not present, create a new scene following the requirements below.

---

## 2. Expected prims for the sync script

The sync script expects at least the robot prim:

```text
/World/go2
```

The full validated scene used these paths:

```text
/World/go2
/World/go2/map
/World/person_1
/World/person_02
/World/person_03
```

Meaning:

| Prim | Purpose |
|---|---|
| `/World/go2` | Main robot transform. |
| `/World/go2/map` | Articulation root used by the Go2 model. |
| `/World/person_1` | Person visual controlled by CSI/mmWave state. |
| `/World/person_02` | Person/obstacle visual controlled by LiDAR/SLAM state. |
| `/World/person_03` | Person visual controlled by camera state. |

If you create a new scene, keep these paths or update the constants in:

```text
go2_dt/ros2_ws/go2_dt_sync_demo2_new.py
```

Important constants include:

```text
ROBOT_XFORM_PATH
ARTICULATION_ROOT_PATH
MMWAVE_PERSON_PRIM_PATH
LIDAR_PERSON_PRIM_PATH
CAMERA_PERSON_PRIM_PATH
```

---

## 3. Opening a scene in Isaac Sim

Launch Isaac Sim and use:

```text
File → Open
```

Then browse to:

```text
/workspace/go2_assets/
```

Recommended validated scene path:

```text
/workspace/go2_assets/go2/prueba_demo2_new_scenario.usd
```

You may open any other `.usd` file if it contains the expected prims or if you update the sync script paths accordingly.

---

## 4. Creating a simple scenario directly in Isaac Sim

You can create a basic scenario inside Isaac Sim without Blender:

1. Open Isaac Sim.
2. Create a new stage.
3. Add a ground plane:
   - `Create → Mesh → Plane`, or use Isaac/Omniverse ground plane tools.
4. Add simple geometry:
   - cubes for walls;
   - cylinders or capsules for obstacles;
   - simple person placeholders at `/World/person_1`, `/World/person_02`, `/World/person_03`.
5. Import or reference the Go2 USD model.
6. Ensure the Go2 root prim is located at:

```text
/World/go2
```

7. Save the scene as:

```text
/workspace/go2_assets/go2/my_scenario.usd
```

If you change the robot or person prim paths, update `go2_dt_sync_demo2_new.py`.

---

## 5. Creating assets from Blender

You can create custom assets in Blender and export them for Isaac Sim.

Recommended process:

1. Model the object or environment in Blender.
2. Apply scale and transforms.
3. Keep the mesh origin consistent.
4. Export as either:
   - `.obj` for simple geometry;
   - `.fbx` if materials/animations are needed;
   - `.usd` if you have a USD export workflow.
5. Import the asset into Isaac Sim.
6. Convert or save the final scene as `.usd`.

Example structure:

```text
go2_assets/
├── imported/
│   ├── wall.obj
│   ├── table.obj
│   └── room_layout.obj
└── scenarios/
    └── room_with_go2.usd
```

Large `.obj`, texture and `.usd` files should be tracked carefully. If a file is large, consider Git LFS.

---

## 6. Using built-in Isaac Sim assets

Isaac Sim includes many built-in assets and primitives. You can create a scenario using:

- default ground plane;
- cube/mesh primitives;
- built-in warehouse/room assets;
- simple capsule/cylinder people placeholders;
- imported Go2 USD model.

Once arranged, save the stage under `go2_assets/`.

---

## 7. Adding a scenario to GitHub

If the scenario is small enough:

```bash
git add go2_assets/go2/prueba_demo2_new_scenario.usd
git commit -m "Add example Isaac Sim scenario"
git push
```

If the scenario is large, use Git LFS:

```bash
git lfs install
git lfs track "*.usd"
git lfs track "*.usda"
git lfs track "*.usdc"
git lfs track "*.obj"
git add .gitattributes
git add go2_assets/go2/prueba_demo2_new_scenario.usd
git commit -m "Add example Isaac Sim scenario with LFS"
git push
```

Before pushing, check size:

```bash
find go2_assets -type f -size +50M -printf '%s %p\n' | sort -nr | numfmt --field=1 --to=iec
```

---

## 8. Mounting rule

The Docker command should mount the project root as `/workspace`:

```bash
-v /home/nextnet/AlbertoDir:/workspace:rw
```

or, if using the clean repository directly:

```bash
-v /home/nextnet/unitree_go2_dt_isaacsim_clean:/workspace:rw
```

The sync script uses hardcoded `/workspace` paths for camera and LiDAR JSON files:

```text
/workspace/go2_dt/ros2_ws/lidar_outputs/live_lidar_state.json
/workspace/go2_dt/camera_yolo/outputs/live_camera_state.json
```

Therefore, the following must exist inside the container:

```text
/workspace/go2_dt
/workspace/go2_assets
```

---

## 9. What not to commit

Do not commit local Isaac cache/config/log folders:

```text
isaac51/
cache/
logs/
Omniverse local config
```

Avoid committing temporary rescue/generated files unless they are intentionally part of the scenario:

```text
*_RESCUE_*
*_RESCUED_FLAT*
isaac_write_test.txt
```

For very large assets, prefer Git LFS or document how to obtain/regenerate them.

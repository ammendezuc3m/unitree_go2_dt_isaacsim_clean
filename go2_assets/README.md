# go2_assets

This folder contains USD scenes and visual assets used by Isaac Sim.

---

## 1. Main scene

```text
go2_assets/go2/prueba_demo2_new_scenario.usd
```

Inside Isaac Docker:

```text
/workspace/go2_assets/go2/prueba_demo2_new_scenario.usd
```

---

## 2. Expected prims

The sync script expects:

```text
/World/go2
/World/go2/map
/World/person_1
/World/person_02
/World/person_03
```

| Prim | Purpose |
|---|---|
| `/World/go2` | Main robot transform. |
| `/World/go2/map` | Articulation root. |
| `/World/person_1` | CSI/mmWave person visual. |
| `/World/person_02` | LiDAR/SLAM person visual. |
| `/World/person_03` | Camera person visual. |

---

## 3. Mounting rule

Mount the project root into Isaac as `/workspace`:

```bash
-v /home/nextnet/AlbertoDir:/workspace:rw
```

or:

```bash
-v /home/nextnet/unitree_go2_dt_isaacsim_clean:/workspace:rw
```

The sync script uses `/workspace` hardcoded paths for camera and LiDAR JSON files.

---

## 4. Do not commit

Do not commit:

```text
isaac51/
cache/
logs/
Omniverse local config
generated rescue USD files
large local-only assets
```

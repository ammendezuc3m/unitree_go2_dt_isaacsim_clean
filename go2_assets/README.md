# go2_assets

Main scene:

```text
go2_assets/go2/prueba_demo2_new_scenario.usd
```

Inside Isaac Docker:

```text
/workspace/go2_assets/go2/prueba_demo2_new_scenario.usd
```

Open it from Isaac Sim using **File → Open**.

Expected prims:

```text
/World/go2
/World/go2/map
/World/person_1
/World/person_02
/World/person_03
```

The sync script uses `/workspace` hardcoded paths for camera and LiDAR JSON files, so mount the project root as `/workspace`.

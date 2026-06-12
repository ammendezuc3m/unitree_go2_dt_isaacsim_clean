# JSON Contracts

The demo uses JSON files as a lightweight integration interface. There is no central API server.

Main consumers:

- `zone_loop_patrol_v3.py`: uses sensor JSONs for safety-stop decisions.
- `go2_dt_sync_demo2_new.py`: uses sensor JSONs for Isaac Sim visualization.

Important: freshness is based on **file modification time (`mtime`)**, not only on `timestamp`. A producer must rewrite the file periodically.

---

## 1. Bad/offline statuses

These statuses are treated as bad/offline by the patrol:

```text
fault
offline
stale
error
```

Recommended valid status:

```text
online
```

---

## 2. Camera JSON

Path:

```text
go2_dt/camera_yolo/outputs/live_camera_state.json
```

Minimum JSON accepted by patrol:

```json
{
  "status": "online",
  "person_detected": true
}
```

Fields used by patrol:

```text
status
person_detected
mtime of file
```

Recommended full example:

```json
{
  "status": "online",
  "input_source": "local_usb_camera_yolox_120p",
  "person_detected": false,
  "count": 0,
  "detections": [],
  "frame_width": 424,
  "frame_height": 240,
  "error": null,
  "timestamp": 1780584039.1061566
}
```

---

## 3. LiDAR/SLAM JSON

Path:

```text
go2_dt/ros2_ws/lidar_outputs/live_lidar_state.json
```

The patrol accepts, in order:

1. `person_detected`
2. `occupied`
3. `count > 0`

Minimum examples:

```json
{
  "status": "online",
  "person_detected": true
}
```

```json
{
  "status": "online",
  "occupied": true
}
```

```json
{
  "status": "online",
  "count": 1
}
```

---

## 4. CSI/mmWave JSON

Path:

```text
go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json
```

The patrol checks:

```text
prediction
raw_prediction
label
count
```

Minimum accepted examples:

```json
{
  "status": "online",
  "prediction": "person"
}
```

```json
{
  "status": "online",
  "raw_prediction": "person"
}
```

```json
{
  "status": "online",
  "label": "occupied"
}
```

```json
{
  "status": "online",
  "count": 1
}
```

Person labels:

```text
person
person_1
human
occupied
true
```

Empty labels:

```text
empty
nothing
none
clear
background
no_object
no_novel_object
false
```

---

## 5. Adding a new sensor without an API

Generic binary detector:

```json
{
  "status": "online",
  "person_detected": true,
  "count": 1,
  "timestamp": 1780000000.0,
  "source": "my_sensor"
}
```

Generic classifier:

```json
{
  "status": "online",
  "prediction": "person",
  "score": 0.83,
  "timestamp": 1780000000.0,
  "source": "my_classifier"
}
```

Requirements:

1. Rewrite the JSON periodically.
2. Keep the file fresh by modification time.
3. Use one of the accepted fields: `person_detected`, `occupied`, `count`, `prediction`, `raw_prediction`, or `label`.
4. If needed, edit the corresponding path parameter or script constant.

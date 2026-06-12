# JSON Contracts

There is no API server. Producers write JSON files and consumers read them.

Freshness is based on **file modification time**, so producers must rewrite files periodically.

## Camera

Path: `go2_dt/camera_yolo/outputs/live_camera_state.json`.

Minimum:

```json
{ "status": "online", "person_detected": true }
```

Patrol uses `status`, `person_detected` and file mtime.

## LiDAR/SLAM

Path: `go2_dt/ros2_ws/lidar_outputs/live_lidar_state.json`.

Accepted forms:

```json
{ "status": "online", "person_detected": true }
```

```json
{ "status": "online", "occupied": true }
```

```json
{ "status": "online", "count": 1 }
```

## CSI/mmWave

Path: `go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json`.

Accepted forms:

```json
{ "status": "online", "prediction": "person" }
```

```json
{ "status": "online", "raw_prediction": "person" }
```

```json
{ "status": "online", "label": "occupied" }
```

```json
{ "status": "online", "count": 1 }
```

Person labels: `person`, `person_1`, `human`, `occupied`, `true`.

Empty labels: `empty`, `nothing`, `none`, `clear`, `background`, `no_object`, `no_novel_object`, `false`.

## Adding a new sensor

Write a JSON periodically using one of the accepted fields:

```json
{
  "status": "online",
  "person_detected": true,
  "count": 1,
  "timestamp": 1780000000.0,
  "source": "my_sensor"
}
```

or:

```json
{
  "status": "online",
  "prediction": "person",
  "score": 0.83,
  "timestamp": 1780000000.0,
  "source": "my_classifier"
}
```

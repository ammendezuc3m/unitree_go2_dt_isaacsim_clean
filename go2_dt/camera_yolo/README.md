# Camera YOLO

This module writes:

```text
camera_yolo/outputs/live_camera_state.json
```

Minimum valid JSON:

```json
{
  "status": "online",
  "person_detected": true
}
```

Recommended full JSON:

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

Manual trigger:

```bash
mkdir -p /home/nextnet/AlbertoDir/go2_dt/camera_yolo/outputs

cat > /home/nextnet/AlbertoDir/go2_dt/camera_yolo/outputs/live_camera_state.json <<'EOF'
{
  "status": "online",
  "person_detected": true,
  "count": 1
}
EOF
```

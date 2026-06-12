# CSI/mmWave Predictor

Input stream:

```text
realtime_inputs/live_csi_stream.txt
```

Output JSON:

```text
analysis_outputs/live_prediction_state.json
```

Minimum valid JSON:

```json
{
  "status": "online",
  "prediction": "person"
}
```

Accepted person labels:

```text
person
person_1
human
occupied
true
```

Accepted empty labels:

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

OpenWrt live script:

```bash
/root/scripts_csi_dog/stream_csi_live_05s_single.sh 172.16.12.170 nextnet
```

Dataset capture:

```bash
/root/scripts_csi_dog/capture_countdown.sh person 120 100 1 10
```

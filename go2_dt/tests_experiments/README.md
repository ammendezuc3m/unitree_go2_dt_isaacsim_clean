# tests_experiments

Carpeta para scripts de prueba, experimentos rápidos y validaciones aisladas.

La demo principal NO debe depender de esta carpeta.

## Estructura

- `csi/`: pruebas de captura CSI, inferencia online, batches, frecuencia de muestreo, etc.
- `radio/`: pruebas de configuración AP/STA, asociación, ping, iperf o recuperación 60 GHz.
- `video/`: pruebas de vídeo RTP/GStreamer fuera de la demo principal.
- `yolo/`: pruebas aisladas de cámara USB/YOLO.
- `ros2/`: pruebas aisladas de ROS 2, SLAM, LiDAR o patrol.
- `tmp_outputs/`: salidas temporales generadas por pruebas.
- `archived/`: pruebas antiguas que se quieren conservar pero no usar.

## Regla

Si un script es necesario para la demo principal, debe estar fuera de esta carpeta.

Si un script es experimental o de diagnóstico, debe guardarse aquí.

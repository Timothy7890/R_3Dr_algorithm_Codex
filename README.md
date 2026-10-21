# 三维重建服务后端骨架
Service_3D_reconstruction

该仓库包含两个相互独立的 FastAPI 服务：

- `control_plane`：部署在电脑 A，创建任务、生成 UUID、选择中转节点并保存任务状态。
- `transfer_node`：部署在中转机，接收和校验视频、选择计算节点、转发任务并接收计算结果。

## 本地运行

安装依赖：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

开发时可先使用默认 SQLite 启动电脑 A：

```powershell
uvicorn control_plane.main:app --reload --port 8000
```

在另一个终端启动中转节点：

```powershell
$env:TRANSFER_NODE_ID="transfer-1"
$env:TRANSFER_PUBLIC_URL="http://localhost:8101"
$env:TRANSFER_CALLBACK_URL="http://localhost:8101"
$env:TRANSFER_CONTROL_PLANE_URL="http://localhost:8000"
uvicorn transfer_node.main:app --reload --port 8101
```

等待一次心跳后创建任务：

```powershell
$body = @{ filename = "input.mp4"; content_type = "video/mp4" } | ConvertTo-Json
$task = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/tasks -ContentType application/json -Body $body
$task
curl.exe -X POST -F "file=@input.mp4" $task.upload_url
Invoke-RestMethod http://localhost:8000/api/v1/tasks/$($task.task_id)
```

未配置计算节点时，上传完成的任务停留在 `QUEUED`。配置格式为逗号分隔的
`节点ID|地址|权重`：

```powershell
$env:TRANSFER_COMPUTE_NODES="Server-1|http://10.0.0.21:8200|1,Server-2|http://10.0.0.22:8200|2"
```

计算节点需要实现：

- `GET /health`，返回 `accepting_tasks`、`active_jobs`、`capacity`。
- `POST /internal/jobs/{task_id}`，接收 `video` 文件以及 `callback_url`、`progress_url` 表单字段；重复的 `task_id` 必须幂等。
- 处理期间向 `progress_url` 发送状态、百分比和阶段名称，并携带 `X-Internal-Key`。
- 完成后向回调地址上传结果文件，并携带 `X-Internal-Key`。

## 计算节点示例

仓库中的 `compute_node` 提供了与 `transfer_node` 对接的 FastAPI 示例服务。它实现：

- `GET /health`：返回 `accepting_tasks`、`active_jobs` 和 `capacity`。
- `POST /internal/jobs/{task_id}`：接收 `video`、`callback_url`、`progress_url`，保存视频并异步执行占位处理。
- 处理期间向 `progress_url` 发送 JSON 进度，完成后向 `callback_url` 以 multipart 方式上传占位结果。
- 使用 `COMPUTE_INTERNAL_API_KEY` 对内部接口鉴权，并对重复 `task_id` 幂等返回。

启动示例：

```powershell
$env:COMPUTE_NODE_ID="Server-1"
$env:COMPUTE_INTERNAL_API_KEY="local-internal-key"
$env:COMPUTE_STORAGE_ROOT="./data/compute"
$env:COMPUTE_CAPACITY="1"
python3 -m uvicorn compute_node.main:app --host 0.0.0.0 --port 8200
```

然后在 transfer node 上配置：

```powershell
$env:TRANSFER_COMPUTE_NODES="Server-1|http://127.0.0.1:8200|1"
```

实际三维重建逻辑位于 `compute_node/processor.py` 的 `run_reconstruction`，当前仅生成占位结果文件，可直接替换为 GPU 处理函数。

## 生产部署注意事项

- 必须替换 `UPLOAD_TOKEN_SECRET` 和 `INTERNAL_API_KEY`，并通过密钥管理系统注入。
- 服务间启用 TLS 或双向 TLS，不要把 `/internal` 接口暴露到公网。
- 结果文件建议迁移至 MinIO、S3 或 NAS；数据库只保存元数据和对象地址。
- 当前上传接口为流式单请求上传。超大文件生产环境建议增加 tus 或 S3 multipart 分块上传。
- 用户认证、任务所有权校验、病毒扫描、视频格式探测和结果下载鉴权需要在接入真实用户系统时补齐。

API 文档：电脑 A 的 `/docs`，中转节点的 `/docs`。

## 用户 Web 界面

控制节点会把 `webui/` 挂载到 `/ui`。启动控制节点和至少一个中转节点后，访问：

```text
http://localhost:13000/ui/
```

界面支持上传视频创建任务、复制 `task_id`、查询进度和根据任务返回的 `result_url` 下载结果。若端口或域名不同，可在页面右上角“接口设置”中修改 API 地址。中转节点的 `TRANSFER_CORS_ORIGINS` 需要包含 Web 页面所在的来源地址。

运行不依赖第三方测试框架的基础测试：

```powershell
python -m unittest discover -s tests -v
```



# 启动control_plane节点
```
python3 -m uvicorn control_plane.main:app --host 0.0.0.0 --port 13000 --reload --no-access-log
```


# 启动transfer_node节点
```
python3 -m uvicorn transfer_node.main:app --host 0.0.0.0 --port 13001 --reload --log-level info
```

# 启动compute_node节点
```
python3 -m uvicorn compute_node.main:app --host 0.0.0.0 --port 14000 --reload --log-level info
```

# 启动 web ui 用户界面
```
python3 -m http.server 8088 -d web_UI
or python -m.......
```


# 测试 指定端口 能否访问的命令   Windows PowerShell

```
Test-NetConnection 10.130.10.166 -Port 30952
```


# 发出 get 这类网络http请求的命令   Windows PowerShel, Linux
```
Invoke-RestMethod -Uri "http://10.76.135.220:13001/health"
Linux: curl "http://10.76.135.220:13001/health"
```


# 通过ssh隧道，配置两台机器的 http 网络端口 转发
# 本地转发 -L，用于网络服务在服务器上，映射为本地可访问
# 远程转发 -R，用于网络服务在本地，映射为服务器端可访问
# -L/-R forwarded_address : service_address
# -L/-R forwarded_ip:port : service_ip:port
```
ssh -N -p 30952 -L 127.0.0.1:14001:127.0.0.1:14000 dky_YX(alias in .ssh/config )
ssh -N -p 30952 -L 127.0.0.1:14001:127.0.0.1:14000 -R 127.0.0.1:13001:127.0.0.1:13001 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 dky_YX
ssh -N -p 30952 -L 127.0.0.1:14002:127.0.0.1:14000 -R 127.0.0.1:13001:127.0.0.1:13001 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 dky_YX2
```


## 实际算法入口与作物参数

本项目是独立部署的真实计算节点，`compute_node/processor.py` 会执行抽帧与 `psn_pipeline_cuda`；更新计算服务器时应更新本仓库，不能只更新 `transfer_service` 中的示例计算节点。

`POST /internal/jobs/{task_id}` 接收中转节点发送的 `plant_type` 和可选 `yolo_ini` 表单字段：

- `tomato`（旧版番茄，界面已隐藏，仍接受旧请求）→ `20260411-Tomato-006.ini`
- `eggplant`（茄子）→ `20261020-eggplant-class.ini`
- `pepper`（辣椒）→ `20260720-lajiao.ini`
- `rapeseed`（油菜）→ `20260526-rapeseed.ini`
- `ginkgo`（银杏）→ `20260323-yinxing.ini`
- `tomato_new`（番茄；原“番茄-新”）→ `20261020-Tomato-CEN-fewshot.ini`
- `other`（其他植物，仅重建不分割）→ `--nerf-stage2-steps 0 --rgb-only --sfm-aabb-scale 16 --sfm-camera-aabb --nerf-export-subject-crop`（背景一起训练，训练区域用 `--sfm-aabb-scale 16`，其余作物为 4：归一化后相机距离为 1，aabb_scale 4 只覆盖约 2 倍相机距离，背景较远的场景（如整个房间）放不进去，模型会在每台相机前堆半透明雾去拟合画面；`--sfm-camera-aabb` 由相机外参求出位于环绕相机内部的立方体，写入 `transforms.json` 的 `export_aabb`，导出点云时只保留框内的点，总点数不变；`--sfm-normalize-camera-scale` 写入 `scale`/`offset`，使相机到注视点的平均距离统一为 1 个 NGP 单位，避免 Metashape 坐标偏大时相机贴到训练区域边界、背景放不下而满屏起雾；`--nerf-export-subject-crop` 在同一次导出里，于 `export_aabb` 粗裁之后、降采样之前找出水平支撑面（桌面/板面/地面）并去掉它及以下的点，再只保留离转轴最近的一团（植物+花盆），找不到时退回 `export_aabb` 裁剪结果，总点数不变；需要 PlantsegNeRF 包含 `RUN_pp/camera_aabb.py` 与 `scripts/export/subject_crop.py`），`--ignore` 增加 `yolo`；`--ini` 仍传 `903_YOLO26.ini`，但不会被读取

所有作物都传 `--sfm-normalize-camera-scale`：在 `transforms.json` 写入 `scale`/`offset`，使相机到注视点的平均距离统一为 1 个 NGP 单位（Metashape 尺度任意，相机过远时植物在训练区域里过小，分割任务会失败；只改 `scale`/`offset`，不改各帧外参）。配合 `--nerf-export-save-world-frame`，导出点云按同一组 `scale`/`offset` 换回 `transforms.json` 的坐标系，可直接用其中的外参与内参投影回原图。

映射后的文件名传入 `psn_pipeline_cuda --ini`。算法按名字解析配置，所以不传绝对路径；这些 ini 必须已存在于计算服务器上算法使用的配置目录。

兼容旧 `plant` 字段和 `lajiao`、`yinxing` 取值。两个作物字段同时出现且指向不同配置，或 `yolo_ini` 与服务端映射不符时，返回 422。没有任何作物选择的旧请求仍使用 `903_YOLO26.ini`。

更新本项目并重启实际计算服务后，新参数才会生效。测试会模拟算法子进程，不运行真实 GPU 推理。

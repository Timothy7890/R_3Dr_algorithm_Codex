# Nerfstudio 视频转点云模块

这个目录是独立运行的三维重建算法适配器，不修改仓库其他服务。它通过 Nerfstudio 官方命令行完成：

1. `ns-process-data video`：从视频抽帧并估计相机姿态。
2. `ns-train splatfacto`：训练 Splatfacto 场景模型。
3. `ns-export pointcloud`：导出 PLY 点云。
4. 当输出路径以 `.pcd` 或 `.txt` 结尾时，用 Open3D 读取 PLY 并转换格式。

## 环境

建议使用 Nerfstudio 官方支持的 CUDA、PyTorch 和 Python 组合。请先按照 Nerfstudio 文档安装，再安装本目录的依赖：

```powershell
pip install -r reconstruction_3d_r_algorithm/requirements.txt
```

Nerfstudio 通常建议使用单独的虚拟环境，避免与本项目 FastAPI 服务的依赖互相影响。

## 使用

```powershell
python reconstruction_3d_r_algorithm/pipeline.py `
  --video .\reconstruction_3d_r_algorithm\input.mp4 `
  --output .\reconstruction_3d_r_algorithm\output\scene.ply `
  --max-iterations 30000 `
  --num-points 1000000
```

把 `--output` 改为 `scene.pcd` 或 `scene.txt` 即可选择其他格式。PCD 由 Open3D 写出；TXT 每行是 `x y z`，有颜色时为 `x y z r g b`，RGB 值范围是 0 到 1。中间目录仍保留 Nerfstudio 导出的原始 PLY。

默认会在输出文件旁生成 `<文件名>.nerfstudio` 中间目录，包含处理后的帧、训练结果和导出文件。使用 `--no-keep-intermediate` 可在成功导出后删除中间文件。

程序会在标准输出打印 JSON 进度事件，例如：

```json
{"stage":"training","progress":20,"message":"Training Splatfacto model"}
```

这类事件可以由上层计算节点转发到现有控制面。

## 路径和命令覆盖

如果 Nerfstudio 命令不在 `PATH`，可以通过环境变量指定：

```powershell
$env:NS_PROCESS_DATA="C:\\venvs\\nerfstudio\\Scripts\\ns-process-data.exe"
$env:NS_TRAIN="C:\\venvs\\nerfstudio\\Scripts\\ns-train.exe"
$env:NS_EXPORT="C:\\venvs\\nerfstudio\\Scripts\\ns-export.exe"
```

`pipeline.py` 对 Nerfstudio 只依赖公开 CLI，因此不同版本的参数变化需要按实际安装版本调整。GPU 显存、视频长度和迭代次数会显著影响运行时间。

## 接入现有计算节点

现有服务的 `compute_node/processor.py` 仍是占位实现。本目录不自动修改它。接入时可在计算任务中调用：

```python
from reconstruction_3d_r_algorithm.pipeline import ReconstructionConfig, reconstruct_video

reconstruct_video(ReconstructionConfig(video_path=input_path, output_path=output_path))
```

其中 `output_path` 可使用 `.ply`、`.pcd` 或 `.txt` 后缀，随后由计算节点把该文件上传给中转节点。

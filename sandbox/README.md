# Sandbox image

镜像标签：`data-reflex-sandbox:3.11`（与 `SANDBOX_IMAGE` 默认一致）。

## 构建

先启动 **Docker Desktop**，再执行：

```powershell
docker build -t data-reflex-sandbox:3.11 -f sandbox/Dockerfile sandbox
```

## 启用真实执行（API）

在 `.env` 中：

```env
SANDBOX_BACKEND=docker
SANDBOX_IMAGE=data-reflex-sandbox:3.11
```

然后重启 `uvicorn`。缺 daemon / 缺镜像时任务会返回沙箱不可用，**不会**在宿主机执行生成代码。

## 评测

```powershell
python eval/run_eval.py --mode real --max-cases 3
```

"""资源路由 — 软件安装包、参考资料等"""
import os
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from utils import (
    require_role, _safe_path, _get_teacher_key,
    _natural_sort_key, RESOURCE_BASE
)

router = APIRouter()


def _get_teacher_resource_dir(teacher_username: str) -> str:
    return os.path.join(RESOURCE_BASE, f"{_get_teacher_key(teacher_username)}_resource")


@router.get("/api/resources")
def list_resources(teacher: str = ""):
    """列出资源（所有老师 或 指定老师）"""
    if teacher:
        teacher_dirs = [_get_teacher_resource_dir(teacher)]
    else:
        teacher_dirs = []
        if os.path.exists(RESOURCE_BASE):
            for d in sorted(os.listdir(RESOURCE_BASE)):
                full = os.path.join(RESOURCE_BASE, d)
                if os.path.isdir(full) and d.endswith("_resource"):
                    teacher_dirs.append(full)

    items = []
    for tdir in teacher_dirs:
        teacher_name = os.path.basename(tdir).replace("_resource", "")
        if not os.path.exists(tdir):
            continue
        for fname in sorted(os.listdir(tdir), key=_natural_sort_key):
            fpath = os.path.join(tdir, fname)
            if fname.startswith(".") or not os.path.isfile(fpath):
                continue
            stat = os.stat(fpath)
            items.append({
                "name": fname,
                "size": stat.st_size,
                "teacher": teacher_name,
                "url": f"/resources/{teacher_name}_resource/{fname}",
            })
    return {"status": "success", "resources": items}


@router.post("/api/resources/upload")
async def upload_resource(request: Request, file: UploadFile = File(...)):
    """上传资源文件"""
    caller = require_role(request, ["admin", "teacher"])
    res_dir = _get_teacher_resource_dir(caller["username"])
    os.makedirs(res_dir, exist_ok=True)

    safe_name = file.filename.replace("\\", "/").split("/")[-1]
    if not safe_name or safe_name.startswith("."):
        raise HTTPException(status_code=400, detail="文件名无效")

    content = await file.read()
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，最大 200MB")

    dest = os.path.join(res_dir, safe_name)
    with open(dest, "wb") as f:
        f.write(content)

    teacher_name = _get_teacher_key(caller["username"])
    return {
        "status": "success",
        "message": f"资源 [{safe_name}] 已上传",
        "url": f"/resources/{teacher_name}_resource/{safe_name}",
    }


@router.delete("/api/resources/{teacher}/{filename}")
def delete_resource(teacher: str, filename: str, request: Request):
    """删除资源文件"""
    caller = require_role(request, ["admin", "teacher"])
    res_dir = os.path.join(RESOURCE_BASE, f"{teacher}_resource")
    fpath = _safe_path(res_dir, filename)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="文件不存在")
    os.remove(fpath)
    return {"status": "success", "message": f"资源 [{filename}] 已删除"}

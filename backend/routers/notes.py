"""笔记本 + 笔记路由"""
import os
import glob as _glob
import shutil
from fastapi import APIRouter, Depends, HTTPException, Request
from utils import get_db, get_current_user, _safe_path, _get_notes_base, NotebookForm, NoteSaveForm

router = APIRouter()


def _get_notes_base(username: str) -> str:
    from utils import _resolve_user_dir
    user_dir = _resolve_user_dir(username)
    if not user_dir:
        raise HTTPException(status_code=404, detail=f"用户 {username} 不存在")
    return os.path.join(user_dir, "notes")


# ─── 笔记本 ───

@router.get("/api/notebooks")
def list_notebooks(request: Request):
    caller = get_current_user(request)
    base = _get_notes_base(caller["username"])
    if not os.path.exists(base):
        return {"status": "success", "notebooks": []}
    items = []
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        if os.path.isdir(p) and not name.startswith("."):
            md_count = len(_glob.glob(os.path.join(p, "*.md")))
            items.append({"name": name, "note_count": md_count})
    return {"status": "success", "notebooks": items}


@router.post("/api/notebooks")
def create_notebook(request: Request, form: NotebookForm):
    caller = get_current_user(request)
    name = form.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="笔记本名称不能为空")
    base = _get_notes_base(caller["username"])
    nb_path = _safe_path(base, name)
    if os.path.exists(nb_path):
        raise HTTPException(status_code=400, detail="笔记本已存在")
    os.makedirs(nb_path, exist_ok=True)
    return {"status": "success", "message": f"笔记本 [{name}] 创建成功！", "name": name}


@router.delete("/api/notebooks/{name:path}")
def delete_notebook(request: Request, name: str):
    caller = get_current_user(request)
    base = _get_notes_base(caller["username"])
    nb_path = _safe_path(base, name)
    if not os.path.exists(nb_path):
        raise HTTPException(status_code=404, detail="笔记本不存在")
    shutil.rmtree(nb_path)
    return {"status": "success", "message": f"笔记本 [{name}] 已删除"}


@router.put("/api/notebooks/{name:path}")
def rename_notebook(request: Request, name: str, new_name: str = ""):
    caller = get_current_user(request)
    if not new_name or new_name == name:
        raise HTTPException(status_code=400, detail="新名称无效")
    base = _get_notes_base(caller["username"])
    old_path = _safe_path(base, name)
    new_path = _safe_path(base, new_name)
    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="笔记本不存在")
    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="同名笔记本已存在")
    os.rename(old_path, new_path)
    return {"status": "success", "message": f"笔记本 [{name}] → [{new_name}]"}


# ─── 笔记 ───

@router.get("/api/notes")
def list_notes(request: Request, notebook: str = ""):
    caller = get_current_user(request)
    base = os.path.join(_get_notes_base(caller["username"]), notebook)
    if not os.path.exists(base):
        return {"status": "success", "notes": []}
    files = sorted(_glob.glob(os.path.join(base, "*.md")), key=os.path.basename)
    notes = []
    for fp in files:
        fname = os.path.basename(fp)
        stat = os.stat(fp)
        notes.append({"filename": fname, "size": stat.st_size, "modified": stat.st_mtime})
    return {"status": "success", "notes": notes}


@router.get("/api/notes/{notebook}/{filename:path}")
def read_note(request: Request, notebook: str, filename: str):
    caller = get_current_user(request)
    base = _get_notes_base(caller["username"])
    fp = _safe_path(base, notebook, filename)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="笔记不存在")
    with open(fp, "r", encoding="utf-8") as f:
        return {"status": "success", "filename": filename, "notebook": notebook, "content": f.read()}


@router.post("/api/notes")
def save_note(request: Request, form: NoteSaveForm, notebook: str = ""):
    caller = get_current_user(request)
    if not notebook:
        raise HTTPException(status_code=400, detail="请指定笔记本")
    if not form.filename.endswith(".md"):
        form.filename += ".md"
    base = os.path.join(_get_notes_base(caller["username"]), notebook)
    os.makedirs(base, exist_ok=True)
    fp = _safe_path(base, form.filename)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(form.content)
    return {"status": "success", "message": f"笔记 [{form.filename}] 保存成功！"}


@router.put("/api/notes/{notebook}/{filename:path}")
def rename_note(request: Request, notebook: str, filename: str, new_name: str = ""):
    caller = get_current_user(request)
    if not new_name or new_name == filename:
        raise HTTPException(status_code=400, detail="新名称无效")
    if not new_name.endswith(".md"):
        new_name += ".md"
    base = _get_notes_base(caller["username"])
    old_fp = _safe_path(base, notebook, filename)
    new_fp = _safe_path(base, notebook, new_name)
    if not os.path.exists(old_fp):
        raise HTTPException(status_code=404, detail="笔记不存在")
    if os.path.exists(new_fp):
        raise HTTPException(status_code=400, detail="同名笔记已存在")
    os.rename(old_fp, new_fp)
    return {"status": "success", "message": f"笔记 [{filename}] → [{new_name}]"}


@router.delete("/api/notes/{notebook}/{filename:path}")
def delete_note(request: Request, notebook: str, filename: str):
    caller = get_current_user(request)
    base = _get_notes_base(caller["username"])
    fp = _safe_path(base, notebook, filename)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="笔记不存在")
    os.remove(fp)
    return {"status": "success", "message": f"笔记 [{filename}] 已删除"}

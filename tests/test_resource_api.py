r"""
资料/软件（resources）API 测试
=============================
覆盖：创建保存 tags/format、编辑（JSON body）、重名/空名校验、列表往返、
删除 → 回收站列表 → 恢复全流程、课程恢复（回移不嵌套）。

运行：
  cd C:\Arin - cabin\CodeNova-Workspace\qc-pocket-space\tests
  venv\Scripts\python.exe -m pytest test_resource_api.py -v
"""
import sys
import os
import json
import shutil

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient
from main import app
from utils import (
    generate_token, ACTIVE_TOKENS, TRASH_DIR,
    RESOURCE_BASE, _get_teacher_key, _get_teacher_course_dir,
)

from routers.auth import _reset_login_failures
_reset_login_failures()

client = TestClient(app)

PREFIX = "biz_restest_t"


def make_token(username: str, role: str) -> str:
    return generate_token(username, role)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def setup_function():
    ACTIVE_TOKENS.clear()
    _reset_login_failures()


# ============================================================
# 辅助：建号 / 建资源 / 清场
# ============================================================

def _register_teacher() -> str:
    admin = make_token("admin", "admin")
    resp = client.post("/api/register", headers=auth(admin),
                       json={"username": PREFIX, "password": "123456", "role": "teacher"})
    if resp.status_code == 400 and "已存在" in resp.text:
        client.delete(f"/api/users/{PREFIX}", headers=auth(make_token("admin", "admin")))
        resp = client.post("/api/register", headers=auth(make_token("admin", "admin")),
                           json={"username": PREFIX, "password": "123456", "role": "teacher"})
    assert resp.status_code == 200, resp.text
    return make_token(PREFIX, "teacher")


def _cleanup():
    """彻底清理测试老师产生的：文件回收站 / 资源目录 / 课程目录 / 账号"""
    if os.path.isdir(TRASH_DIR):
        for name in os.listdir(TRASH_DIR):
            if f"__{PREFIX}__" in name:
                shutil.rmtree(os.path.join(TRASH_DIR, name), ignore_errors=True)
    shutil.rmtree(os.path.join(RESOURCE_BASE, f"{_get_teacher_key(PREFIX)}_resource"), ignore_errors=True)
    shutil.rmtree(_get_teacher_course_dir(PREFIX), ignore_errors=True)
    resp = client.delete(f"/api/users/{PREFIX}", headers=auth(make_token("admin", "admin")))
    if resp.status_code == 200:
        trash_id = resp.json().get("trash", {}).get("id")
        if trash_id:
            client.delete(f"/api/trash/{trash_id}", headers=auth(make_token("admin", "admin")))


def _create_resource(token: str, name: str, **extra) -> str:
    body = {"teacher": PREFIX, "name": name, "category": "reference",
            "description": "", "tags": [], "format": "folder"}
    body.update(extra)
    resp = client.post("/api/resources", headers=auth(token), json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["resource"]["id"]


def _list_resource(token: str, rid: str) -> dict:
    resp = client.get(f"/api/resources?teacher={PREFIX}", headers=auth(token))
    assert resp.status_code == 200, resp.text
    for item in resp.json()["resources"]:
        if item["id"] == rid:
            return item
    raise AssertionError(f"列表中未找到资源 {rid}")


# ============================================================
# 📚 创建 + 编辑
# ============================================================

class Test资源CRUD:

    def test_创建保存标签与格式(self):
        token = _register_teacher()
        try:
            rid = _create_resource(token, "标签资料", tags=["高一", "数学"],
                                   format="ebook", description="描述A")
            item = _list_resource(token, rid)
            assert item["name"] == "标签资料"
            assert item["description"] == "描述A"
            assert item["tags"] == ["高一", "数学"]
            assert item["format"] == "ebook"
            assert item["category"] == "reference"
        finally:
            _cleanup()

    def test_编辑写入meta并往返(self):
        token = _register_teacher()
        try:
            rid = _create_resource(token, "原始名")
            resp = client.put(f"/api/resources/{rid}", headers=auth(token),
                              json={"name": "改后名", "description": "新描述",
                                    "tags": ["语文"], "format": "ebook"})
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "success"

            # 列表往返（编辑弹窗依赖这些字段）
            item = _list_resource(token, rid)
            assert item["name"] == "改后名"
            assert item["description"] == "新描述"
            assert item["tags"] == ["语文"]
            assert item["format"] == "ebook"

            # meta 真实落盘
            meta_path = os.path.join(RESOURCE_BASE, f"{_get_teacher_key(PREFIX)}_resource",
                                     rid, "resource.json")
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            assert meta["name"] == "改后名" and meta["tags"] == ["语文"]
        finally:
            _cleanup()

    def test_编辑重名与空名(self):
        token = _register_teacher()
        try:
            a = _create_resource(token, "重名A")
            _create_resource(token, "重名B")

            # 改成另一资源的名字 → 400
            resp = client.put(f"/api/resources/{a}", headers=auth(token),
                              json={"name": "重名B", "description": "", "tags": [], "format": ""})
            assert resp.status_code == 400

            # 名字不动（重名校验排除自身）→ 200
            resp = client.put(f"/api/resources/{a}", headers=auth(token),
                              json={"name": "重名A", "description": "", "tags": [], "format": ""})
            assert resp.status_code == 200

            # 空名 → 400
            resp = client.put(f"/api/resources/{a}", headers=auth(token),
                              json={"name": "   ", "description": "", "tags": [], "format": ""})
            assert resp.status_code == 400
        finally:
            _cleanup()


# ============================================================
# 🗑️ 回收站：删除 → 列表 → 恢复
# ============================================================

class Test资源回收站:

    def test_删除到回收站再到恢复(self):
        token = _register_teacher()
        try:
            rid = _create_resource(token, "回收资料", tags=["恢复测试"])

            # 删除 → 回收站出现 type=resource 且带可读 name
            resp = client.delete(f"/api/resources/{rid}", headers=auth(token))
            assert resp.status_code == 200, resp.text

            resp = client.get("/api/trash/items", headers=auth(token))
            assert resp.status_code == 200
            items = [i for i in resp.json()["items"]
                     if i.get("type") == "resource" and rid in i.get("_folder", "")]
            assert items, "资源删除后未出现在回收站"
            folder = items[0]["_folder"]
            assert items[0].get("name") == "回收资料", "回收站条目缺少可读名称"

            # 恢复 → 资源回列表、meta 完整、条目消失
            resp = client.post(f"/api/trash/items/{folder}/restore", headers=auth(token))
            assert resp.status_code == 200, resp.text

            item = _list_resource(token, rid)
            assert item["name"] == "回收资料"
            assert item["tags"] == ["恢复测试"]

            meta_path = os.path.join(RESOURCE_BASE, f"{_get_teacher_key(PREFIX)}_resource",
                                     rid, "resource.json")
            assert os.path.isfile(meta_path), "恢复后 resource.json 不在目录根（发生嵌套）"
            assert not os.path.exists(os.path.join(os.path.dirname(meta_path), ".trash.json"))

            resp = client.get("/api/trash/items", headers=auth(token))
            assert not any(i.get("_folder") == folder for i in resp.json()["items"]), "恢复后回收站条目未清除"
        finally:
            _cleanup()


# ============================================================
# 📦 课程回收站恢复（同一条恢复链路，防回归）
# ============================================================

class Test课程回收站恢复:

    def test_课程恢复不嵌套(self):
        token = _register_teacher()
        try:
            resp = client.post("/api/courses", headers=auth(token),
                               json={"teacher": PREFIX, "name": "回收站课程X", "course_type": "scratch"})
            assert resp.status_code == 200, resp.text
            cid = resp.json()["course"]["id"]

            resp = client.delete(f"/api/courses/{cid}", headers=auth(token))
            assert resp.status_code == 200, resp.text

            resp = client.get("/api/trash/items", headers=auth(token))
            items = [i for i in resp.json()["items"]
                     if i.get("type") == "course" and cid in i.get("_folder", "")]
            assert items, "课程删除后未出现在回收站"
            folder = items[0]["_folder"]

            resp = client.post(f"/api/trash/items/{folder}/restore", headers=auth(token))
            assert resp.status_code == 200, resp.text

            # course.json 必须落在 {cid}/ 根下，不能嵌套成 {cid}/{cid}/
            cdir = _get_teacher_course_dir(PREFIX)
            assert os.path.isfile(os.path.join(cdir, cid, "course.json")), "恢复后 course.json 不在目录根（发生嵌套）"
            assert not os.path.exists(os.path.join(cdir, cid, cid)), "恢复出现嵌套目录"
            assert not os.path.exists(os.path.join(cdir, cid, ".trash.json")), ".trash.json 未清除"
        finally:
            _cleanup()

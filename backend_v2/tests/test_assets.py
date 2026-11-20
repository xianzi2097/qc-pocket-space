"""验收单：资产域 + 文件树（规范 §3.4/§3.5 · D16/D17 · M2）

覆盖：
  · 建资产：库+盘双写、课程族 type 必填、重名 5001/6001（跨 bucket 全局唯一）、学生 403
  · 列表/详情：公开字段裁剪（无 publish），owner/admin 才见 publish
  · 改资料：字段更新、重名拦、bucket 跨族挪动（课→资料）
  · 删资产（软删四联动）：行删 + trash 快照(type=asset) + 磁盘移回收站 + file_order 保留
  · 文件树七动作（F1~F7）：列/下/传/建夹/改名/删/排序
      - F3 自动建中间层目录（D14③）、超限 413、非法路径 5010、写权限 403
      - F1 文件夹在前名称升序、无记录按默认
      - F2 浏览器开的格式 inline、其余 attachment
      - F5 改目录名 → file_order 前缀跟随（账随货走）、同名幂等
      - F6 树内物理删不进回收站、根目录不给删
      - F7 拖拽排序落 file_order、幂等、未知名 1002
  · 课件（族守卫：仅课程族，违反 404）：扫描 has_main/has_student/sb3_count、删课件
  · 发布（仅课程族）：入队即返、后台单线程、状态轮询、失败留 message
  · 打包下载（全族，D33/D34）：多文件实时 zip、单文件直发、课程族须 shareable=true
  · viz 可视化族（D35）：建/筛不需 type、重名 6001、lessons/publish 族守卫 404、
      .html 走 inline + CSP sandbox 响应头；非 viz 的 .html 仍 attachment（老行为不变）
"""
import io
import time
import zipfile

from app.db import SessionLocal
from app.models import Asset, FileOrder, Trash

PWD = "abcd1234"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _teacher(client, admin_token, username):
    resp = client.post(
        "/api/accounts",
        json={"username": username, "password": PWD, "role": "teacher"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["id"]


def _login_as(client, username):
    resp = client.post("/api/auth/login", json={"username": username, "password": PWD})
    assert resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["token"]


def _mk_asset(client, token, bucket="courses", name="ast_demo", type_=None):
    body = {"bucket": bucket, "name": name}
    if type_ is not None:
        body["type"] = type_
    resp = client.post("/api/assets", json=body, headers=_auth(token))
    assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text
    return resp.json()["data"]


def _upload(client, token, asset_id, path, content: bytes):
    return client.post(
        f"/api/assets/{asset_id}/files/upload",
        data={"path": path},
        files={"file": ("f.bin", content, "application/octet-stream")},
        headers=_auth(token),
    )


def _wait_publish(client, token, asset_id, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/assets/{asset_id}/publish-status", headers=_auth(token))
        state = resp.json()["data"]["state"]
        if state in ("done", "failed"):
            return state
        time.sleep(0.02)
    return "timeout"


# ---------------- 建资产 ----------------

def test_create_requires_type_for_course_family(client, login):
    token = login()["token"]
    resp = client.post("/api/assets", json={"bucket": "courses", "name": "ast_notype"},
                       headers=_auth(token))
    assert resp.status_code == 400 and resp.json()["code"] == 1002


def test_create_makes_row_and_disk(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, bucket="courses", name="ast_dual", type_="scratch")

    db = SessionLocal()
    try:
        from app import config
        from app.models import Asset
        row = db.get(Asset, data["id"])
        assert row is not None and row.bucket == "courses" and row.type == "scratch"
        assert (config.DATA_ROOT / "assets" / data["id"]).is_dir()
    finally:
        db.close()


def test_create_duplicate_name_5001(client, login):
    token = login()["token"]
    _mk_asset(client, token, name="ast_dup", type_="scratch")
    resp = client.post("/api/assets", json={"bucket": "books", "name": "ast_dup", "type": "book"},
                       headers=_auth(token))
    assert resp.status_code == 409 and resp.json()["code"] == 5001


def test_create_duplicate_across_family_6001(client, login):
    token = login()["token"]
    _mk_asset(client, token, bucket="courses", name="ast_cross", type_="scratch")
    resp = client.post("/api/assets", json={"bucket": "reference", "name": "ast_cross"},
                       headers=_auth(token))
    assert resp.status_code == 409 and resp.json()["code"] == 6001


def test_create_student_forbidden(client, login):
    admin = login()["token"]
    client.post("/api/accounts", json={"username": "ast_stu", "password": PWD, "role": "student"},
                headers=_auth(admin))
    stu_token = _login_as(client, "ast_stu")
    resp = client.post("/api/assets", json={"bucket": "reference", "name": "ast_by_stu"},
                       headers=_auth(stu_token))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_create_invalid_bucket(client, login):
    token = login()["token"]
    resp = client.post("/api/assets", json={"bucket": "movies", "name": "ast_bad"},
                       headers=_auth(token))
    assert resp.status_code == 400 and resp.json()["code"] == 1002


# ---------------- 列表 / 详情 ----------------

def test_list_bucket_filter(client, login):
    token = login()["token"]
    _mk_asset(client, token, bucket="courses", name="ast_f_course", type_="scratch")
    _mk_asset(client, token, bucket="reference", name="ast_f_ref")

    resp = client.get("/api/assets", params={"bucket": "courses"})
    items = resp.json()["data"]["items"]
    assert items and all(i["bucket"] == "courses" for i in items)

    resp_all = client.get("/api/assets")
    assert resp_all.json()["data"]["total"] >= len(items)


def test_detail_public_trim(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_trim", type_="scratch")
    resp = client.get(f"/api/assets/{data['id']}")          # 不带 token
    body = resp.json()["data"]
    # publish.state 公开（前端据此开电子书 iframe），message（可能带服务器路径）不外发
    assert body["publish"] == {"state": "none"}
    allowed = {"id", "owner_id", "owner_name", "bucket", "name",
               "description", "tags", "type", "format", "shareable", "created_at", "publish"}
    assert set(body.keys()) <= allowed
    assert body["owner_name"] == "admin"


def test_list_publish_state_public_message_trimmed(client, login, monkeypatch):
    """列表公开面：只见 state 词；owner 自己的列表才带失败 message（渲染排障用）"""
    import app.publish as publish
    monkeypatch.setattr(publish, "_run_build",
                        lambda asset_id: (_ for _ in ()).throw(RuntimeError("模板缺失: C:\\x")))
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_pstate", type_="scratch")
    client.post(f"/api/assets/{data['id']}/publish", headers=_auth(token))
    assert _wait_publish(client, token, data["id"]) == "failed"

    pub = client.get("/api/assets", params={"bucket": "courses"}).json()["data"]["items"]
    row = next(i for i in pub if i["id"] == data["id"])
    assert row["publish"]["state"] == "failed"
    assert "message" not in row["publish"]                  # 公开不带 message

    own = client.get("/api/assets", params={"bucket": "courses"},
                     headers=_auth(token)).json()["data"]["items"]
    mine = next(i for i in own if i["id"] == data["id"])
    assert "模板缺失" in mine["publish"]["message"]          # owner 可见排障信息


def test_detail_owner_sees_publish(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_pub", type_="scratch")
    resp = client.get(f"/api/assets/{data['id']}", headers=_auth(token))
    assert "publish" in resp.json()["data"]


def test_detail_missing_404(client, login):
    resp = client.get("/api/assets/zzzzzzzzzzzz")
    assert resp.status_code == 404 and resp.json()["code"] == 1004


# ---------------- 改资料 / 挪动 ----------------

def test_patch_updates_fields(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_patch", type_="scratch")
    resp = client.patch(f"/api/assets/{data['id']}",
                        json={"description": "新描述", "tags": ["python"]},
                        headers=_auth(token))
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["description"] == "新描述"
    assert resp.json()["data"]["tags"] == ["python"]


def test_patch_rename_conflict_5001(client, login):
    token = login()["token"]
    _mk_asset(client, token, name="ast_a", type_="scratch")
    other = _mk_asset(client, token, name="ast_b", type_="scratch")
    resp = client.patch(f"/api/assets/{other['id']}", json={"name": "ast_a"},
                        headers=_auth(token))
    assert resp.status_code == 409 and resp.json()["code"] == 5001


def test_patch_bucket_cross_family(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, bucket="courses", name="ast_move", type_="scratch")
    resp = client.patch(f"/api/assets/{data['id']}", json={"bucket": "reference"},
                        headers=_auth(token))
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["bucket"] == "reference"
    # 挪后按资料族筛得到它
    items = client.get("/api/assets", params={"bucket": "reference"}).json()["data"]["items"]
    assert any(i["id"] == data["id"] for i in items)


def test_patch_other_teacher_403(client, login):
    admin = login()["token"]
    _teacher(client, admin, "ast_t1")
    _teacher(client, admin, "ast_t2")
    t1 = _login_as(client, "ast_t1")
    t2 = _login_as(client, "ast_t2")
    data = _mk_asset(client, t1, name="ast_own", type_="scratch")
    resp = client.patch(f"/api/assets/{data['id']}", json={"name": "ast_hijack"},
                        headers=_auth(t2))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


# ---------------- 删资产（软删四联动） ----------------

def test_delete_soft_four_effects(client, login):
    from app import config
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_del", type_="scratch")
    _upload(client, token, data["id"], "keep.txt", b"keep")
    # 造一条排序账
    client.post(f"/api/assets/{data['id']}/files/order",
                json={"path": "", "names": ["keep.txt"], "position": 0},
                headers=_auth(token))

    resp = client.delete(f"/api/assets/{data['id']}", headers=_auth(token))
    assert resp.json()["code"] == 0

    db = SessionLocal()
    try:
        from app.models import Asset
        assert db.get(Asset, data["id"]) is None                       # ① 行删
        trash_row = db.query(Trash).filter(Trash.name == "ast_del").first()
        assert trash_row is not None and trash_row.type == "asset"     # ② 快照
        assert not (config.DATA_ROOT / "assets" / data["id"]).exists()  # ③ 磁盘移走
        assert (config.DATA_ROOT / "trash" / data["id"]).is_dir()
        kept = db.query(FileOrder).filter(FileOrder.asset_id == data["id"]).count()
        assert kept == 1                                               # ④ 账本保留
    finally:
        db.close()


def test_delete_twice_404(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_del2", type_="scratch")
    assert client.delete(f"/api/assets/{data['id']}", headers=_auth(token)).json()["code"] == 0
    resp = client.delete(f"/api/assets/{data['id']}", headers=_auth(token))
    assert resp.status_code == 404 and resp.json()["code"] == 1004


def test_delete_soft_cleans_publish_artifact(client, login, monkeypatch, tmp_path):
    """软删四联动第⑤项：发布产物清理（§3.4）——已删资产的产物必须同灭，
    否则删除的电子书在 /course/{id}/ 公网仍可访问（数据安全事故）"""
    import app.publish as publish
    monkeypatch.setattr(publish, "SITES_DIR", tmp_path / "sites")

    token = login()["token"]
    data = _mk_asset(client, token, name="ast_del_pub", type_="scratch")
    art = tmp_path / "sites" / data["id"]
    art.mkdir(parents=True)
    (art / "index.html").write_text("<html>published</html>", encoding="utf-8")

    resp = client.delete(f"/api/assets/{data['id']}", headers=_auth(token))
    assert resp.json()["code"] == 0
    assert not art.exists(), "软删必须清理发布产物（规范 §3.4 四联动）"


def test_build_aborts_if_asset_deleted_midway(client, login, monkeypatch, tmp_path):
    """竞态守卫：构建期间（最长 910s）资产被删 → 写回产物前必须复核资产还在，
    否则删除的电子书在构建完成后又"复活"成公网孤儿"""
    from types import SimpleNamespace

    import app.publish as publish

    tpl = tmp_path / "tpl"
    (tpl / "docs" / ".vitepress").mkdir(parents=True)
    monkeypatch.setattr(publish, "TEMPLATE_DIR", tpl)
    monkeypatch.setattr(publish, "SITES_DIR", tmp_path / "sites")

    token = login()["token"]
    data = _mk_asset(client, token, name="ast_race", type_="scratch")
    _upload(client, token, data["id"], "index.md", b"# race")

    def _fake_run(cmd, **kwargs):
        # 模拟 vitepress build 期间用户删除了资产
        db = SessionLocal()
        try:
            db.delete(db.get(Asset, data["id"]))
            db.commit()
        finally:
            db.close()
        out = tpl / "docs" / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html>built</html>", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(publish, "subprocess", SimpleNamespace(run=_fake_run))
    publish._run_build(data["id"])          # 不抛异常，但绝不能写出产物

    assert not (tmp_path / "sites" / data["id"]).exists(), \
        "构建期间被删的资产不得写出产物（孤儿 = 公网可访问已删内容）"


# ---------------- F3 上传 ----------------

def test_upload_writes_disk(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_up", type_="scratch")
    resp = _upload(client, token, data["id"], "hello.txt", b"hi v2")
    assert resp.json()["code"] == 0

    from app import config
    got = (config.DATA_ROOT / "assets" / data["id"] / "hello.txt").read_bytes()
    assert got == b"hi v2"                                             # 账货一致：盘上真有


def test_upload_auto_creates_parents(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_auto", type_="scratch")
    resp = _upload(client, token, data["id"], "a/b/c.txt", b"deep")
    assert resp.json()["code"] == 0                                    # D14③ 不先建夹也收

    from app import config
    assert (config.DATA_ROOT / "assets" / data["id"] / "a" / "b" / "c.txt").is_file()


def test_upload_too_large_413(client, login, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "UPLOAD_MAX_BYTES", 10)
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_big", type_="scratch")
    resp = _upload(client, token, data["id"], "big.bin", b"x" * 20)
    assert resp.status_code == 413 and resp.json()["code"] == 1113


def test_upload_write_permission(client, login):
    admin = login()["token"]
    _teacher(client, admin, "ast_w1")
    _teacher(client, admin, "ast_w2")
    t1 = _login_as(client, "ast_w1")
    t2 = _login_as(client, "ast_w2")
    data = _mk_asset(client, t1, name="ast_guard", type_="scratch")
    resp = _upload(client, t2, data["id"], "x.txt", b"x")
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_upload_invalid_path_5010(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_evil", type_="scratch")
    resp = _upload(client, token, data["id"], "../escape.txt", b"x")
    assert resp.status_code == 400 and resp.json()["code"] == 5010


# ---------------- F1 列目录 ----------------

def test_list_root_empty(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_empty", type_="scratch")
    resp = client.get(f"/api/assets/{data['id']}/files")
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["entries"] == []


def test_list_dirs_before_files_by_name(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_sort", type_="scratch")
    for p in ["z.txt", "a.txt"]:
        _upload(client, token, data["id"], p, b"1")
    for d in ["zdir", "adir"]:
        client.post(f"/api/assets/{data['id']}/files/mkdir",
                    json={"path": d}, headers=_auth(token))

    entries = client.get(f"/api/assets/{data['id']}/files").json()["data"]["entries"]
    assert [e["name"] for e in entries] == ["adir", "zdir", "a.txt", "z.txt"]
    assert entries[0]["type"] == "dir" and entries[2]["type"] == "file"
    assert entries[2]["size"] == 1


def test_list_missing_asset_404(client, login):
    resp = client.get("/api/assets/zzzzzzzzzzzz/files")
    assert resp.status_code == 404 and resp.json()["code"] == 1004


def test_list_invalid_path_5010(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_lp", type_="scratch")
    resp = client.get(f"/api/assets/{data['id']}/files", params={"path": "../x"})
    assert resp.status_code == 400 and resp.json()["code"] == 5010


# ---------------- F2 下载 ----------------

def test_download_file_bytes(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_dl", type_="scratch")
    _upload(client, token, data["id"], "doc.txt", b"content-v2")
    resp = client.get(f"/api/assets/{data['id']}/files/download",
                      params={"path": "doc.txt"})
    assert resp.status_code == 200 and resp.content == b"content-v2"


def test_download_inline_for_browser_types(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_inline", type_="scratch")
    _upload(client, token, data["id"], "readme.txt", b"x")
    resp = client.get(f"/api/assets/{data['id']}/files/download",
                      params={"path": "readme.txt"})
    assert "inline" in resp.headers.get("content-disposition", "")


def test_download_attachment_for_others(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_att", type_="scratch")
    _upload(client, token, data["id"], "pkg.zip", b"x")
    resp = client.get(f"/api/assets/{data['id']}/files/download",
                      params={"path": "pkg.zip"})
    assert "attachment" in resp.headers.get("content-disposition", "")


def test_download_missing_404(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_dl404", type_="scratch")
    resp = client.get(f"/api/assets/{data['id']}/files/download",
                      params={"path": "nope.txt"})
    assert resp.status_code == 404 and resp.json()["code"] == 1004


# ---------------- viz 可视化族（D35） ----------------

def test_viz_create_without_type(client, login):
    """viz 非课程族：不填 type 直接建，bucket 落库 viz"""
    token = login()["token"]
    data = _mk_asset(client, token, bucket="viz", name="viz_no_type")
    assert data["bucket"] == "viz"


def test_viz_list_filter(client, login):
    token = login()["token"]
    _mk_asset(client, token, bucket="viz", name="viz_f1")
    _mk_asset(client, token, bucket="reference", name="viz_f2")
    items = client.get("/api/assets", params={"bucket": "viz"}).json()["data"]["items"]
    assert items and all(i["bucket"] == "viz" for i in items)


def test_viz_duplicate_6001(client, login):
    """重名按 owner 跨 bucket 全局：viz 与资料同名 → 6001"""
    token = login()["token"]
    _mk_asset(client, token, bucket="viz", name="viz_dup")
    resp = client.post("/api/assets", json={"bucket": "reference", "name": "viz_dup"},
                       headers=_auth(token))
    assert resp.status_code == 409 and resp.json()["code"] == 6001


def test_viz_family_guard_404(client, login):
    """viz ∉ 课程族：lessons/publish 族守卫自动拦"""
    token = login()["token"]
    data = _mk_asset(client, token, bucket="viz", name="viz_guard")
    resp = client.get(f"/api/assets/{data['id']}/lessons", headers=_auth(token))
    assert resp.status_code == 404 and resp.json()["code"] == 1004
    resp = client.post(f"/api/assets/{data['id']}/publish", headers=_auth(token))
    assert resp.status_code == 404 and resp.json()["code"] == 1004


def test_viz_html_inline_with_csp_sandbox(client, login):
    """viz 的 .html：inline 渲染 + CSP sandbox allow-scripts（演示页可跑脚本但摸不到登录态）"""
    token = login()["token"]
    data = _mk_asset(client, token, bucket="viz", name="viz_html")
    _upload(client, token, data["id"], "index.html", b"<h1>viz</h1>")
    resp = client.get(f"/api/assets/{data['id']}/files/download",
                      params={"path": "index.html"})
    assert resp.status_code == 200
    assert "inline" in resp.headers.get("content-disposition", "")
    csp = resp.headers.get("content-security-policy", "")
    assert "sandbox" in csp and "allow-scripts" in csp


def test_non_viz_html_still_attachment(client, login):
    """资料族 .html 维持 attachment（不因 D35 改变老行为）"""
    token = login()["token"]
    data = _mk_asset(client, token, bucket="reference", name="ref_html")
    _upload(client, token, data["id"], "page.html", b"<h1>x</h1>")
    resp = client.get(f"/api/assets/{data['id']}/files/download",
                      params={"path": "page.html"})
    assert "attachment" in resp.headers.get("content-disposition", "")


# ---------------- F4 建文件夹 ----------------

def test_mkdir_creates_and_nested(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_mkdir", type_="scratch")
    resp = client.post(f"/api/assets/{data['id']}/files/mkdir",
                       json={"path": "x/y"}, headers=_auth(token))
    assert resp.json()["code"] == 0

    from app import config
    assert (config.DATA_ROOT / "assets" / data["id"] / "x" / "y").is_dir()


def test_mkdir_conflict_5011(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_mkc", type_="scratch")
    client.post(f"/api/assets/{data['id']}/files/mkdir",
                json={"path": "same"}, headers=_auth(token))
    resp = client.post(f"/api/assets/{data['id']}/files/mkdir",
                       json={"path": "same"}, headers=_auth(token))
    assert resp.status_code == 409 and resp.json()["code"] == 5011


# ---------------- F5 重命名 ----------------

def test_rename_file_and_self_idempotent(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_rn", type_="scratch")
    _upload(client, token, data["id"], "old.txt", b"x")

    resp = client.patch(f"/api/assets/{data['id']}/files/rename",
                        json={"path": "old.txt", "new_name": "new.txt"},
                        headers=_auth(token))
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["path"] == "new.txt"

    again = client.patch(f"/api/assets/{data['id']}/files/rename",
                         json={"path": "new.txt", "new_name": "new.txt"},
                         headers=_auth(token))
    assert again.json()["code"] == 0                                   # 同名幂等


def test_rename_dir_updates_order_prefix(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_rnp", type_="scratch")
    _upload(client, token, data["id"], "第3课/sb.txt", b"x")
    client.post(f"/api/assets/{data['id']}/files/order",
                json={"path": "第3课", "names": ["sb.txt"], "position": 0},
                headers=_auth(token))

    resp = client.patch(f"/api/assets/{data['id']}/files/rename",
                        json={"path": "第3课", "new_name": "三课"},
                        headers=_auth(token))
    assert resp.json()["code"] == 0

    db = SessionLocal()
    try:
        rows = db.query(FileOrder).filter(FileOrder.asset_id == data["id"]).all()
        # 目录自身的排序行（父层 name 改了）+ 内部文件行（前缀改了）
        assert rows, "排序账不该消失"
        for row in rows:
            assert "第3课" not in row.parent_path
    finally:
        db.close()


def test_rename_conflict_and_bad_name(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_rnc", type_="scratch")
    _upload(client, token, data["id"], "a.txt", b"x")
    _upload(client, token, data["id"], "b.txt", b"x")

    conflict = client.patch(f"/api/assets/{data['id']}/files/rename",
                            json={"path": "a.txt", "new_name": "b.txt"},
                            headers=_auth(token))
    assert conflict.status_code == 409 and conflict.json()["code"] == 5011

    bad = client.patch(f"/api/assets/{data['id']}/files/rename",
                       json={"path": "a.txt", "new_name": ".."},
                       headers=_auth(token))
    assert bad.status_code == 400 and bad.json()["code"] == 5010


# ---------------- F6 删文件 ----------------

def test_delete_file_and_dir_removes_order_rows(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_rm", type_="scratch")
    _upload(client, token, data["id"], "sub/f.txt", b"x")
    client.post(f"/api/assets/{data['id']}/files/order",
                json={"path": "sub", "names": ["f.txt"], "position": 0},
                headers=_auth(token))

    resp = client.delete(f"/api/assets/{data['id']}/files", params={"path": "sub"},
                         headers=_auth(token))
    assert resp.json()["code"] == 0

    from app import config
    assert not (config.DATA_ROOT / "assets" / data["id"] / "sub").exists()  # 盘上真没
    db = SessionLocal()
    try:
        assert db.query(FileOrder).filter(FileOrder.asset_id == data["id"]).count() == 0
    finally:
        db.close()


def test_delete_missing_and_root_rejected(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_rmx", type_="scratch")
    missing = client.delete(f"/api/assets/{data['id']}/files", params={"path": "nope"},
                            headers=_auth(token))
    assert missing.status_code == 404 and missing.json()["code"] == 1004

    root = client.delete(f"/api/assets/{data['id']}/files", params={"path": ""},
                         headers=_auth(token))
    assert root.status_code == 400 and root.json()["code"] == 1002      # 整资产用 DELETE 资产


# ---------------- F7 排序 ----------------

def test_order_applies_and_idempotent(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_ord", type_="scratch")
    for p in ["c.txt", "a.txt", "b.txt"]:
        _upload(client, token, data["id"], p, b"x")

    body = {"path": "", "names": ["b.txt", "a.txt", "c.txt"], "position": 0}
    assert client.post(f"/api/assets/{data['id']}/files/order",
                       json=body, headers=_auth(token)).json()["code"] == 0
    names = [e["name"] for e in
             client.get(f"/api/assets/{data['id']}/files").json()["data"]["entries"]]
    assert names == ["b.txt", "a.txt", "c.txt"]

    # 幂等：同请求再发一次，顺序不变
    client.post(f"/api/assets/{data['id']}/files/order", json=body, headers=_auth(token))
    names2 = [e["name"] for e in
              client.get(f"/api/assets/{data['id']}/files").json()["data"]["entries"]]
    assert names2 == names


def test_order_unknown_name_1002(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_orx", type_="scratch")
    _upload(client, token, data["id"], "a.txt", b"x")
    resp = client.post(f"/api/assets/{data['id']}/files/order",
                       json={"path": "", "names": ["ghost.txt"], "position": 0},
                       headers=_auth(token))
    assert resp.status_code == 400 and resp.json()["code"] == 1002


# ---------------- 课件（课程族） ----------------

def test_lessons_scan(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_les", type_="scratch")
    _upload(client, token, data["id"], "第1课/main.sb3", b"x")
    _upload(client, token, data["id"], "第1课/student.sb3", b"x")
    _upload(client, token, data["id"], "第1课/extra.sb3", b"x")
    client.post(f"/api/assets/{data['id']}/files/mkdir",
                json={"path": "第2课"}, headers=_auth(token))

    items = client.get(f"/api/assets/{data['id']}/lessons").json()["data"]["items"]
    by_name = {i["name"]: i for i in items}
    assert by_name["第1课"]["has_main"] is True
    assert by_name["第1课"]["has_student"] is True
    assert by_name["第1课"]["sb3_count"] == 3
    assert by_name["第2课"]["has_main"] is False and by_name["第2课"]["sb3_count"] == 0


def test_lessons_guard_resource_404(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, bucket="reference", name="ast_lesg")
    resp = client.get(f"/api/assets/{data['id']}/lessons")
    assert resp.status_code == 404 and resp.json()["code"] == 1004


def test_delete_lesson_and_missing(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_ldel", type_="scratch")
    _upload(client, token, data["id"], "第1课/main.sb3", b"x")

    resp = client.delete(f"/api/assets/{data['id']}/lessons/第1课", headers=_auth(token))
    assert resp.json()["code"] == 0

    from app import config
    assert not (config.DATA_ROOT / "assets" / data["id"] / "第1课").exists()

    missing = client.delete(f"/api/assets/{data['id']}/lessons/无此课", headers=_auth(token))
    assert missing.status_code == 404 and missing.json()["code"] == 1004


def test_delete_lesson_write_permission(client, login):
    admin = login()["token"]
    _teacher(client, admin, "ast_l1")
    _teacher(client, admin, "ast_l2")
    t1 = _login_as(client, "ast_l1")
    t2 = _login_as(client, "ast_l2")
    data = _mk_asset(client, t1, name="ast_lguard", type_="scratch")
    _upload(client, t1, data["id"], "课A/main.sb3", b"x")
    resp = client.delete(f"/api/assets/{data['id']}/lessons/课A", headers=_auth(t2))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


# ---------------- 发布（课程族 · 后台队列） ----------------

def test_publish_queue_and_done(client, login, monkeypatch):
    import app.publish as publish

    monkeypatch.setattr(publish, "_run_build", lambda asset_id: None)
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_pubq", type_="scratch")

    resp = client.post(f"/api/assets/{data['id']}/publish", headers=_auth(token))
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["state"] in ("queued", "building", "done")

    assert _wait_publish(client, token, data["id"]) == "done"


def test_publish_failure_keeps_message(client, login, monkeypatch):
    import app.publish as publish

    def _boom(asset_id):
        raise RuntimeError("vitepress 炸了")

    monkeypatch.setattr(publish, "_run_build", _boom)
    token = login()["token"]
    data = _mk_asset(client, token, name="ast_pubf", type_="scratch")

    client.post(f"/api/assets/{data['id']}/publish", headers=_auth(token))
    assert _wait_publish(client, token, data["id"]) == "failed"
    status = client.get(f"/api/assets/{data['id']}/publish-status",
                        headers=_auth(token)).json()["data"]
    assert "vitepress" in (status.get("message") or "")


def test_publish_guard_and_permission(client, login, monkeypatch):
    import app.publish as publish
    monkeypatch.setattr(publish, "_run_build", lambda asset_id: None)

    admin = login()["token"]
    _teacher(client, admin, "ast_p1")
    _teacher(client, admin, "ast_p2")
    t1 = _login_as(client, "ast_p1")
    t2 = _login_as(client, "ast_p2")

    # 资料族 → 404 族守卫
    ref = _mk_asset(client, t1, bucket="reference", name="ast_pubg")
    guard = client.post(f"/api/assets/{ref['id']}/publish", headers=_auth(t1))
    assert guard.status_code == 404 and guard.json()["code"] == 1004

    # 非 owner → 403
    course = _mk_asset(client, t1, name="ast_pubp", type_="scratch")
    forbidden = client.post(f"/api/assets/{course['id']}/publish", headers=_auth(t2))
    assert forbidden.status_code == 403 and forbidden.json()["code"] == 2005

    # status 同样保护
    st = client.get(f"/api/assets/{course['id']}/publish-status", headers=_auth(t2))
    assert st.status_code == 403


# ---------------- 打包下载（资料族） ----------------

def test_download_zip_multi_files(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, bucket="reference", name="ast_zip")
    _upload(client, token, data["id"], "a.txt", b"AAA")
    _upload(client, token, data["id"], "sub/b.txt", b"BBB")

    resp = client.get(f"/api/assets/{data['id']}/download")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "a.txt" in names and "sub/b.txt" in names
    assert zf.read("a.txt") == b"AAA"


def test_download_single_file_direct(client, login):
    token = login()["token"]
    data = _mk_asset(client, token, bucket="download", name="ast_single")
    _upload(client, token, data["id"], "tool.zip", b"PK-single")

    resp = client.get(f"/api/assets/{data['id']}/download")
    assert resp.status_code == 200 and resp.content == b"PK-single"    # 单文件直发不套壳


def test_download_course_family_zip(client, login):
    """D33/D34：课程族打包下载须 shareable=true，默认关 → 403；PATCH 打开 → 整资产实时 zip。"""
    token = login()["token"]
    data = _mk_asset(client, token, bucket="courses", name="ast_share", type_="scratch")
    _upload(client, token, data["id"], "01.开篇.md", b"# hello")
    _upload(client, token, data["id"], "img/logo.png", b"\x89PNG-fake")

    # 默认关闭 → 403
    resp = client.get(f"/api/assets/{data['id']}/download")
    assert resp.status_code == 403 and resp.json()["code"] == 2005

    # 老师打开开关 → 200，zip 含全部源文件
    patch = client.patch(f"/api/assets/{data['id']}",
                         json={"shareable": True},
                         headers={"Authorization": f"Bearer {token}"})
    assert patch.status_code == 200 and patch.json()["data"]["shareable"] is True

    resp = client.get(f"/api/assets/{data['id']}/download")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "01.开篇.md" in names and "img/logo.png" in names
    assert zf.read("01.开篇.md") == b"# hello"

    # 开关公开可见（游客列表也能看到，前端据此显隐按钮）
    pub = client.get("/api/assets").json()["data"]["items"]
    assert any(a["id"] == data["id"] and a["shareable"] is True for a in pub)

    # 资料族不受开关限制（原行为不变，恒可下载）
    ref = _mk_asset(client, token, bucket="reference", name="ast_ref_dl")
    _upload(client, token, ref["id"], "notes.txt", b"NN")
    assert client.get(f"/api/assets/{ref['id']}/download").status_code == 200

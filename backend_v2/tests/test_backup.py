"""验收单：每日自动备份（M4 ② · app/backup.py）

覆盖：
  · do_backup 产出 zip：含数据库一致快照 + 磁盘文件（账货都在包里）
  · db 及其 WAL/SHM 不重复入包（快照已覆盖）
  · 保留策略：只清自己的前缀（v2_auto_），保留 keep 份
  · 备份落在 DATA_ROOT 同级 backups/（测试环境天然隔离到临时目录）
"""
import zipfile

from app import backup, config


def test_backup_zip_contains_db_and_files(client, login):
    # 造一点真实数据（一个资产 + 一个文件）
    token = login()["token"]
    asset = client.post("/api/assets",
                        json={"bucket": "reference", "name": "备份用"},
                        headers={"Authorization": f"Bearer {token}"}).json()["data"]
    client.post(f"/api/assets/{asset['id']}/files/upload", data={"path": "keep.txt"},
                files={"file": ("keep.txt", b"in-data", "application/octet-stream")},
                headers={"Authorization": f"Bearer {token}"})

    name = backup.do_backup()
    path = backup.BACKUP_ROOT / name
    assert path.is_file()

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert f"data_v2/{config.DB_PATH.name}" in names         # 数据库一致快照
        assert not any(n.endswith((".db-wal", ".db-shm")) for n in names)  # 不重复
        assert any(n.endswith("keep.txt") for n in names)        # 磁盘文件也在
        # 快照是完整的库（能读出表）
        import io, sqlite3, tempfile, os
        snap = zf.read(f"data_v2/{config.DB_PATH.name}")
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(snap)
            conn = sqlite3.connect(tmp)
            try:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                assert "assets" in tables and "trash" in tables
            finally:
                conn.close()
        finally:
            os.unlink(tmp)


def test_cleanup_keeps_only_own_prefix(client, login, monkeypatch, tmp_path):
    monkeypatch.setattr(backup, "BACKUP_ROOT", tmp_path)
    # 造 4 份自己的 + 2 份 V1 的
    for i in range(4):
        (tmp_path / f"v2_auto_2026010{i}_000000.zip").write_bytes(b"x")
    (tmp_path / "auto_20260101_000000.zip").write_bytes(b"v1")   # V1 的不许碰
    (tmp_path / "manual_v1.zip").write_bytes(b"v1")

    backup._cleanup_old(keep=2)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["auto_20260101_000000.zip", "manual_v1.zip",
                    "v2_auto_20260102_000000.zip", "v2_auto_20260103_000000.zip"]


def test_backup_root_is_isolated_from_v1():
    # 生产：DATA_ROOT 同级 backups/（与 V1 同址但前缀隔离）；测试：临时目录同级
    assert backup.BACKUP_ROOT == config.DATA_ROOT.parent / "backups"
    assert backup._PREFIX == "v2_auto_"

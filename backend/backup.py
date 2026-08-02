"""
后端每日自动备份模块（自动层 / 服务常驻）
============================================
在 FastAPI 服务运行期间（服务常驻、没人碰启动器时），每天自动把整个 data/ 目录备份为 zip。

- 备份范围：整个 data/（数据库 + users/ + courses/ + trash/），不是只有 qingcheng.db
- 数据库用 sqlite3 官方 backup API 生成一致快照（自动包含 WAL 里尚未合并的写入）
- 备份位置：data 同级 backups/（例如 QC-PocketSpace/backups/）
- 保留策略：最近 14 份，最旧自动清理（覆盖约半个月）
- 触发：后台线程每 10 分钟检查一次，距上次备份 >= 间隔才备份（默认 24h=1 天，可配）

与启动器备份（qx-launcher/app/backup.py）的分工：
  · 后端（本文件）＝自动层：服务【运行中】时可用
      - 服务常驻期间每天自动备份，保留最近 14 份
  · 启动器（qx-launcher/app/backup.py）＝手动层：服务【未运行】时可用
      - 手动备份（导出到 U 盘等）、从备份 ZIP 恢复、启动前自动备份

两者时间错开、互为补充，共同覆盖全天候：
  服务没跑 → 靠启动器；服务在跑 → 靠后端。
行为已统一：同一备份目录 backups/、同一命名 auto_*.zip、保留最近 14 份。

可通过环境变量控制：
    QC_DISABLE_AUTO_BACKUP=1        禁用自动备份
    QC_BACKUP_INTERVAL_HOURS=48     备份间隔（小时），默认 24
    QC_BACKUP_KEEP=30               保留份数，默认 14
"""
import os
import time
import sqlite3
import shutil
import tempfile
import threading
import zipfile
from datetime import datetime

# ── 路径（与 models.py 保持一致，从本文件位置推导） ──
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.abspath(os.path.join(BACKEND_DIR, "..", "..", "data"))
DB_PATH = os.path.join(DATA_ROOT, "qingcheng.db")
# data 同级 backups/（例如 QC-PocketSpace/backups/）
BACKUP_ROOT = os.path.abspath(os.path.join(BACKEND_DIR, "..", "..", "backups"))

# ── 策略（可按机构调整） ──
CHECK_INTERVAL = 600       # 每 10 分钟检查一次（600s）
# 备份间隔（小时），默认 24h=1 天；机构课程集中可调大，如 48=2 天
BACKUP_INTERVAL = int(os.environ.get("QC_BACKUP_INTERVAL_HOURS", "24")) * 3600
KEEP = int(os.environ.get("QC_BACKUP_KEEP", "14"))   # 保留份数，默认 14（覆盖约半个月）
DISABLED = os.environ.get("QC_DISABLE_AUTO_BACKUP", "0") == "1"

_PREFIX = "auto_"  # 自动备份文件名前缀，用于区分手动备份


def _snapshot_db(src: str, dst: str):
    """用 sqlite3 官方 backup API 生成一致快照（自动包含 WAL 数据）"""
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _cleanup_old(keep: int = KEEP):
    """保留最近 keep 份自动备份，删除更旧的"""
    if not os.path.isdir(BACKUP_ROOT):
        return
    files = sorted(
        f for f in os.listdir(BACKUP_ROOT)
        if f.startswith(_PREFIX) and f.endswith(".zip")
    )
    for old in files[:-keep]:
        try:
            os.remove(os.path.join(BACKUP_ROOT, old))
        except OSError:
            pass


def do_backup() -> str:
    """
    执行一次全量备份，返回备份文件名（例如 auto_20260802_210000.zip）。
    备份 zip 内结构：
        data/qingcheng.db    ← 一致快照（含 WAL 数据，单一完整文件）
        data/users/...
        data/courses/...
        data/trash/...
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_name = f"{_PREFIX}{ts}.zip"
    dst_path = os.path.join(BACKUP_ROOT, dst_name)
    os.makedirs(BACKUP_ROOT, exist_ok=True)

    with zipfile.ZipFile(dst_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1️⃣ 数据库 → 一致快照（独立于 WAL 的单一文件）
        if os.path.exists(DB_PATH):
            tmp_dir = tempfile.mkdtemp(prefix="qc_backup_")
            try:
                snap = os.path.join(tmp_dir, "qingcheng.db")
                _snapshot_db(DB_PATH, snap)
                zf.write(snap, "data/qingcheng.db")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        # 2️⃣ 其余文件 → 原样复制（排除 db 及 wal/shm，db 已由快照覆盖）
        for root, dirs, files in os.walk(DATA_ROOT):
            for fn in files:
                if fn == "qingcheng.db" or fn.endswith((".db-wal", ".db-shm")):
                    continue
                fp = os.path.join(root, fn)
                arc = os.path.join("data", os.path.relpath(fp, DATA_ROOT))
                zf.write(fp, arc)

    _cleanup_old()
    return dst_name


def _last_backup_ts() -> float:
    """最近一次自动备份的时间戳，无则返回 0（表示应立即备份）"""
    if not os.path.isdir(BACKUP_ROOT):
        return 0
    files = [f for f in os.listdir(BACKUP_ROOT)
             if f.startswith(_PREFIX) and f.endswith(".zip")]
    if not files:
        return 0
    return max(os.path.getmtime(os.path.join(BACKUP_ROOT, f)) for f in files)


def _backup_loop():
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            if time.time() - _last_backup_ts() >= BACKUP_INTERVAL:
                name = do_backup()
                print(f"[auto-backup] 每日备份完成: {name}")
        except Exception as e:
            print(f"[auto-backup] 自动备份失败: {e}")


def start_auto_backup():
    """FastAPI 启动时调用，注册后台每日备份线程"""
    if DISABLED:
        print("[auto-backup] 已通过环境变量 QC_DISABLE_AUTO_BACKUP 禁用")
        return
    t = threading.Thread(target=_backup_loop, name="qc-auto-backup", daemon=True)
    t.start()
    print(f"[auto-backup] 每日自动备份已启动 → {BACKUP_ROOT}（保留最近 {KEEP} 份）")


if __name__ == "__main__":
    # 手动触发一次（调试用）：python backup.py
    name = do_backup()
    print(f"备份完成: {BACKUP_ROOT}\\{name}")

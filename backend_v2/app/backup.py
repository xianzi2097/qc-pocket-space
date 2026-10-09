"""每日自动备份（V1 backup.py 平移 · M4 ②）

- 备份范围：整个 data_v2/（数据库 + assets/ + trash/ 等），不是只有 db 文件
- 数据库用 sqlite3 官方 backup API 出一致快照（自动包含 WAL 未合并的写入）
- 位置：DATA_ROOT 同级 backups/（生产 = 工作区/backups/；测试 = 临时目录旁，天然隔离）
- 命名：v2_auto_*.zip（前缀隔离，V1 的 auto_*.zip 互不清理、互不误伤）
- 保留：最近 QC_BACKUP_KEEP 份（默认 14），最旧自动清理
- 触发：后台线程每 10 分钟检查，距上次 >= QC_BACKUP_INTERVAL_HOURS（默认 24h）才备
- 开关：QC_DISABLE_AUTO_BACKUP=1 禁用（测试环境已置 1，不起线程）

与启动器手动备份的分工沿用 V1：服务运行中靠本线程，服务未跑靠启动器（M5 对接）。
"""
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import config

logger = logging.getLogger("codenova.v2.backup")

BACKUP_ROOT = config.DATA_ROOT.parent / "backups"
_PREFIX = "v2_auto_"
CHECK_INTERVAL = 600                                        # 每 10 分钟看一眼
BACKUP_INTERVAL = int(os.environ.get("QC_BACKUP_INTERVAL_HOURS", "24")) * 3600
KEEP = int(os.environ.get("QC_BACKUP_KEEP", "14"))
DISABLED = os.environ.get("QC_DISABLE_AUTO_BACKUP", "0") == "1"


def _snapshot_db(src: Path, dst: Path) -> None:
    """sqlite3 官方 backup API：一致快照（含 WAL 数据）"""
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def do_backup() -> str:
    """执行一次全量备份，返回文件名（v2_auto_YYYYmmdd_HHMMSS.zip）"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_ROOT / f"{_PREFIX}{ts}.zip"
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        if config.DB_PATH.exists():                          # 1. 数据库 → 一致快照
            with tempfile.TemporaryDirectory(prefix="qc_v2_backup_") as tmp:
                snap = Path(tmp) / config.DB_PATH.name
                _snapshot_db(config.DB_PATH, snap)
                zf.write(snap, f"data_v2/{config.DB_PATH.name}")
        for path in config.DATA_ROOT.rglob("*"):             # 2. 其余文件原样入包
            if not path.is_file():
                continue
            if path.name == config.DB_PATH.name or path.name.endswith((".db-wal", ".db-shm")):
                continue                                     # db 已由快照覆盖
            zf.write(path, Path("data_v2") / path.relative_to(config.DATA_ROOT))

    _cleanup_old()
    return dst.name


def _cleanup_old(keep: int = KEEP) -> None:
    if not BACKUP_ROOT.is_dir():
        return
    files = sorted(f for f in BACKUP_ROOT.iterdir()
                   if f.is_file() and f.name.startswith(_PREFIX) and f.name.endswith(".zip"))
    for old in files[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass


def _last_backup_ts() -> float:
    if not BACKUP_ROOT.is_dir():
        return 0.0
    files = [f for f in BACKUP_ROOT.iterdir()
             if f.is_file() and f.name.startswith(_PREFIX) and f.name.endswith(".zip")]
    return max((f.stat().st_mtime for f in files), default=0.0)


def _backup_loop() -> None:
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            if time.time() - _last_backup_ts() >= BACKUP_INTERVAL:
                name = do_backup()
                logger.info("每日备份完成: %s", name)
        except Exception:
            logger.exception("自动备份失败")


def start_auto_backup() -> None:
    if DISABLED:
        logger.info("自动备份已禁用（QC_DISABLE_AUTO_BACKUP=1）")
        return
    threading.Thread(target=_backup_loop, name="qc-v2-backup", daemon=True).start()
    logger.info("每日自动备份已启动 → %s（保留最近 %d 份）", BACKUP_ROOT, KEEP)

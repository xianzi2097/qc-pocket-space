"""测试基架 —— 让每个测试用例"借"到公共设施，且不碰任何真实数据

三道隔离保证（为什么可以放心跑）：
  ① 进程内 TestClient       不起 uvicorn、不占 8000/8001 端口
  ② 临时数据区              开头创建临时目录当数据库，整个测试结束自动删除
  ③ 与 V1 完全无关          不连 qingcheng.db、不动 data/、不发真实 HTTP

提供的 fixture（测试函数直接把名字当参数用）：
  client            已启动的应用实例，直接 client.post("/api/auth/login", ...)
  login()           一键登录，返回 {"token":..., "account":...}
  _clean_...        自动：每个用例跑完清空登录限流计数（防止用例间互相锁死）
  _cleanup_...      自动：全部跑完删除临时目录

注意：设置环境变量必须在 import main 之前，否则 app 已按真库路径初始化。
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="qc_v2_test_"))
os.environ["QC_V2_DATA_ROOT"] = str(_TMP_ROOT / "data")
os.environ["QC_V2_DB"] = str(_TMP_ROOT / "test.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main as main_app  # noqa: E402  必须在环境变量设置之后导入

# 不经 TestClient（不起 lifespan）的测试也需要表存在，这里先幂等建一次
main_app.init_db()

ADMIN = {"username": "admin", "password": "111111"}


@pytest.fixture(scope="session")
def client():
    with TestClient(main_app.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clean_login_rate_limit():
    """测试共用同一来源 IP，逐例清空限流计数，避免用例间互相锁死"""
    from app.db import SessionLocal
    from app.models import LoginAttempt

    yield

    db = SessionLocal()
    try:
        db.query(LoginAttempt).delete()
        db.commit()
    finally:
        db.close()


def _cleanup_tmpdir_now() -> None:
    """归还连接池 + 删临时目录。
    挂在 atexit 上：pytest --collect-only 子进程不执行任何 fixture，
    只靠 session 钩子会每次泄漏一个目录（曾积压 31 个）。"""
    try:
        from app.db import engine

        engine.dispose()
    except Exception:
        pass
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


atexit.register(_cleanup_tmpdir_now)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmpdir():
    yield
    _cleanup_tmpdir_now()


@pytest.fixture()
def login(client):
    def _login(username: str = ADMIN["username"], password: str = ADMIN["password"]):
        resp = client.post("/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text
        return resp.json()["data"]

    return _login

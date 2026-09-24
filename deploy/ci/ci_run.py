# C:\QC-CI\ci_run.py —— CodeNova 服务器端 CI 看门狗（层2）
# schtasks 每 5 分钟调一次；只在有新 commit 时跑快测；结果写 status.json。
# 隔离铁律：独立目录 C:\QC-CI\qc-space（浅克隆），测试自带 data/，绝不接触生产
# 目录 C:\QC-Packet\qc-pocket-space；不跑 e2e（占端口 + 太重）；生产单进程 uvicorn 不受影响。
import json
import subprocess
import datetime
import pathlib
import sys

CI = pathlib.Path(r"C:\QC-CI")
REPO = CI / "qc-space"
VENV_PY = CI / "venv" / "Scripts" / "python.exe"
GIT = r"C:\Program Files\Git\cmd\git.exe"
BRANCH_FILE = CI / "watch_branch"  # 内容=跟踪的 rev（默认 origin/master；冒烟测试时临时改）
STATUS = CI / "status.json"
REPO_URL = "https://gitee.com/Arin-Cabin/qc-pocket-space.git"

ENV = {
    **__import__("os").environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "NO_PROXY": "localhost,127.0.0.1,::1",
}


def now():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def sh(cmd, cwd=None, timeout=300):
    r = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, shell=True,
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, env=ENV,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def write_status(d):
    STATUS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def read_status():
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    branch = BRANCH_FILE.read_text(encoding="utf-8").strip() if BRANCH_FILE.exists() else "origin/master"

    # 1) 首次浅克隆
    if not (REPO / ".git").exists():
        REPO.mkdir(parents=True, exist_ok=True)
        rc, out = sh(f'"{GIT}" clone --depth 50 {REPO_URL} "{REPO}"', cwd=CI, timeout=600)
        if rc != 0:
            write_status({"last_check": now(), "result": "SETUP_FAIL", "detail": out[-800:]})
            sys.exit(2)

    # 2) 同步远端（只动 .git，不碰工作区）。浅克隆默认单分支 refspec（只跟
    # origin/master），这里强制全分支 refspec，否则别的分支永远 fetch 不到。
    rc, out = sh(f'"{GIT}" fetch origin --prune "+refs/heads/*:refs/remotes/origin/*"',
                 cwd=REPO, timeout=180)
    if rc != 0:
        write_status({"last_check": now(), "result": "FETCH_FAIL", "detail": out[-800:]})
        sys.exit(3)

    rc, out = sh(f'"{GIT}" rev-parse --short {branch}', cwd=REPO, timeout=60)
    sha = out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else "unknown"

    # 3) 这个 sha 已经测过了 → 只打卡（保活），不重跑
    last = read_status()
    if last.get("run_sha") == sha and last.get("result") in ("PASS", "FAIL"):
        last["last_check"] = now()
        write_status(last)
        return

    # 4) 检出到目标 sha（在 CI 克隆里，与生产无关）
    rc, out = sh(f'"{GIT}" checkout -q -f {sha}', cwd=REPO, timeout=180)
    if rc != 0:
        write_status({"last_check": now(), "result": "CHECKOUT_FAIL",
                      "run_sha": sha, "detail": out[-800:]})
        sys.exit(4)

    # 5) 跑快测（cwd=CI 克隆 → 用它自己的 data/；TestClient 不占端口）
    rc, out = sh(
        f'"{VENV_PY}" -m pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/venv -q --tb=short',
        cwd=REPO, timeout=900,
    )
    tail = "\n".join(out.splitlines()[-25:])
    write_status({
        "last_check": now(), "ts_run": now(),
        "result": "PASS" if rc == 0 else "FAIL",
        "run_sha": sha, "branch": branch, "tail": tail,
    })
    sys.exit(0 if rc == 0 else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 任何意外都必须落状态，绝不允许静默死亡（看门狗靠它报警）
        import traceback
        try:
            write_status({"last_check": now(), "result": "CRASH",
                          "detail": repr(e) + "\n" + traceback.format_exc()[-600:]})
        except Exception:
            pass
        sys.exit(9)

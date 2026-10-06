"""数据库连接：只管"怎么连上 SQLite"，不管表长什么样（那是 models.py）

本文件提供：
  engine        连接对象（每次连上都设 WAL 日志模式 + 开启外键约束）
  SessionLocal  会话工厂：每个请求开一个独立会话，用完自动关
  Base          表结构基类：models.py 里所有 class 都继承它
  get_db()      FastAPI 依赖：接口参数写 Depends(get_db) 即可拿到会话
  init_db()     启动时建表（15 张，缺哪张建哪张，已存在则跳过）

位置记忆：config 管参数 → db 管连接 → models 管表结构
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 10},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  导入以注册表定义

    Base.metadata.create_all(bind=engine)

"""数据模型 + 数据库引擎"""
import os
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.abspath(os.path.join(BACKEND_DIR, "..", "..", "data", "qingcheng.db"))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

_DATA_ROOT = os.path.abspath(os.path.join(BACKEND_DIR, "..", "..", "data"))
USERS_DIR = os.path.join(_DATA_ROOT, "users")
for _d in [_DATA_ROOT, USERS_DIR,
           os.path.join(USERS_DIR, "admins"),
           os.path.join(USERS_DIR, "teachers"),
           os.path.join(USERS_DIR, "students"),
           os.path.join(_DATA_ROOT, "trash"),
           os.path.join(_DATA_ROOT, "courses")]:
    os.makedirs(_d, exist_ok=True)

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 10})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    role = Column(String)                                # "admin" / "teacher" / "student"
    avatar_path = Column(String, default="avatar_01.jpg")
    qc_descriptors = Column(Text, default="{}")


class TrashModel(Base):
    """垃圾桶：软删除的用户记录"""
    __tablename__ = "trash"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String)
    role = Column(String)
    original_path = Column(String)
    trash_folder = Column(String)
    db_snapshot = Column(Text)
    trashed_at = Column(String)


Base.metadata.create_all(bind=engine)

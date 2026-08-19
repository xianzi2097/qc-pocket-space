import sys
sys.path.insert(0, r"..\..\qc-pocket-space\backend")
from e2e_helpers import create_test_student, get_user_id

s = create_test_student("demo_view", "admin")
uid = get_user_id(s["username"])
print(f"User: {s['username']} / 111111")
print(f"ID: {uid}")

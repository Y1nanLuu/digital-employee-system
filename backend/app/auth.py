import secrets
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from .config import DEMO_PASSWORD

security = HTTPBasic()
USERS = {
    'employee': {'username': 'employee', 'name': '演示员工', 'role': 'employee'},
    'approver': {'username': 'approver', 'name': '演示审批人', 'role': 'approver'},
    'admin': {'username': 'admin', 'name': '演示管理员', 'role': 'admin'},
}

def current_user(credentials: HTTPBasicCredentials = Depends(security)):
    valid = secrets.compare_digest(credentials.password.encode(), DEMO_PASSWORD.encode())
    if credentials.username not in USERS or not valid:
        raise HTTPException(401, '账号或密码错误', headers={'WWW-Authenticate': 'Basic'})
    return USERS[credentials.username]

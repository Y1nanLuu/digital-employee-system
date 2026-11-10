import os

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./data/tasks.db')
FINANCE_URL = os.getenv('FINANCE_URL', 'http://127.0.0.1:8001')
FINANCE_KEY = os.getenv('FINANCE_KEY', 'local-finance-key')
DEMO_PASSWORD = os.getenv('DEMO_PASSWORD', 'demo123')
# 集成版本标识：健康检查对外暴露，便于联调与验收时确认当前运行版本
APP_VERSION = os.getenv('APP_VERSION', '0.4.0')
APP_STAGE = os.getenv('APP_STAGE', 'D4-baseline')

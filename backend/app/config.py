import os

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./data/tasks.db')
FINANCE_URL = os.getenv('FINANCE_URL', 'http://127.0.0.1:8001')
FINANCE_KEY = os.getenv('FINANCE_KEY', 'local-finance-key')
DEMO_PASSWORD = os.getenv('DEMO_PASSWORD', 'demo123')
# 集成版本标识：健康检查对外暴露，便于联调与验收时确认当前运行版本
APP_VERSION = os.getenv('APP_VERSION', '0.5.0')
APP_STAGE = os.getenv('APP_STAGE', 'enterprise-review')
ACTIVE_POLICY_VERSION = os.getenv('ACTIVE_POLICY_VERSION', 'enterprise-demo-v2')
FILE_STORAGE_DIR = os.getenv('FILE_STORAGE_DIR', './data/invoices')
COMPANY_TITLE = os.getenv('COMPANY_TITLE', '示例科技有限公司')

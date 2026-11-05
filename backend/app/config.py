import os

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./data/tasks.db')
FINANCE_URL = os.getenv('FINANCE_URL', 'http://127.0.0.1:8001')
FINANCE_KEY = os.getenv('FINANCE_KEY', 'local-finance-key')
DEMO_PASSWORD = os.getenv('DEMO_PASSWORD', 'demo123')

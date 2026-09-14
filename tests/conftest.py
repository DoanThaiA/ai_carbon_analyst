"""Set các biến môi trường bắt buộc TRƯỚC khi bất kỳ test module nào import
code của app (core/config.py::Settings.from_env() raise ngay nếu thiếu
DATABASE_URL) — không cần Postgres/Anthropic thật chạy được cho các test ở
đây, vì chúng chỉ test logic thuần (validate định dạng file, parse .docx),
không thực sự gọi DB/LLM.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/carbon_analyst")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

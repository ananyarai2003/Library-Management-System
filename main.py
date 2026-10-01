"""Entry point: uvicorn main:app --reload"""
from app.main import create_app

app = create_app()

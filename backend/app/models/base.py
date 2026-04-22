"""Declarative Base for all ORM models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared base for all SQLAlchemy models in this project."""

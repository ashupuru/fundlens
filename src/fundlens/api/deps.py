from collections.abc import Iterator

from sqlalchemy.orm import Session

from fundlens.db.session import get_engine


def get_session() -> Iterator[Session]:
    engine = get_engine()
    with Session(engine) as session:
        yield session

"""Keep network waits outside database transactions."""
from sqlalchemy.orm import Session


def run_model_outside_transaction(db: Session, chain, *args, **kwargs):
    # Persist logs/heartbeats before yielding to a remote provider. Callers must
    # refresh cancellation/lease state again before publishing returned data.
    db.commit()
    return chain.run_json(*args, **kwargs)

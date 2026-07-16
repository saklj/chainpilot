"""Injectable local embedding index and cosine retrieval for NL2SQL examples."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import duckdb
import numpy as np

from .nl2sql import FewShot
from .safe_sql import database_path

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_PATH = REPO_ROOT / "evals" / "questions_50.jsonl"


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class BgeEmbedder:
    """Lazy fastembed wrapper; importing this module never requires fastembed."""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5") -> None:
        self.model_name = model_name
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.model_name)
        return [np.asarray(vector, dtype=float).tolist() for vector in self._model.embed(texts)]


@dataclass(frozen=True)
class RetrievedExample:
    question_id: str
    question: str
    gold_sql: str
    category: str
    similarity: float

    def to_few_shot(self) -> FewShot:
        answer = f"```sql\n{self.gold_sql}\n```" if self.gold_sql else "NO_ANSWER"
        return FewShot(self.question, answer)


def _read_questions(path: str | Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        gold_sql = item.get("gold_sql")
        rows.append(
            {
                "id": str(item["id"]),
                "question": str(item["question"]),
                "gold_sql": gold_sql.strip() if isinstance(gold_sql, str) else "",
                "category": str(item["category"]),
            }
        )
    return rows


def build_index(
    connection: duckdb.DuckDBPyConnection,
    embedder: Embedder,
    questions_path: str | Path = QUESTIONS_PATH,
) -> int:
    questions = _read_questions(questions_path)
    vectors = embedder.embed([item["question"] for item in questions])
    if len(vectors) != len(questions):
        raise ValueError("embedder returned a different number of vectors")
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1 or not dimensions or next(iter(dimensions)) == 0:
        raise ValueError("embedding dimensions must be non-zero and consistent")
    connection.execute("DROP TABLE IF EXISTS qa_embedding")
    connection.execute(
        "CREATE TABLE qa_embedding (question_id VARCHAR PRIMARY KEY, question VARCHAR, "
        "gold_sql VARCHAR, category VARCHAR, embedding FLOAT[])"
    )
    connection.executemany(
        "INSERT INTO qa_embedding VALUES (?, ?, ?, ?, ?)",
        [
            (item["id"], item["question"], item["gold_sql"], item["category"], vector)
            for item, vector in zip(questions, vectors, strict=True)
        ],
    )
    return len(questions)


def retrieve_examples(
    connection: duckdb.DuckDBPyConnection,
    embedder: Embedder,
    question: str,
    k: int = 4,
    exclude_id: str | None = None,
    min_similarity: float | None = None,
) -> list[RetrievedExample]:
    if k <= 0:
        raise ValueError("k must be positive")
    query_vectors = embedder.embed([question])
    if len(query_vectors) != 1:
        raise ValueError("embedder must return exactly one query vector")
    query = np.asarray(query_vectors[0], dtype=float)
    rows = connection.execute(
        "SELECT question_id, question, gold_sql, category, embedding "
        "FROM qa_embedding ORDER BY question_id"
    ).fetchall()
    scored: list[RetrievedExample] = []
    query_norm = float(np.linalg.norm(query))
    for question_id, stored_question, gold_sql, category, embedding in rows:
        if exclude_id is not None and str(question_id) == exclude_id:
            continue
        vector = np.asarray(embedding, dtype=float)
        if vector.shape != query.shape:
            raise ValueError("query and index embedding dimensions differ")
        denominator = query_norm * float(np.linalg.norm(vector))
        similarity = float(np.dot(query, vector) / denominator) if denominator else 0.0
        if min_similarity is not None and similarity < min_similarity:
            continue
        scored.append(
            RetrievedExample(
                str(question_id),
                str(stored_question),
                str(gold_sql),
                str(category),
                similarity,
            )
        )
    return sorted(scored, key=lambda item: (-item.similarity, item.question_id))[:k]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--query")
    parser.add_argument("--db-path", type=Path, default=database_path())
    parser.add_argument("--k", type=int, default=4)
    args = parser.parse_args()
    connection = duckdb.connect(str(args.db_path))
    try:
        embedder = BgeEmbedder()
        if args.build:
            print(f"indexed={build_index(connection, embedder)}")
        else:
            for item in retrieve_examples(connection, embedder, args.query, args.k):
                print(f"{item.question_id}\t{item.similarity:.4f}\t{item.question}")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

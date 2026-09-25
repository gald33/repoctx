"""Defects an adversarial review reproduced against the first cut of 1.17.0.

Each test here failed on that cut (635b446) and names the reproduction it pins.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

numpy = pytest.importorskip("numpy")

from repoctx import embeddings  # noqa: E402
from repoctx.authority import AuthorityProducer  # noqa: E402
from repoctx.authority.extract import extract_constraints, extract_heading_bullets  # noqa: E402
from repoctx.graph import _resolve_ts_path  # noqa: E402
from repoctx.scanner import scan_repository  # noqa: E402
from repoctx.vector_index import IndexEntry, VectorIndex  # noqa: E402


def _index(paths: list[str]) -> VectorIndex:
    vecs = numpy.eye(len(paths), 4, dtype=numpy.float32)
    entries = [IndexEntry(path=p, kind="code", content_hash=f"h{i}") for i, p in enumerate(paths)]
    return VectorIndex(vectors=vecs, entries=entries, model_name="m", dimension=4)


def _bump(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_save_that_lands_while_the_model_loads_is_picked_up(tmp_path: Path, monkeypatch) -> None:
    """Review #1: the stamp was taken after the (>60 s) model load, so a save in
    that window was recorded as seen while the old vectors were held."""
    _index(["a.py"]).save(tmp_path)

    def model_whose_load_races_a_save(_config):
        _index(["a.py", "b.py"]).save(tmp_path)
        _bump(tmp_path / "index_config.json")
        return object()

    monkeypatch.setattr(embeddings, "HAS_EMBEDDINGS", True)
    monkeypatch.setattr(embeddings, "resolve_embeddings_dir", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(embeddings, "shared_embeddings_dir", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(embeddings, "_autoprovision_note", lambda *_a, **_k: "")
    monkeypatch.setattr(embeddings, "EmbeddingModel", model_whose_load_races_a_save)

    retriever = embeddings.load_retriever_status(tmp_path).retriever
    assert [e.path for e in retriever.index.entries] == ["a.py"], "fixture: loaded before the save"

    assert retriever.refresh_index() is True
    assert [e.path for e in retriever.index.entries] == ["a.py", "b.py"]


@pytest.mark.parametrize(
    "bullet",
    ["Avoid logging tokens", "Don’t log tokens", "**Never** log tokens", "Nobody may read raw secrets",
     "No raw SQL", "Must not block the event loop"],
)
def test_a_bullet_that_already_prohibits_is_not_prefixed(tmp_path: Path, bullet: str) -> None:
    """Review #3: 'Avoid logging tokens' became 'Do not avoid logging tokens'."""
    _write(tmp_path / "contracts" / "rules.md", f"# Rules\n\n## Do not\n- {bullet}\n")
    statements = [c.statement for c in extract_constraints(AuthorityProducer(tmp_path).build_authority_records())]
    assert statements == [bullet]


@pytest.mark.parametrize(
    ("bullet", "expected"),
    [("Stop containers mid-deploy", "Do not stop containers mid-deploy"),
     ("No-op writes to prod", "Do not no-op writes to prod"),
     ("Not-null columns without a default", "Do not not-null columns without a default"),
     ("Notify the owner twice", "Do not notify the owner twice")],
)
def test_a_bullet_that_only_looks_like_a_prohibition_keeps_its_not(
    tmp_path: Path, bullet: str, expected: str,
) -> None:
    """Re-review: a hyphen ended `\\b` ("No-op"), and "stop" is a verb, not a negation."""
    _write(tmp_path / "contracts" / "rules.md", f"# Rules\n\n## Do not\n- {bullet}\n")
    statements = [c.statement for c in extract_constraints(AuthorityProducer(tmp_path).build_authority_records())]
    assert statements == [expected]


def test_a_longer_fence_is_not_closed_by_a_shorter_one() -> None:
    """Review #4: a ``` line inside a ```` block closed it, and `- x` leaked out."""
    text = "## Invariants\n- real\n````md\n```\n- x\n```\n````\n"
    assert [b for _t, items in extract_heading_bullets(text) for b in items] == ["real"]


def test_inline_code_in_triple_backticks_does_not_open_a_fence() -> None:
    """Review #4: a line starting ```x``` opened a fence and swallowed the section."""
    text = "## Invariants\n```inline``` is not a fence\n- kept\n"
    assert [b for _t, items in extract_heading_bullets(text) for b in items] == ["kept"]


def test_a_readme_below_the_authority_root_is_still_a_contract(tmp_path: Path) -> None:
    """Review, suspected: skipping every README under contracts/** dropped real ones."""
    _write(tmp_path / "contracts" / "README.md", "# How contracts work\n\n## Do not\n- log token values\n")
    _write(tmp_path / "contracts" / "payments" / "README.md", "# Payments\n\n## Invariants\n- amounts are integers\n")

    records = AuthorityProducer(tmp_path).build_authority_records()
    paths = {r.path for r in records}
    assert "contracts/payments/README.md" in paths
    assert "contracts/README.md" not in paths


def test_ts_import_still_resolves_to_ts_before_shell(tmp_path: Path) -> None:
    """Review, suspected: .bash/.sh sorted first, so './foo' tried foo.bash before foo.ts."""
    _write(tmp_path / "src" / "foo.sh", "echo hi\n")
    _write(tmp_path / "src" / "foo.ts", "export const foo = 1;\n")
    _write(tmp_path / "src" / "main.ts", "import { foo } from './foo';\n")
    index = scan_repository(tmp_path)

    assert _resolve_ts_path("src/main.ts", "./foo", index) == "src/foo.ts"


def test_incremental_build_falls_back_on_an_inconsistent_index(tmp_path: Path, monkeypatch) -> None:
    """Review, suspected: the new load check raised through `index --incremental`."""
    _index(["a.py"]).save(tmp_path)
    numpy.save(tmp_path / "vectors.npy", numpy.eye(2, 4, dtype=numpy.float32))
    monkeypatch.setattr(embeddings, "resolve_embeddings_dir", lambda *_a, **_k: tmp_path)

    assert embeddings._load_compatible_existing_index(
        tmp_path, embeddings.DEFAULT_EMBEDDING_CONFIG, embeddings.ChunkConfig(),
    ) is None

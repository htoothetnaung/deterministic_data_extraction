from __future__ import annotations

from io import BytesIO

from app.services.artifact_store import ArtifactStore


def test_artifact_store_persists_raw_and_hash_marker(tmp_path):
    store = ArtifactStore(tmp_path)
    payload = BytesIO(b"hello document")

    storage_path, file_hash, size = store.store_raw("case-1", "doc-1", payload, "sample.pdf")
    store.remember_hash("case-1", file_hash, storage_path)

    assert size == len(b"hello document")
    assert file_hash
    assert tmp_path.joinpath("case-1", "doc-1", "raw", "sample.pdf").exists()
    assert store.hash_reuse("case-1", file_hash) == (True, storage_path)


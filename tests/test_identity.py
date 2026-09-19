"""Document identity: a file, at a version - not bytes alone.

Content-addressing alone collapses two things that must stay apart: the same bytes
filed under two access tags are two documents with two ACLs, and a file whose contents
change is one document at a new version whose old chunks must stop being retrievable.
"""
import shutil

import pytest

from docint.config import ROOT
from docint.models import ManifestEntry
from docint.parse import make_chunk_id, source_id
from docint.models import SourceLocation

LOC = SourceLocation(kind="pdf_page", page=1)
INVOICE = ROOT / "corpus" / "procurement" / "invoice_acme_001.pdf"


def test_identical_bytes_under_two_access_tags_are_two_documents(tmp_path):
    a = tmp_path / "procurement" / "same.pdf"
    b = tmp_path / "general" / "same.pdf"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(INVOICE, p)

    from docint.parse import content_hash, parse
    assert content_hash(a) == content_hash(b)          # same bytes
    assert source_id(a) != source_id(b)                # different documents

    doc_a, chunks_a = parse(a)
    doc_b, chunks_b = parse(b)
    assert doc_a.access_tag != doc_b.access_tag
    # Chunk ids must not collide, or one ACL silently overwrites the other in the store.
    assert {c.chunk_id for c in chunks_a}.isdisjoint({c.chunk_id for c in chunks_b})


def test_changed_content_yields_new_chunk_ids():
    old = make_chunk_id("corpus/x.pdf", "hash-v1", LOC)
    new = make_chunk_id("corpus/x.pdf", "hash-v2", LOC)
    assert old != new


def test_moving_a_file_changes_its_identity():
    """A move is an ACL event: the tag is derived from the directory, so the document
    must re-index rather than keep the identity it had elsewhere."""
    assert source_id("corpus/procurement/x.pdf") != source_id("corpus/general/x.pdf")


def test_unchanged_bytes_at_the_same_path_keep_every_id():
    a = make_chunk_id("corpus/x.pdf", "hash-v1", LOC)
    b = make_chunk_id("corpus/x.pdf", "hash-v1", LOC)
    assert a == b


def test_changed_content_deletes_the_previous_chunks(tmp_path, monkeypatch):
    """Otherwise new ids are written alongside the old ones, which stay retrievable."""
    deleted: list[list[str]] = []

    class StubIndex:
        pass

    import docint.index as index_mod
    monkeypatch.setattr(index_mod, "delete_chunks",
                        lambda index, ids: deleted.append(list(ids)))

    from docint.ingest import ingest_document
    target = tmp_path / "procurement" / "doc.pdf"
    target.parent.mkdir(parents=True)
    shutil.copy(INVOICE, target)

    manifest = {source_id(target): ManifestEntry(
        source_id=source_id(target), content_hash="a-different-hash",
        document_id="doc_old", filename="doc.pdf", access_tag="procurement",
        document_type="invoice", chunk_ids=["old1", "old2"],
        ingested_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )}

    # Stop before any model call - the deletion happens first, which is what matters.
    monkeypatch.setattr("docint.ingest.parse",
                        lambda p: (_ for _ in ()).throw(RuntimeError("stop after delete")))
    with pytest.raises(RuntimeError):
        ingest_document(target, manifest, index=StubIndex())

    assert deleted == [["old1", "old2"]]

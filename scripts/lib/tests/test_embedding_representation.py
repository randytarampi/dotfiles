"""Same-width embeddings must not pass without representation parity."""

from embedding_representation import REQUIRED_FIELDS, compare_representations


def identity(**changed):
    data = {
        "engine": "ollama",
        "model": "embeddinggemma",
        "revision": "immutable-revision",
        "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dimensions": 768,
        "pooling": "mean",
        "normalization": "l2",
        "task_prefix": "search_document:",
        "template": "default",
        "fixed_vector_sha256": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    }
    data.update(changed)
    return data


def test_all_authoritative_fields_required():
    assert compare_representations(identity(), identity())["status"] == "MATCH"
    for key in REQUIRED_FIELDS:
        missing = identity()
        del missing[key]
        result = compare_representations(identity(), missing)
        assert result["status"] == "UNKNOWN"
        assert key in result["unknown_fields"]


def test_same_width_and_model_can_be_a_different_representation():
    for field, variant in (
        (
            "digest",
            "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ),
        ("revision", "other-revision"),
        ("pooling", "cls"),
        ("normalization", "none"),
        ("task_prefix", "query:"),
        ("template", "other"),
        (
            "fixed_vector_sha256",
            "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ),
    ):
        result = compare_representations(identity(), identity(**{field: variant}))
        assert result["status"] == "MISMATCH"
        assert field in result["different_fields"]


def test_unqualified_identity_never_proves_compatibility():
    assert compare_representations(None, identity())["status"] == "UNKNOWN"
    assert (
        compare_representations(identity(), identity(dimensions=True))["status"]
        == "UNKNOWN"
    )
    assert (
        compare_representations(identity(), identity(dimensions=769))["status"]
        == "MISMATCH"
    )
    assert (
        compare_representations(identity(), identity(task_prefix=""))["status"]
        == "MISMATCH"
    )
    assert (
        compare_representations(identity(task_prefix=""), identity(task_prefix=""))[
            "status"
        ]
        == "MATCH"
    )


def test_digest_fields_are_never_accepted_as_placeholder_strings():
    for field in ("digest", "fixed_vector_sha256"):
        for malformed in ("sha256:fixture", "sha256:" + "g" * 64, "", "short"):
            result = compare_representations(identity(), identity(**{field: malformed}))
            assert result["status"] == "UNKNOWN"
            assert field in result["unknown_fields"]

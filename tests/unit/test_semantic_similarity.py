from apps.api.app.semantic_similarity import near_duplicate_groups, semantic_hash, similarity_report


def test_similarity_report_flags_number_only_variant():
    source = "A bag has 12 red balls and 8 blue balls. How many balls are there?"
    candidate = "A bag has 15 red balls and 9 blue balls. How many balls are there?"

    report = similarity_report(source, candidate)

    assert report["number_only_variant"] is True
    assert report["source_similarity_risk"] == "high"
    assert semantic_hash(source) == report["source_semantic_hash"]


def test_near_duplicate_groups_uses_semantic_threshold():
    rows = [
        ("q1", "A number is 3 more than another number, and the sum is 11."),
        ("q2", "A number is 5 more than another number, and the sum is 13."),
        ("q3", "Find the area of a rectangle with sides 4 and 9."),
    ]

    groups = near_duplicate_groups(rows, threshold=0.84)

    assert groups[0]["question_ids"] == ["q1", "q2"]
    assert groups[0]["number_only_variant"] is True

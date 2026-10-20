from app import badge_tips
from app.badge_reference import BADGES, lookup, norm

_TUPLES = {"tips", "watch_out", "fun_facts"}
_SCALARS = {"notes", "hint", "giveaway", "level"}


def test_every_entry_names_a_badge_the_reference_knows():
    """A typo would silently attach nothing, so the names are checked."""
    known = {norm(r["name"]) for r in BADGES.values()}
    assert {name for name in badge_tips.TIPS if norm(name) not in known} == set()


def test_entries_only_use_fields_the_page_renders_in_the_right_shape():
    for name, entry in badge_tips.TIPS.items():
        for field, value in entry.items():
            assert field in _TUPLES | _SCALARS, (name, field)
            if field in _TUPLES:
                assert isinstance(value, tuple) and value, (name, field)
                assert all(isinstance(v, str) and v.strip() for v in value), (name, field)
            else:
                assert isinstance(value, str) and value.strip(), (name, field)


def test_the_shared_name_is_left_alone():
    """Untouchable 5.0 is two badges; a name-keyed tip would land on both."""
    assert norm("Untouchable 5.0") not in {norm(n) for n in badge_tips.TIPS}


def test_tips_follow_the_documents_own():
    doc = lookup("2 Fast 2 See", "Complete Numbers level 2 without the eye going off")
    out = badge_tips.overlay("2 Fast 2 See", doc)

    assert out["tips"][: len(doc["tips"])] == doc["tips"]
    assert out["tips"][len(doc["tips"]):] == badge_tips.TIPS["2 Fast 2 See"]["tips"]
    assert out["watch_out"] == doc["watch_out"] + badge_tips.TIPS["2 Fast 2 See"]["watch_out"]


def test_a_correction_replaces_the_document_s_value():
    """Link level 1 stopped costing lives, so the recorded Thumb War method
    overshoots."""
    doc = lookup("Thumb War", "Score exactly 1234 points in any one level")
    assert "Link" in doc["notes"]

    out = badge_tips.overlay("Thumb War", doc)
    assert out["notes"].startswith("You must lose at least one life")
    assert out["difficulty"] == doc["difficulty"]      # everything else untouched


def test_overlay_copies_rather_than_mutating_and_passes_strangers_through():
    doc = lookup("Tiptoeing")
    before = dict(doc)
    badge_tips.overlay("Tiptoeing", doc)
    assert doc == before

    nobody = lookup("Some Badge Nobody Has Written Up")
    assert badge_tips.overlay("Some Badge Nobody Has Written Up", nobody) is nobody


def test_name_matching_is_normalised_like_the_reference():
    assert badge_tips.overlay("activ8", lookup("ACTIV8"))["tips"] == badge_tips.overlay(
        "ACTIV8", lookup("ACTIV8")
    )["tips"]

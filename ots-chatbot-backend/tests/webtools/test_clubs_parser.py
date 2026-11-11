from pathlib import Path

import pytest

from web_tools.parsers.clubs_html import LayoutChanged, parse_clubs_listing, split_type_line

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "web"


@pytest.fixture(scope="module")
def clubs():
    return {(c.slug or c.club_id): c for c in parse_clubs_listing((FIX / "clubs_listing_sample.html").read_text())}


def test_counts_and_keys(clubs):
    assert len(clubs) == 6
    assert clubs["33rd"].club_id == "35460"


def test_full_entry(clubs):
    c = clubs["33rd"]
    assert c.name == "33rd Street Productions"
    assert c.type == "Student Organization" and c.categories == ("Arts and Media",)
    assert c.website_url == "https://312.iit.edu/33rd/"
    assert c.mission.startswith("33rd Street Productions is a joint organization")
    assert c.membership_benefits.startswith("Members of 33rd Street Productions")
    assert "Mission" not in c.mission[:10]


def test_restricted_with_contact_and_empty_mission(clubs):
    c = clubs["AAR"]
    assert c.contact_name == "Subhrasankar Chatterjee"
    assert "restricted_to_some_users" in c.flags
    assert c.mission == "" and c.membership_benefits == ""
    assert c.type == "Mumbai Student Organization"
    assert c.categories == ("Academic", "Professional Organization", "Administration")


def test_not_registered_flag(clubs):
    assert "not_registered" in clubs["aba"].flags


def test_no_contact_is_none(clubs):
    assert any(c.contact_name is None for c in clubs.values())


def test_residence_hall_type(clubs):
    halls = [c for c in clubs.values() if c.type == "Residence Hall"]
    assert halls and halls[0].categories == ("Social",)


def test_whitespace_in_type_line():
    assert split_type_line("Residence Hall\n\t\t - Social") == ("Residence Hall", ("Social",))
    assert split_type_line("Administration") == ("Administration", ())


def test_placeholder_text_is_dropped():
    html = ('<ul><li class="list-group-item"><input id="cb_club_1"><h2><a href=" https://312.iit.edu/x/ ">X</a></h2>'
            '<p id="club_1"><strong>Mission</strong><br>Our mission is (define your mission under group settings)</p></li></ul>')
    (c,) = parse_clubs_listing(html)
    assert c.mission == "" and c.slug == "x"


@pytest.mark.parametrize("html", ["", "<html><body>Please sign in</body></html>", "[eventName] [date_text]"])
def test_layout_change_raises(html):
    with pytest.raises(LayoutChanged):
        parse_clubs_listing(html)


def test_many_unusable_entries_raise():
    bad = '<li class="list-group-item"><input id="cb_club_%d"><h2><span>no link</span></h2></li>'
    good = '<li class="list-group-item"><input id="cb_club_99"><h2><a href="https://312.iit.edu/ok/">Ok</a></h2></li>'
    with pytest.raises(LayoutChanged):
        parse_clubs_listing("<ul>" + good + "".join(bad % i for i in range(5)) + "</ul>")


def test_external_site_gives_no_slug(clubs):
    c = clubs["35443"]  # Robotics: listing links illinoistechrobotics.org, not a 312 page
    assert c.slug is None and c.website_url == "https://illinoistechrobotics.org/"


def test_iit_edu_path_is_not_mistaken_for_a_slug():
    html = ('<ul><li class="list-group-item"><input id="cb_club_7"><h2>'
            '<a href="https://www.iit.edu/arc/student-resources/x">Comm Lab</a></h2></li></ul>')
    (c,) = parse_clubs_listing(html)
    assert c.slug is None


def test_duplicate_listings_are_merged_by_club_id():
    one = ('<li class="list-group-item"><input id="cb_club_7"><h2><a href="https://312.iit.edu/x/">X</a></h2>'
           '<p class="grey-element">Student Organization - Academic</p>%s</li>')
    flagged = '<span class="badge">Group Not Registered Yet</span>'
    clubs = parse_clubs_listing("<ul>" + one % "" + one % flagged + "</ul>")
    assert len(clubs) == 1 and clubs[0].flags == ("not_registered",)


@pytest.mark.parametrize("label", ["Email group officers", "Contact us", "Send a message"])
def test_generic_contact_labels_are_not_names(label):
    html = ('<ul><li class="list-group-item"><input id="cb_club_5"><h2><a href="https://312.iit.edu/x/">X</a></h2>'
            f'<p>Contact: <a href="javascript:;">{label}</a></p></li></ul>')
    assert parse_clubs_listing(html)[0].contact_name is None

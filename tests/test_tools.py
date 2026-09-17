"""The tools that aren't job sources: clock, location, and the resume reader.

Each is exercised through its registered tool, because the string it returns
*is* its contract — the model reads that text and decides what to do next, so a
failure has to be a sentence and never an exception.
"""

from __future__ import annotations

import os
import zipfile
from datetime import datetime
from pathlib import Path

from scout.core import settings
from scout.tools import build_registry, clock, location, resume

from .conftest import FakeRequests, FakeResponse, call_tool


def run(module, name: str, **args) -> str:
    """Call a tool that needs no network."""
    registry = build_registry([module])
    tool = next(t for t in registry.tools if t.name == name)
    return tool.invoke(args)


# --- Clock ----------------------------------------------------------------


def test_the_clock_reports_a_local_time_with_a_zone() -> None:
    reported = run(clock, "get_current_time")

    hour, minute, _rest = reported.split(":", 2)
    assert 0 <= int(hour) <= 23
    assert 0 <= int(minute) <= 59


def test_the_date_is_spelled_out_for_the_model() -> None:
    """A model reasons about "posted 2 days ago" better from a full date."""
    reported = run(clock, "get_current_date")

    assert str(datetime.now().astimezone().year) in reported
    assert datetime.now().astimezone().strftime("%A") in reported


# --- Location -------------------------------------------------------------


def test_location_joins_city_region_and_country(monkeypatch) -> None:
    fake = FakeRequests(
        FakeResponse(
            {
                "status": "success",
                "city": "Boston",
                "regionName": "Massachusetts",
                "country": "United States",
            }
        )
    )

    assert call_tool(location, "get_location", fake, monkeypatch) == (
        "Boston, Massachusetts, United States"
    )
    assert fake.calls[0]["url"] == location.GEO_API_URL


def test_location_reports_what_the_service_did_return(monkeypatch) -> None:
    """Partial answers are common on the free tier; a city alone still helps."""
    fake = FakeRequests(FakeResponse({"status": "success", "city": "Boston"}))

    assert call_tool(location, "get_location", fake, monkeypatch) == "Boston"


def test_a_failed_lookup_is_a_sentence(monkeypatch) -> None:
    fake = FakeRequests(FakeResponse({"status": "fail", "message": "reserved range"}))

    assert call_tool(location, "get_location", fake, monkeypatch) == "Location unavailable."


def test_an_unreachable_geolocation_service_is_a_sentence(monkeypatch) -> None:
    fake = FakeRequests(error=OSError("no route to host"))

    reply = call_tool(location, "get_location", fake, monkeypatch)

    assert "couldn't reach" in reply


# --- Resume ---------------------------------------------------------------


def test_a_missing_data_folder_says_where_to_put_the_resume(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path / "data"))

    reply = run(resume, "get_resume_profile")

    assert "No data/ folder" in reply
    assert "PDF" in reply


def test_an_empty_data_folder_asks_for_a_resume(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    assert "No resume found" in run(resume, "get_resume_profile")


def test_a_text_resume_is_returned_with_its_filename(monkeypatch, tmp_path) -> None:
    (tmp_path / "resume.txt").write_text("Sai — ML Engineer\nPyTorch, Spark")
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    reply = run(resume, "get_resume_profile")

    assert "Resume file: resume.txt" in reply
    assert "PyTorch, Spark" in reply


def test_the_most_recent_resume_wins(monkeypatch, tmp_path) -> None:
    """Dropping in a new resume should take effect without deleting the old one."""
    old = tmp_path / "2024.md"
    old.write_text("the old one")
    new = tmp_path / "2026.md"
    new.write_text("the new one")
    os.utime(old, (1_000_000, 1_000_000))
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    assert "the new one" in run(resume, "get_resume_profile")


def test_files_that_are_not_resumes_are_ignored(monkeypatch, tmp_path) -> None:
    (tmp_path / "referrals.json").write_text("{}")
    (tmp_path / ".gitkeep").write_text("")
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    assert "No resume found" in run(resume, "get_resume_profile")


def test_an_empty_resume_is_reported_as_unreadable(monkeypatch, tmp_path) -> None:
    """A scanned PDF reads as empty text, which is worth saying out loud."""
    (tmp_path / "scan.txt").write_text("   \n")
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    assert "empty or unreadable" in run(resume, "get_resume_profile")


def test_a_corrupt_file_names_the_file_instead_of_raising(monkeypatch, tmp_path) -> None:
    (tmp_path / "resume.pdf").write_bytes(b"not really a pdf")
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    reply = run(resume, "get_resume_profile")

    assert reply.startswith("Could not read resume 'resume.pdf'")


def test_the_resume_folder_can_be_moved(monkeypatch, tmp_path) -> None:
    """An installed copy of the package needs this: its project root is inside
    site-packages, where nobody wants to keep a resume."""
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    (elsewhere / "cv.md").write_text("relocated")
    monkeypatch.setattr(settings, "RESUME_DIR", str(elsewhere))

    assert resume.resume_dir() == elsewhere
    assert "relocated" in run(resume, "get_resume_profile")


def test_a_folder_that_does_not_exist_holds_no_resumes(tmp_path) -> None:
    """``scout doctor`` asks this of a path the user may not have created yet."""
    assert resume.resumes_in(tmp_path / "nowhere") == []


def test_a_docx_resume_is_read_as_text(monkeypatch, tmp_path) -> None:
    _write_docx(tmp_path / "resume.docx", "Sai — ML Engineer at Northeastern")
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    reply = run(resume, "get_resume_profile")

    assert "Resume file: resume.docx" in reply
    assert "ML Engineer at Northeastern" in reply


def _write_docx(path: Path, text: str) -> None:
    """The smallest .docx docx2txt will read: one paragraph in a zip."""
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)

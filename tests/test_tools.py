"""The tools that aren't job sources: clock, location, and the resume reader.

Each is exercised through its registered tool, because the string it returns
*is* its contract — the model reads that text and decides what to do next, so a
failure has to be a sentence and never an exception.
"""

from __future__ import annotations

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

    assert "No resume found at data/resume.pdf" in reply
    assert "PDF" in reply


def test_an_empty_data_folder_asks_for_a_resume(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    assert "No resume found" in run(resume, "get_resume_profile")


def test_the_resume_is_returned_with_its_filename(monkeypatch, tmp_path) -> None:
    _write_pdf(tmp_path / "resume.pdf", "Sai - ML Engineer, PyTorch, Spark")
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    reply = run(resume, "get_resume_profile")

    assert reply.startswith(f"Resume file: {tmp_path.name}/resume.pdf")
    assert "PyTorch, Spark" in reply


def test_only_resume_pdf_is_read(monkeypatch, tmp_path) -> None:
    """A resume under any other name, or in any other format, is not the resume."""
    _write_pdf(tmp_path / "sai_resume0807.pdf", "an old one")
    (tmp_path / "resume.txt").write_text("a text one")
    (tmp_path / "resume.docx").write_bytes(b"a word one")
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    assert "No resume found" in run(resume, "get_resume_profile")


def test_an_empty_resume_is_reported_as_unreadable(monkeypatch, tmp_path) -> None:
    """A scanned PDF reads as empty text, which is worth saying out loud."""
    _write_pdf(tmp_path / "resume.pdf", "")
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    assert "empty or unreadable" in run(resume, "get_resume_profile")


def test_a_corrupt_file_names_the_file_instead_of_raising(monkeypatch, tmp_path) -> None:
    (tmp_path / "resume.pdf").write_bytes(b"not really a pdf")
    monkeypatch.setattr(settings, "RESUME_DIR", str(tmp_path))

    reply = run(resume, "get_resume_profile")

    assert reply.startswith(f"Could not read resume '{tmp_path.name}/resume.pdf'")


def test_the_resume_folder_can_be_moved(monkeypatch, tmp_path) -> None:
    """An installed copy of the package needs this: its project root is inside
    site-packages, where nobody wants to keep a resume."""
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    _write_pdf(elsewhere / "resume.pdf", "relocated")
    monkeypatch.setattr(settings, "RESUME_DIR", str(elsewhere))

    assert resume.resume_path() == elsewhere / "resume.pdf"
    assert "relocated" in run(resume, "get_resume_profile")


def _write_pdf(path: Path, text: str) -> None:
    """The smallest PDF pypdf will read text from: one page, one line of Helvetica."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            b" /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref,
    )
    path.write_bytes(bytes(pdf))

from pathlib import Path

import pytest

from app.services.parsers import doclayout_yolo_demo as demo


def test_missing_model_path_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo.settings, "doclayout_yolo_model_path", "")

    assert demo.is_available() is False
    assert "EXTRACT_DOCLAYOUT_YOLO_MODEL_PATH" in (demo.availability_notes() or "")


def test_normalize_detections_scales_bbox_and_labels() -> None:
    raw = type(
        "RawResult",
        (),
        {
            "names": {0: "table", 1: "plain text"},
            "boxes": type(
                "Boxes",
                (),
                {
                    "xyxy": [[10, 20, 110, 220], [0, 0, 50, 100]],
                    "cls": [0, 1],
                    "conf": [0.91, 0.72],
                },
            )(),
        },
    )()

    regions = demo._normalize_detections(raw, 3, 200, 400, 100, 200)

    assert regions[0]["page"] == 3
    assert regions[0]["layout_label"] == "plain text"
    assert regions[1]["layout_label"] == "table"
    assert regions[1]["bbox"] == pytest.approx({"x0": 5.0, "top": 10.0, "x1": 55.0, "bottom": 110.0})
    assert regions[1]["layout_confidence"] == 0.91


def test_parser_count_excludes_layout_and_errors() -> None:
    metadata = demo._document_metadata(
        Path("Balance Sheet.pdf"),
        12,
        {demo.TEXT_PARSER, demo.TABLE_PARSER, demo.ERROR_PARSER},
    )

    assert metadata["parser_count"] == 2
    assert metadata["parser_names"] == [demo.TABLE_PARSER, demo.TEXT_PARSER]
    assert metadata["document_title"] == "Balance Sheet"


def test_routed_parser_requires_parent_layout_region_id() -> None:
    with pytest.raises(ValueError, match="parent layout region id"):
        demo._parse_region(
            Path("input.pdf"),
            page=None,
            pdf_page=None,
            region={"page": 1, "bbox": {"x0": 0, "top": 0, "x1": 10, "bottom": 10}, "layout_label": "text"},
            region_id="",
            parser_name=demo.TEXT_PARSER,
        )

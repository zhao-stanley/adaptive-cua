import io
from dataclasses import replace

import pytest
from PIL import Image, ImageDraw

from adaptive_cua.browser.models import BrowserSnapshot, Element
from adaptive_cua.representation.observations import AdaptiveObservations
from adaptive_cua.representation.visual import screenshot_image


def element(
    identifier="e1", *, tag="input", name="Name", value="", text="", x=10, y=10, width=100, height=30, **kwargs
):
    return Element(
        identifier,
        tag,
        "textbox" if tag == "input" else ("button" if tag == "button" else ""),
        name,
        value,
        {"x": x, "y": y, "width": width, "height": height},
        True,
        True,
        tag == "input",
        None,
        text=text,
        **kwargs,
    )


def snap(version, elements=None, image=None, **kwargs):
    image = image or Image.new("RGB", (320, 240), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return BrowserSnapshot(
        version,
        "t1",
        "https://example.test",
        "Page",
        {"width": image.width, "height": image.height},
        elements or [],
        buffer.getvalue(),
        [],
        {},
        [],
        document_id=kwargs.pop("document_id", "doc1"),
        **kwargs,
    )


def chart():
    image = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 45, 45, 95), fill="blue")
    draw.rectangle((55, 65, 75, 95), fill="red")
    canvas = element("chart", tag="canvas", name="", x=20, y=20, width=100, height=100)
    return image, canvas


def test_form_is_text_only_and_changes_are_lossless():
    adapter = AdaptiveObservations()
    initial = adapter.prepare(snap(1, [element(), element("remove", name="Remove")]))
    assert initial.data["structure_mode"] == "full" and initial.images == []
    changed = adapter.prepare(
        snap(2, [element(value="Jane"), element("add", name="ZIP")]),
        action="type_text",
        arguments={"element_id": "e1", "text": "Jane"},
    )
    assert changed.data["base_version"] == 1
    assert changed.data["added"][0]["id"] == "add"
    assert changed.data["updated"][0]["value"] == "Jane"
    assert changed.data["removed"] == ["remove"]
    assert "elements" not in changed.data and changed.images == []
    unchanged = adapter.prepare(snap(3, [element(value="Jane"), element("add", name="ZIP")]))
    assert unchanged.data["added"] == unchanged.data["updated"] == unchanged.data["removed"] == []
    assert unchanged.data["base_version"] == 2


@pytest.mark.parametrize("change", ["document", "url", "tab", "viewport"])
def test_page_identity_resets_even_for_same_url_reload(change):
    adapter = AdaptiveObservations()
    adapter.prepare(snap(1, [element()]))
    current = snap(2, [element()])
    if change == "document":
        current.document_id = "new-document"
    elif change == "url":
        current.url += "/new"
    elif change == "tab":
        current.tab_id = "t2"
    else:
        current = snap(2, [element()], image=Image.new("RGB", (500, 200), "white"))
    result = adapter.prepare(current)
    assert result.data["structure_mode"] == "full" and result.data["base_version"] is None
    assert result.data["reset_visuals"]


def test_hidden_elements_become_removals():
    adapter = AdaptiveObservations()
    visible = element(tag="button", name="Delete")
    adapter.prepare(snap(1, [visible]))
    result = adapter.prepare(snap(2, [replace(visible, visible=False)]))
    assert result.data["removed"] == ["e1"] and not result.images


def test_crops_are_cached_changed_and_invalidated():
    image, canvas = chart()
    adapter = AdaptiveObservations()
    first = adapter.prepare(snap(1, [canvas], image))
    assert first.data["level"] == "crops" and len(first.images) == 1
    visual = first.data["visuals"][0]
    crop = Image.open(io.BytesIO(first.images[0]))
    assert crop.size == (visual["bounds"]["width"], visual["bounds"]["height"])
    assert visual["bounds"]["x"] == 16 and visual["bounds"]["y"] == 16
    assert visual["scale"] == 1 and visual["image_index"] == 0
    second = adapter.prepare(snap(2, [canvas, element("input", y=180, value="new")], image))
    assert not second.images
    assert second.data["visuals"][0]["source_observation_version"] == 1
    assert second.data["visuals"][0]["image_index"] is None
    ImageDraw.Draw(image).rectangle((30, 30, 60, 60), fill="green")
    third = adapter.prepare(snap(3, [canvas], image))
    assert len(third.images) == 1
    assert visual["id"] in third.data["removed_visuals"]
    fourth = adapter.prepare(snap(4))
    assert fourth.data["visuals"] == [] and fourth.data["removed_visuals"]
    assert not fourth.images


def test_named_container_does_not_mask_unexplained_pixels():
    image, _ = chart()
    container = element(tag="div", name="Dashboard", width=300, height=200)
    result = AdaptiveObservations().prepare(snap(1, [container], image))
    assert result.images and result.data["level"] == "crops"
    assert "unrepresented_visual_content" in result.data["reasons"]


def test_actual_text_rects_explain_pixels_without_masking_the_container():
    image = Image.new("RGB", (320, 240), "white")
    ImageDraw.Draw(image).text((10, 10), "Plain paragraph text", fill="black")
    paragraph = element(
        tag="p",
        name="",
        text="Plain paragraph text",
        width=300,
        height=200,
        text_rects=[{"x": 10, "y": 10, "width": 150, "height": 20}],
    )
    result = AdaptiveObservations().prepare(snap(1, [paragraph], image))
    assert not result.images
    assert result.data["elements"][0]["text"] == "Plain paragraph text"
    assert "text_rects" not in result.data["elements"][0]


def test_crop_clips_to_viewport_and_merges_nearby_candidates():
    canvas = element(tag="canvas", name="", x=-10, y=-10, width=40, height=40)
    nearby = element("icon", tag="button", name="", x=32, y=0, width=20, height=20)
    result = AdaptiveObservations().prepare(snap(1, [canvas, nearby]))
    assert len(result.images) == 1
    assert result.data["visuals"][0]["bounds"] == {"x": 0, "y": 0, "width": 56, "height": 34}


def test_large_visual_uses_full_screenshot():
    canvas = element(tag="canvas", name="", x=0, y=0, width=320, height=240)
    result = AdaptiveObservations().prepare(snap(1, [canvas]))
    assert result.data["level"] == "screenshot"
    assert "crop_budget_exceeded" in result.data["reasons"]
    assert Image.open(io.BytesIO(result.images[0])).size == (320, 240)


def test_failures_escalate_and_success_deescalates():
    adapter = AdaptiveObservations()
    button = element(tag="button", name="Submit")
    adapter.prepare(snap(1, [button]))
    first = adapter.prepare(snap(2, [button]), action="click", status="invalid_target")
    assert first.data["level"] == "structure"
    adapter.prepare(snap(3, [button]), action="observe", requested="full")
    second = adapter.prepare(snap(4, [button]), action="click", status="invalid_target")
    assert second.data["level"] == "screenshot"
    success = adapter.prepare(snap(5, [replace(button, name="Submitted")]), action="click")
    assert success.data["level"] == "changes" and not success.images


def test_repeated_no_effect_is_a_detail_signal_not_an_action_failure():
    adapter = AdaptiveObservations()
    button = element(tag="button", name="Submit")
    adapter.prepare(snap(1, [button]))
    first = adapter.prepare(snap(2, [button]), action="click", arguments={"element_id": "e1"})
    assert first.data["level"] == "structure"
    second = adapter.prepare(snap(3, [button]), action="click", arguments={"element_id": "e1"})
    assert second.data["level"] == "screenshot"
    assert "repeated_action_without_effect" in second.data["reasons"]


@pytest.mark.parametrize(
    "action,args",
    [
        ("click", {"element_id": "e1"}),
        ("type_text", {"element_id": "e1", "text": "Jane"}),
        ("press_key", {"key": "Tab"}),
    ],
)
def test_expected_noops_do_not_escalate(action, args):
    adapter = AdaptiveObservations()
    adapter.prepare(snap(1, [element(value="Jane")]))
    for version in (2, 3):
        result = adapter.prepare(snap(version, [element(value="Jane")]), action=action, arguments=args)
        assert result.data["level"] == "changes" and not result.images


def test_explicit_requests_periodic_refresh_and_reset():
    image, canvas = chart()
    adapter = AdaptiveObservations(full_refresh_interval=2)
    adapter.prepare(snap(1, [canvas], image))
    adapter.prepare(snap(2, [canvas], image))
    adapter.prepare(snap(3, [canvas], image))
    assert adapter.prepare(snap(4, [canvas], image)).data["structure_mode"] == "full"
    screenshot = adapter.prepare(snap(5, [canvas], image), requested="screenshot")
    assert screenshot.data["level"] == "screenshot" and len(screenshot.images) == 1
    crop = adapter.prepare(snap(6, [canvas], image), requested="crops")
    assert crop.data["level"] == "crops" and len(crop.images) == 1
    adapter.reset()
    restored = adapter.prepare(snap(7, [canvas], image))
    assert restored.data["structure_mode"] == "full" and len(restored.images) == 1
    assert restored.data["visuals"][0]["source_observation_version"] == 7


def test_full_mode_preserves_screenshot_every_step():
    adapter = AdaptiveObservations(mode="full")
    for version in (1, 2):
        result = adapter.prepare(snap(version, [element()]))
        assert result.data["structure_mode"] == "full" and len(result.images) == 1


def test_mismatched_screenshot_scale_is_rejected():
    current = snap(1)
    current.viewport["width"] = 640
    with pytest.raises(ValueError, match="dimensions"):
        screenshot_image(current)


def test_new_popup_is_progress_even_if_active_page_pixels_are_unchanged():
    adapter = AdaptiveObservations()
    button = element(tag="button", name="Open popup")
    adapter.prepare(snap(1, [button]))
    current = snap(2, [button])
    current.tabs = [
        {"id": "t1", "title": "Page", "url": current.url},
        {"id": "t2", "title": "Popup", "url": "about:blank"},
    ]
    result = adapter.prepare(current, action="click", arguments={"element_id": "e1"})
    assert result.data["level"] == "changes"
    assert "no_observed_effect" not in result.data["reasons"]

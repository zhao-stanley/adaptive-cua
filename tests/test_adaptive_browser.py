import io

import pytest
from PIL import Image

from src.browser.session import BrowserSession
from src.representation.observations import AdaptiveObservations


@pytest.fixture
def browser():
    session = BrowserSession(headless=True)
    session.start()
    yield session
    session.close()


def test_real_form_keeps_paragraph_and_confirmation_text_without_images(browser):
    browser.page.set_content("""
        <p>Shipping costs $5.</p><p style="display:none">Secret promo code</p>
        <form onsubmit="event.preventDefault();document.querySelector('#status').textContent='Order placed for Jane'">
        <label>Name<input required></label><button>Place order</button></form>
        <div id="status"></div>
    """)
    adapter = AdaptiveObservations()
    initial = browser.tools.observe()
    prepared = adapter.prepare(initial)
    assert not prepared.images
    assert any(e["text"] == "Shipping costs $5." for e in prepared.data["elements"])
    assert not any("Secret promo" in e["text"] for e in prepared.data["elements"])
    field = next(e for e in initial.elements if e.name == "Name")
    result = browser.tools.execute("type_text", element_id=field.id, text="Jane")
    update = adapter.prepare(result.observation, action="type_text", arguments={"element_id": field.id, "text": "Jane"})
    assert update.data["structure_mode"] == "delta" and not update.images
    button = next(e for e in result.observation.elements if e.name == "Place order")
    submitted = browser.tools.execute("click", element_id=button.id)
    confirmation = adapter.prepare(submitted.observation, action="click", arguments={"element_id": button.id})
    assert confirmation.data["structure_mode"] == "delta" and not confirmation.images
    assert any(e["text"] == "Order placed for Jane" for e in confirmation.data["added"] + confirmation.data["updated"])


def test_real_canvas_is_cropped_and_updated_when_pixels_change(browser):
    browser.page.set_content("""<h1>Quarterly sales</h1><canvas width="200" height="120"></canvas>
        <script>const ctx = document.querySelector('canvas').getContext('2d');
        ctx.fillStyle='red';ctx.fillRect(20,20,40,70);ctx.fillStyle='black';ctx.fillText('Q1',20,110);</script>""")
    adapter = AdaptiveObservations()
    first = adapter.prepare(browser.tools.observe())
    assert first.data["level"] == "crops" and len(first.images) == 1
    crop = Image.open(io.BytesIO(first.images[0]))
    assert crop.width < 300 and crop.height < 160
    second = adapter.prepare(browser.tools.observe())
    assert not second.images and second.data["visuals"][0]["image_index"] is None
    browser.page.locator("canvas").evaluate(
        "e => {const c=e.getContext('2d');c.fillStyle='blue';c.fillRect(80,20,40,70)}"
    )
    third = adapter.prepare(browser.tools.observe())
    assert len(third.images) == 1
    assert first.data["visuals"][0]["id"] in third.data["removed_visuals"]


def test_real_overlay_hides_visual_candidate_until_dismissed(browser):
    browser.page.set_content("""<canvas width="200" height="100"></canvas>
        <div style="position:fixed;inset:0;background:white">
        <button onclick="this.parentElement.remove()">Dismiss</button></div>""")
    adapter = AdaptiveObservations()
    snapshot = browser.tools.observe()
    first = adapter.prepare(snapshot)
    assert not first.images
    button = next(e for e in snapshot.elements if e.name == "Dismiss")
    result = browser.tools.execute("click", element_id=button.id)
    second = adapter.prepare(result.observation, action="click", arguments={"element_id": button.id})
    assert second.data["level"] == "crops"


def test_real_same_url_reload_resets_document_baseline(browser, tmp_path):
    file = tmp_path / "page.html"
    file.write_text("<p>Same URL</p>")
    browser.tools.execute("navigate", url=file.as_uri())
    adapter = AdaptiveObservations()
    first = adapter.prepare(browser.tools.observe())
    browser.page.reload()
    second = adapter.prepare(browser.tools.observe())
    assert first.data["url"] == second.data["url"]
    assert first.data["document_id"] != second.data["document_id"]
    assert second.data["structure_mode"] == "full"

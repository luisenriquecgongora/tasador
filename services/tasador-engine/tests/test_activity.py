import io
import pytest
from PIL import Image
from conftest import FakeVision, jpeg_bytes, make_client
from tasador_engine.activity import ActivityTracker, GLOBAL_ACTIVITY, make_thumbnail_b64
from tasador_engine.config import Settings
from tasador_engine.images import PreparedImage
from tasador_engine.main import create_app
from tasador_engine.schemas import (
    Extracted,
    IdentifyContext,
    IdentifyResponse,
    Identification,
    Links,
    Price,
    ProviderInfo,
)


def _sample_image() -> PreparedImage:
    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return PreparedImage(jpeg=buf.getvalue(), image=img)


def test_activity_tracker_lifecycle():
    tracker = ActivityTracker(max_runs=10)
    tracker.clear()
    assert len(tracker.list_history()) == 0

    img = _sample_image()
    ctx = IdentifyContext(currency="PLN", country="PL")

    run = tracker.start_run([img], ctx)
    assert run.status == "running"
    assert len(run.thumbnails) == 1
    assert run.input_summary["currency"] == "PLN"

    tracker.add_step(
        run_id=run.id,
        step_id="barcodes",
        title="ZXing Barcode Detection",
        status="ok",
        duration_ms=15,
        inputs={"images": 1},
        outputs={"count": 0, "barcodes": []},
    )

    response = IdentifyResponse(
        best=Identification(name="Sample Mixer", brand="Pioneer", confidence=0.92),
        price=Price(currency="PLN"),
        links=Links(),
        extracted=Extracted(),
        provider=ProviderInfo(deployment="local"),
        timings_ms={"total": 250},
    )

    tracker.complete_run(run.id, response, duration_ms=250)

    history = tracker.list_history()
    assert len(history) == 1
    assert history[0]["id"] == run.id
    assert history[0]["status"] == "completed"
    assert history[0]["best"]["name"] == "Sample Mixer"
    assert history[0]["can_rerun"] is True

    detail = tracker.get_run(run.id)
    assert detail is not None
    assert len(detail["steps"]) == 1
    assert detail["steps"][0]["step_id"] == "barcodes"
    assert detail["output"]["best"]["name"] == "Sample Mixer"

    replay = tracker.get_replay_data(run.id)
    assert replay is not None
    assert len(replay[0]) == 1
    assert replay[1].currency == "PLN"

    tracker.clear()
    assert len(tracker.list_history()) == 0


def test_activity_endpoints(settings):
    client = make_client(settings, FakeVision())
    GLOBAL_ACTIVITY.clear()

    # Initially empty history
    res = client.get("/v1/activity/history")
    assert res.status_code == 200
    assert res.json() == []

    # Non-existent run
    res = client.get("/v1/activity/non_existent")
    assert res.status_code == 404

    # Run identify to trigger activity tracking
    img = _sample_image()
    res = client.post(
        "/v1/identify",
        files=[("images", ("test.jpg", img.jpeg, "image/jpeg"))],
        data={"context": '{"currency": "EUR"}'},
    )
    assert res.status_code == 200

    history = client.get("/v1/activity/history").json()
    assert len(history) == 1
    run_id = history[0]["id"]
    assert history[0]["status"] == "completed"

    detail = client.get(f"/v1/activity/{run_id}").json()
    assert detail["id"] == run_id
    assert len(detail["steps"]) >= 3  # input, barcodes, ocr, output

    # Re-run the activity
    rerun_res = client.post(f"/v1/activity/{run_id}/rerun")
    assert rerun_res.status_code == 200

    # History now has 2 runs
    history2 = client.get("/v1/activity/history").json()
    assert len(history2) == 2

    # Clear history
    del_res = client.delete("/v1/activity/history")
    assert del_res.status_code == 200
    assert client.get("/v1/activity/history").json() == []

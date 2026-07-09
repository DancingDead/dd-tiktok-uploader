import pytest

from db import add_member, connect
from webui import create_app


@pytest.fixture
def client(tmp_path):
    conn = connect(tmp_path / "platform.db")
    add_member(conn, "theo", "s3cret")
    conn.close()
    app = create_app(root=tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def test_api_requires_login(client):
    assert client.get("/api/state").status_code == 401


def test_login_logout_cycle(client):
    bad = client.post("/api/login", json={"name": "theo", "password": "faux"})
    assert bad.status_code == 401

    ok = client.post("/api/login", json={"name": "theo", "password": "s3cret"})
    assert ok.status_code == 200

    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.get_json()["member"] == "theo"
    assert state.get_json()["niches"] == []
    assert state.get_json()["presets"] == []

    client.post("/api/logout")
    assert client.get("/api/state").status_code == 401


def test_index_served_without_login(client):
    assert client.get("/").status_code == 200

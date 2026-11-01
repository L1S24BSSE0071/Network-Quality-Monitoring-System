"""Dashboard layout and static asset serving tests."""


def test_dashboard_root_serves_html_layout(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    html = response.text
    assert "Network Quality Monitoring System" in html
    assert '/static/styles.css' in html
    assert '/static/app.js' in html
    assert 'id="overview-cards"' in html
    assert 'id="targets-section"' in html
    assert 'id="targets-empty"' in html
    assert 'id="targets-table"' in html
    assert 'id="targets-tbody"' in html
    assert 'id="refresh-btn"' in html


def test_dashboard_static_assets_are_served(client):
    css_resp = client.get("/static/styles.css")
    assert css_resp.status_code == 200
    assert "text/css" in css_resp.headers["content-type"]
    assert ".badge-normal" in css_resp.text
    assert ".badge-warning" in css_resp.text
    assert ".badge-critical" in css_resp.text
    assert ".badge-unknown" in css_resp.text

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "javascript" in js_resp.headers["content-type"]
    assert "/targets/summaries" in js_resp.text
    assert "loadDashboard" in js_resp.text
    assert "renderTargets" in js_resp.text

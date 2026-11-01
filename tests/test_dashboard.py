"""Dashboard layout and static asset serving tests."""


def test_dashboard_root_serves_html_layout(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    html = response.text
    assert "Network Quality Monitoring System" in html
    assert '/static/styles.css' in html
    assert '/static/app.js' in html
    assert 'data:image/svg+xml' in html
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
    assert "AbortController" in js_resp.text


def test_static_missing_file_returns_404(client):
    assert client.get("/static/non_existent.js").status_code == 404


def test_dashboard_contract_matches_summaries_endpoint(client, make_target):
    """Verify that /targets/summaries response contains all keys expected by app.js."""
    target = make_target(name="Dashboard Test Target")
    response = client.get("/targets/summaries")
    assert response.status_code == 200
    summaries = response.json()
    assert len(summaries) == 1
    item = summaries[0]

    expected_keys = {
        "target_id",
        "name",
        "host_or_url",
        "check_type",
        "interval_seconds",
        "warning_ms",
        "critical_ms",
        "enabled",
        "status",
        "last_checked_at",
        "last_is_up",
        "last_latency_ms",
        "last_http_status",
        "last_error_message",
        "total_checks",
        "up_checks",
        "failed_checks",
        "uptime_pct",
        "avg_latency_ms",
    }
    assert expected_keys.issubset(set(item.keys()))
    assert item["target_id"] == target["id"]
    assert item["name"] == "Dashboard Test Target"
    assert item["status"] == "unknown"

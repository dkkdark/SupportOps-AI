"""Published notices use real operator details and cannot redirect to themselves."""

from tests.test_workspaces import register


def test_public_notices_are_complete_and_use_current_release_details(platform):
    client, _, _ = platform
    client.app.state.auth.config.demo_release = True
    for path in ("/privacy", "/terms"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Kseniia Blokhina" in response.text
        assert "Germany" in response.text
        assert "info.xenonimus@gmail.com" in response.text
        for unfinished in (
            "Draft for operator review",
            "to be completed",
            "must complete",
            "[Service operator",
        ):
            assert unfinished not in response.text
    terms = client.get("/terms").text
    assert "10 registered accounts" in terms
    assert "$5.00" in terms
    privacy = client.get("/privacy").text
    assert "OpenAI" in privacy and "Cloudflare Turnstile" in privacy


def test_operator_details_are_escaped_and_local_policy_links_do_not_loop(platform):
    client, _, _ = platform
    config = client.app.state.auth.config
    config.legal_business_name = 'Operator <script>alert("x")</script>'
    response = client.get("/terms")
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    config.public_base_url = "https://app.example.test"
    config.legal_terms_url = config.public_base_url + "/terms/"
    config.legal_privacy_url = config.public_base_url + "/privacy"
    config.legal_documents_approved = True
    assert client.get("/terms", follow_redirects=False).status_code == 200
    assert client.get("/privacy", follow_redirects=False).status_code == 200
    config.legal_privacy_url = "https://policies.example.test/privacy"
    response = client.get("/privacy", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == config.legal_privacy_url


def test_workspace_status_describes_the_supported_database_and_policies(platform):
    client, _, _ = platform
    register(client)
    checks = {c["name"]: c for c in client.get("/api/launch-status").json()["checks"]}
    assert checks["Database"]["ready"]
    assert checks["Operator and policies"]["ready"]

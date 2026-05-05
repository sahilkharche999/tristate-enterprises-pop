def test_login_sets_cross_site_refresh_cookie(client):
    signup_response = client.post(
        "/auth/signup",
        json={
            "email": "cookie-check@example.com",
            "name": "Cookie Check",
            "password": "password123",
        },
    )
    assert signup_response.status_code == 201

    login_response = client.post(
        "/auth/login",
        json={
            "email": "cookie-check@example.com",
            "password": "password123",
        },
    )

    assert login_response.status_code == 200
    set_cookie = login_response.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "SameSite=none" in set_cookie
    assert "Secure" in set_cookie

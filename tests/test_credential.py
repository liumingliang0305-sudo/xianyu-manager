import stat

import httpx

from xianyu_core.credential import Credential, jar_to_cookies


def test_save_and_load_roundtrip_with_private_permissions(tmp_path):
    cred = Credential(cookies={"unb": "42", "cookie2": "c2", "_m_h5_tk": "tok_1"})
    path = cred.save(tmp_path / "accounts" / "42.json")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = Credential.load(path)
    assert loaded.cookies == cred.cookies
    assert loaded.h5_token == "tok"
    assert loaded.saved_at is not None


def test_from_cookie_header():
    cred = Credential.from_cookie_header("unb=42; cookie2=a=b; _m_h5_tk=tok_1; broken")
    assert cred.cookies == {"unb": "42", "cookie2": "a=b", "_m_h5_tk": "tok_1"}
    assert cred.missing_login_cookies() == []


def test_jar_prefers_shared_domain_cookie():
    jar = httpx.Cookies()
    jar.set("cookie2", "host-only", domain="passport.goofish.com")
    jar.set("cookie2", "shared", domain=".goofish.com")
    jar.set("x", "1", domain="passport.goofish.com")
    assert jar_to_cookies(jar) == {"cookie2": "shared", "x": "1"}

"""共用 fixture：假平台和一个已登录账号。"""

import httpx
import pytest
from fakes import UNB, FakePlatform

from app.accounts import Accounts
from xianyu_core.credential import Credential


@pytest.fixture
def platform():
    return FakePlatform(
        {
            "1001": {"title": "洗衣机", "price": 35800, "status": 0},
            "1002": {"title": "平板", "price": 140000, "status": 1},
        }
    )


@pytest.fixture
def accounts(tmp_path, platform):
    Credential(cookies={"unb": UNB, "_m_h5_tk": "tok_1"}).save(tmp_path / f"{UNB}.json")
    return Accounts(tmp_path, transport=httpx.MockTransport(platform))

"""共用 fixture：假平台和一个已登录账号。"""

import httpx
import pytest
from fakes import UNB, FakePlatform

from app.accounts import Accounts
from xianyu_core import renew
from xianyu_core.credential import Credential


@pytest.fixture(autouse=True)
def no_renew_delay(monkeypatch):
    """免密续期前不用真等 2 秒。"""
    monkeypatch.setattr(renew, "SILENT_RENEW_DELAY", 0)


@pytest.fixture
def platform():
    return FakePlatform(
        {
            "1001": {"title": "洗衣机", "price": 35800, "status": 0},
            "1002": {"title": "平板", "price": 140000, "status": 1},
        },
        {
            "5001": {
                "item_id": "1002",
                "status": "交易成功",
                "price": 140000,
                "post": 0,
                "created": 1761700000000,
            },
            "5002": {
                "item_id": "1001",
                "status": "等待买家付款",
                "price": 35800,
                "post": 1000,
                "created": 1761800000000,
            },
        },
    )


@pytest.fixture
def accounts(tmp_path, platform):
    Credential(cookies={"unb": UNB, "_m_h5_tk": "tok_1"}).save(tmp_path / f"{UNB}.json")
    return Accounts(tmp_path, transport=httpx.MockTransport(platform))

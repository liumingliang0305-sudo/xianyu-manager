from xianyu_core.sign import h5_token, mtop_sign

# 期望值用 macOS 自带的 `md5` 命令独立算出，不是用被测代码生成的


def test_mtop_sign_matches_md5_of_joined_fields():
    assert mtop_sign("abc", "1700000000000", '{"a":1}') == "82935b9ade4505038b200b96c23f3f44"


def test_mtop_sign_without_token():
    assert mtop_sign("", "1700000000000", "{}") == "682fdab74a53471d2bf2a6994077bb71"


def test_mtop_sign_with_chinese_data_uses_utf8():
    assert (
        mtop_sign("tok", "1700000000000", '{"q":"机械键盘"}') == "6b11376fe0b4f672c56e411af544a97d"
    )


def test_h5_token_takes_part_before_underscore():
    assert h5_token("3f2a9c_1761600000000") == "3f2a9c"
    assert h5_token("") == ""
    assert h5_token(None) == ""

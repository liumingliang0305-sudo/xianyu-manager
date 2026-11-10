import pytest

from app import wordcheck
from app.wordcheck import check, load_rules, normalize


def hits(text):
    return [(h.text, h.category, h.level) for h in check(text)]


def test_word_list_loads():
    rules = load_rules()
    categories = {r.category.key for r in rules}
    assert categories == {
        "contact",
        "fake",
        "prohibited",
        "virtual",
        "medical",
        "absolute",
        "platform",
        "promotion",
        "business",
    }
    # 每条都带档位、依据；经营性表述不给替换建议
    assert all(r.level in wordcheck.LEVELS and r.basis in wordcheck.BASES for r in rules)
    assert all(r.suggest is None for r in rules if r.category.key == "business")


@pytest.mark.parametrize(
    ("text", "word", "category", "level"),
    [
        ("有事加我", "加我", "contact", "strong"),
        ("详情加我", "加我", "contact", "strong"),
        ("加个微信细聊", "加个微信", "contact", "strong"),
        ("可以微信联系", "微信联系", "contact", "strong"),
        ("QQ找我", "QQ找我", "contact", "strong"),
        ("走支付宝便宜点", "走支付宝", "contact", "strong"),
        ("高仿 AJ1", "高仿", "fake", "strong"),
        ("纯原版本", "纯原", "fake", "strong"),
        ("1:1复刻，做工好", "1:1复刻", "fake", "strong"),
        ("仿真枪模型", "仿真枪", "prohibited", "strong"),
        ("百度网盘资源", "百度网盘", "virtual", "weak"),
        ("三天根治失眠", "根治", "medical", "weak"),
        ("全网最低", "全网最低", "absolute", "weak"),
        ("成色完美", "完美", "absolute", "weak"),
        ("拼多多百亿补贴买的", "拼多多", "platform", "weak"),
        ("复制淘口令打开", "淘口令", "platform", "strong"),
        ("好评返现 5 元", "好评返现", "promotion", "weak"),
        ("一手货源，量大从优", "一手货源", "business", "info"),
    ],
)
def test_hits(text, word, category, level):
    assert (word, category, level) in hits(text)


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("威信同号", "威信"),
        ("薇信", "薇信"),
        ("V信", "V信"),
        ("v❤️", "v❤"),
        ("➕我", "➕我"),
        ("+我", "+我"),
        ("ＶＸ", "ＶＸ"),  # 全角
        ("微 信 联系", "微 信 联系"),  # 中间加空格
        ("加.微信", "加.微信"),
        ("扣扣", "扣扣"),
    ],
)
def test_variants(text, word):
    assert any(h.text == word and h.category == "contact" for h in check(text)), check(text)


@pytest.mark.parametrize(
    ("text", "found", "rule"),
    [
        ("电话13812345678", "13812345678", "手机号"),
        ("电话 138 1234 5678 ", "138 1234 5678", "手机号"),
        ("电话１３８１２３４５６７８", "１３８１２３４５６７８", "手机号"),
        ("QQ：12345678", "QQ：12345678", "QQ 号"),
        ("q号 123456", "q号 123456", "QQ 号"),
        ("加Q 1234567", "加Q 1234567", "QQ 号"),
        ("V：xiaoming", "V：xiaoming", "微信号"),
        ("微信号 abc_123", "微信号 abc_123", "微信号"),
        ("vx: lily2024", "vx: lily2024", "微信号"),
        ("加v ab12345", "加v ab12345", "微信号"),
        ("看 https://m.tb.cn/h.abc 这个", "https://m.tb.cn/h.abc", "链接"),
        ("taobao.com 上买的", "taobao.com", "链接"),
    ],
)
def test_patterns(text, found, rule):
    assert (found, rule) in [(h.text, h.rule) for h in check(text)], check(text)


@pytest.mark.parametrize(
    "text",
    [
        "最近买的，九成新",
        "加热很快，加湿器",
        "微信支付不支持，走闲鱼",
        "不接受转账，拍下就发",
        "参加我们学校的比赛用过",
        "第一次用，唯一的缺点是有点重",
        "儿童电话手表",
        "100%棉，最大支持 128G",
        "最好自提，最好是同城",
        "王者荣耀皮肤周边，极品飞车光盘",
        "京东方屏幕",
        "1688 元入的，原价 1688元",
        "实体店买的",
        "Vans 板鞋，vintage1990 款",
        "加vip会员送的",
        "充电宝 Q10000 型号",
        "订单号 20261110123456789",
        "Node.js 教程纸质书",
        "喜欢的私聊",
        "增高鞋垫，内增高",
        "食品级硅胶",
        "电影特效素材盘",
        "打气枪",
    ],
)
def test_no_false_positives(text):
    assert check(text) == []


def test_positions_match_original_text():
    text = "全新！ＶＸ同号，最低价出"
    for hit in check(text):
        assert text[hit.start : hit.end] == hit.text
    assert normalize("ＶＸ㎏") == "vx㎏"  # 转出来不是一个字的（㎏ → kg）保持原样


def test_contained_hits_in_same_category_are_merged():
    # "1:1复刻" 包住了 "复刻"，只给一次；不同类的各给各的
    found = hits("1:1复刻，全网最低")
    assert ("复刻", "fake", "weak") not in found
    assert ("1:1复刻", "fake", "strong") in found
    assert ("全网最低", "absolute", "weak") in found


def test_hit_has_basis_and_suggestion():
    [hit] = check("成色完美")
    assert (hit.basis, hit.suggest) == ("law", "成色很好，没有明显瑕疵")
    assert hit.note and hit.category_name == "极限词"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[[rules]]\ncategory = "nope"\nwords = ["加我"]', "不认识的类别"),
        ('[[rules]]\ncategory = "c"\nwords = ["最"]', "不扫单字"),
        ('[[rules]]\ncategory = "c"\npattern = "a"', "要写 label"),
        ('[[rules]]\ncategory = "c"\nwords = ["加我"]\npattern = "a"', "只写一个"),
        ('[[rules]]\ncategory = "c"\nwords = ["加我"]\nlevel = "high"', "档位"),
        ('[[rules]]\ncategory = "c"\nwords = ["加我"]\ntypo = 1', "不认识的键"),
    ],
)
def test_bad_word_list_is_rejected(tmp_path, body, message):
    path = tmp_path / "words.toml"
    path.write_text(
        '[categories.c]\nname = "c"\nlevel = "weak"\nbasis = "law"\n' + body, encoding="utf-8"
    )
    with pytest.raises(ValueError, match=message):
        load_rules(path)

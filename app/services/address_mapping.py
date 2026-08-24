"""Address mapping helpers: province name -> state code, country -> ISO code."""
import logging
from typing import Dict, Optional

from sqlmodel import Session, select

from app.models.address_mapping import AddressMapping

logger = logging.getLogger(__name__)

# province-name lookup cache, loaded once per process (34 rows):
#   key -> state_code, where key covers the Chinese full name, the Chinese
#   short name (suffix stripped) and the English name (lower-cased)
_state_code_cache: Optional[Dict[str, str]] = None

_CN_SUFFIXES = ("省", "市", "自治区", "特别行政区", "维吾尔自治区", "回族自治区", "壮族自治区")


def _build_state_code_cache(session: Session) -> Dict[str, str]:
    cache: Dict[str, str] = {}
    for row in session.exec(select(AddressMapping).where(AddressMapping.is_active == True)).all():  # noqa: E712
        cache[row.state_desc] = row.state_code
        if row.state_name_en:
            cache[row.state_name_en.lower()] = row.state_code
        # short Chinese name: 浙江省 -> 浙江, 北京市 -> 北京
        short = row.state_desc
        for suffix in sorted(_CN_SUFFIXES, key=len, reverse=True):
            if short.endswith(suffix) and len(short) > len(suffix):
                short = short[: -len(suffix)]
                break
        cache[short] = row.state_code
    return cache


def get_state_code(session: Session, state_name: Optional[str]) -> Optional[str]:
    """
    Resolve a province/state name to its administrative division code.

    Matches the Chinese full name (浙江省), the Chinese short name (浙江)
    or the English name (Zhejiang / zhejiang, case-insensitive).

    Returns None when no mapping matches.
    """
    if not state_name:
        return None
    global _state_code_cache
    if _state_code_cache is None:
        try:
            _state_code_cache = _build_state_code_cache(session)
        except Exception as e:
            logger.error(f"[AddressMapping] Failed to load state code cache: {e}")
            return None
    return _state_code_cache.get(state_name) or _state_code_cache.get(state_name.lower())


# Common Chinese country/region names → ISO 3166-1 alpha-2 (pycountry only
# understands English/official names). Covers typical order destinations;
# unknown names still fall through unchanged.
_COUNTRY_ZH_TO_ALPHA2 = {
    "中国": "CN", "中国大陆": "CN", "中华人民共和国": "CN",
    "香港": "HK", "香港特别行政区": "HK",
    "澳门": "MO", "澳门特别行政区": "MO", "澳门行政区": "MO",
    "台湾": "TW", "台湾省": "TW", "中华台北": "TW",
    "美国": "US", "美利坚合众国": "US",
    "日本": "JP", "韩国": "KR", "南韩": "KR", "大韩民国": "KR",
    "朝鲜": "KP", "北朝鲜": "KP",
    "英国": "GB", "联合王国": "GB", "大不列颠及北爱尔兰联合王国": "GB",
    "法国": "FR", "德国": "DE", "意大利": "IT", "西班牙": "ES",
    "葡萄牙": "PT", "荷兰": "NL", "比利时": "BE", "卢森堡": "LU",
    "瑞士": "CH", "奥地利": "AT", "爱尔兰": "IE", "冰岛": "IS",
    "丹麦": "DK", "挪威": "NO", "瑞典": "SE", "芬兰": "FI",
    "波兰": "PL", "捷克": "CZ", "斯洛伐克": "SK", "匈牙利": "HU",
    "罗马尼亚": "RO", "保加利亚": "BG", "希腊": "GR", "土耳其": "TR",
    "俄罗斯": "RU", "俄国": "RU",
    "乌克兰": "UA", "白俄罗斯": "BY", "哈萨克斯坦": "KZ",
    "印度": "IN", "巴基斯坦": "PK", "孟加拉国": "BD", "斯里兰卡": "LK",
    "尼泊尔": "NP", "不丹": "BT", "马尔代夫": "MV",
    "泰国": "TH", "越南": "VN", "老挝": "LA", "柬埔寨": "KH",
    "缅甸": "MM", "马来西亚": "MY", "新加坡": "SG", "印度尼西亚": "ID",
    "印尼": "ID", "菲律宾": "PH", "文莱": "BN",
    "澳大利亚": "AU", "澳洲": "AU", "新西兰": "NZ",
    "加拿大": "CA", "墨西哥": "MX", "巴西": "BR", "阿根廷": "AR",
    "智利": "CL", "哥伦比亚": "CO", "秘鲁": "PE",
    "南非": "ZA", "埃及": "EG", "尼日利亚": "NG", "肯尼亚": "KE",
    "以色列": "IL", "阿联酋": "AE", "沙特阿拉伯": "SA", "沙特": "SA",
    "卡塔尔": "QA", "科威特": "KW", "巴林": "BH", "阿曼": "OM",
    "蒙古": "MN",
}


def to_iso_country_code(country: Optional[str]) -> Optional[str]:
    """
    Normalize a country value to the ISO 3166-1 alpha-2 code (upper-case).

    Lookup order: already-standard 2-letter codes pass through; Chinese
    names resolve via the built-in mapping; other names (English) resolve
    via pycountry; anything unresolvable is returned unchanged.
    """
    if not country:
        return country
    value = country.strip()
    # note: isalpha() alone is True for Chinese text (e.g. "中国"), so the
    # 2-letter pass-through must be restricted to ASCII letters
    if len(value) == 2 and value.isascii() and value.isalpha():
        return value.upper()
    if value in _COUNTRY_ZH_TO_ALPHA2:
        return _COUNTRY_ZH_TO_ALPHA2[value]
    try:
        import pycountry
        return pycountry.countries.lookup(value).alpha_2.upper()
    except Exception:
        return value

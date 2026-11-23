"""The site catalogue behind the web UI.

Every entry is a service a phone number can plausibly be attached to, grouped by
category. Two kinds of entry exist, and the UI never blurs them:

* **automated** (``probe`` set) — a working probe in :mod:`numtrail.modules`
  actually asks the service, and the UI shows a real status for it.
* **manual** (``probe`` empty) — the catalogue points you at the service's own
  sign-in / reset flow, and you check it yourself. There is no honest way to
  automate these: the endpoint is undocumented, captcha'd, or behind a login.
  Adding a URL that pretends otherwise would be fabrication, so we don't.

``lookup`` is a path relative to ``https://<domain>/`` where a human can start.
An empty path means "start at the site root".

Run ``python -m numtrail.catalog --write`` to regenerate the JSON the web app
serves, and ``python -m numtrail.catalog`` to see coverage by category.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, NamedTuple

__all__ = ["Site", "SITES", "as_dicts", "coverage", "write_json", "STATIC_DIR"]

STATIC_DIR = Path(__file__).parent / "web" / "static"


class Site(NamedTuple):
    id: str
    name: str
    domain: str
    category: str
    region: str
    lookup: str = ""     # path on the domain where a human checks manually
    probe: str = ""      # registered module name, if automated
    verified: bool = False
    note: str = ""


def _s(*rows) -> list[Site]:
    return [Site(*row) for row in rows]


SITES: list[Site] = _s(
    # ---------------------------------------------------------------- social
    ("facebook", "Facebook", "facebook.com", "social", "global", "login/identify"),
    ("instagram", "Instagram", "instagram.com", "social", "global", "", "instagram", True,
     "HMAC-signed account lookup"),
    ("snapchat", "Snapchat", "snapchat.com", "social", "global", "", "snapchat", True,
     "signup phone validator"),
    ("x", "X (Twitter)", "x.com", "social", "global", "account/begin_password_reset"),
    ("tiktok", "TikTok", "tiktok.com", "social", "global", "login/phone"),
    ("pinterest", "Pinterest", "pinterest.com", "social", "global", "password/reset"),
    ("reddit", "Reddit", "reddit.com", "social", "global", "login"),
    ("tumblr", "Tumblr", "tumblr.com", "social", "global", "forgot_password"),
    ("quora", "Quora", "quora.com", "social", "global"),
    ("flickr", "Flickr", "flickr.com", "social", "global"),
    ("imgur", "Imgur", "imgur.com", "social", "global"),
    ("9gag", "9GAG", "9gag.com", "social", "global"),
    ("vimeo", "Vimeo", "vimeo.com", "social", "global", "log_in"),
    ("threads", "Threads", "threads.net", "social", "global"),
    ("clubhouse", "Clubhouse", "clubhouse.com", "social", "global"),
    ("mewe", "MeWe", "mewe.com", "social", "global"),
    ("vk", "VK", "vk.com", "social", "ru", "restore"),
    ("okru", "OK.ru", "ok.ru", "social", "ru"),
    ("weibo", "Weibo", "weibo.com", "social", "cn"),
    ("douyin", "Douyin", "douyin.com", "social", "cn"),
    ("xing", "XING", "xing.com", "social", "eu"),

    # ------------------------------------------------------------- messaging
    ("whatsapp", "WhatsApp", "whatsapp.com", "messaging", "global"),
    ("telegram", "Telegram", "telegram.org", "messaging", "global"),
    ("signal", "Signal", "signal.org", "messaging", "global"),
    ("viber", "Viber", "viber.com", "messaging", "global"),
    ("wechat", "WeChat", "wechat.com", "messaging", "cn"),
    ("line", "LINE", "line.me", "messaging", "asia"),
    ("kakaotalk", "KakaoTalk", "kakaocorp.com", "messaging", "kr"),
    ("zalo", "Zalo", "zalo.me", "messaging", "vn"),
    ("imo", "imo", "imo.im", "messaging", "global"),
    ("threema", "Threema", "threema.ch", "messaging", "eu"),
    ("discord", "Discord", "discord.com", "messaging", "global"),
    ("groupme", "GroupMe", "groupme.com", "messaging", "global"),
    ("skype", "Skype", "skype.com", "messaging", "global"),

    # -------------------------------------------------------------- identity
    ("google", "Google", "accounts.google.com", "identity", "global",
     "signin/v2/identifier"),
    ("gmail", "Gmail", "mail.google.com", "identity", "global"),
    ("apple", "Apple ID", "appleid.apple.com", "identity", "global"),
    ("microsoft", "Microsoft", "login.live.com", "identity", "global"),
    ("outlook", "Outlook", "outlook.com", "identity", "global"),
    ("yahoo", "Yahoo", "login.yahoo.com", "identity", "global", "account/recovery"),
    ("aol", "AOL", "aol.com", "identity", "global"),
    ("yandex", "Yandex", "yandex.com", "identity", "ru"),
    ("mailru", "Mail.ru", "mail.ru", "identity", "ru"),
    ("zoho", "Zoho", "zoho.com", "identity", "global"),
    ("proton", "Proton Mail", "proton.me", "identity", "eu"),

    # -------------------------------------------------------------- shopping
    ("amazon", "Amazon", "amazon.com", "shopping", "global", "", "amazon", True,
     "/ax/claim sign-in flow"),
    ("ebay", "eBay", "ebay.com", "shopping", "global", "signin"),
    ("etsy", "Etsy", "etsy.com", "shopping", "global"),
    ("aliexpress", "AliExpress", "aliexpress.com", "shopping", "global"),
    ("alibaba", "Alibaba", "alibaba.com", "shopping", "global"),
    ("taobao", "Taobao", "taobao.com", "shopping", "cn"),
    ("jd", "JD.com", "jd.com", "shopping", "cn"),
    ("shopee", "Shopee", "shopee.com", "shopping", "asia"),
    ("lazada", "Lazada", "lazada.com", "shopping", "asia"),
    ("tokopedia", "Tokopedia", "tokopedia.com", "shopping", "id"),
    ("flipkart", "Flipkart", "flipkart.com", "shopping", "in"),
    ("myntra", "Myntra", "myntra.com", "shopping", "in"),
    ("meesho", "Meesho", "meesho.com", "shopping", "in"),
    ("snapdeal", "Snapdeal", "snapdeal.com", "shopping", "in"),
    ("wish", "Wish", "wish.com", "shopping", "global"),
    ("temu", "Temu", "temu.com", "shopping", "global"),
    ("shein", "SHEIN", "shein.com", "shopping", "global"),
    ("zalando", "Zalando", "zalando.com", "shopping", "eu"),
    ("asos", "ASOS", "asos.com", "shopping", "eu"),
    ("walmart", "Walmart", "walmart.com", "shopping", "us"),
    ("target", "Target", "target.com", "shopping", "us"),
    ("bestbuy", "Best Buy", "bestbuy.com", "shopping", "us"),
    ("ikea", "IKEA", "ikea.com", "shopping", "global"),
    ("rakuten", "Rakuten", "rakuten.co.jp", "shopping", "jp"),
    ("mercadolibre", "Mercado Libre", "mercadolibre.com", "shopping", "latam"),
    ("noon", "noon", "noon.com", "shopping", "mena"),
    ("jumia", "Jumia", "jumia.com", "shopping", "africa"),

    # --------------------------------------------------------------- finance
    ("paypal", "PayPal", "paypal.com", "finance", "global", "authflow"),
    ("venmo", "Venmo", "venmo.com", "finance", "us"),
    ("cashapp", "Cash App", "cash.app", "finance", "us"),
    ("revolut", "Revolut", "revolut.com", "finance", "eu"),
    ("wise", "Wise", "wise.com", "finance", "global"),
    ("coinbase", "Coinbase", "coinbase.com", "finance", "global"),
    ("binance", "Binance", "binance.com", "finance", "global"),
    ("kraken", "Kraken", "kraken.com", "finance", "global"),
    ("robinhood", "Robinhood", "robinhood.com", "finance", "us"),
    ("klarna", "Klarna", "klarna.com", "finance", "eu"),
    ("paytm", "Paytm", "paytm.com", "finance", "in"),
    ("phonepe", "PhonePe", "phonepe.com", "finance", "in"),
    ("gcash", "GCash", "gcash.com", "finance", "ph"),
    ("maya", "Maya", "maya.ph", "finance", "ph"),
    ("gopay", "GoPay", "gojek.com", "finance", "id"),
    ("ovo", "OVO", "ovo.id", "finance", "id"),
    ("dana", "DANA", "dana.id", "finance", "id"),
    ("nubank", "Nubank", "nubank.com.br", "finance", "br"),
    ("picpay", "PicPay", "picpay.com", "finance", "br"),
    ("alipay", "Alipay", "alipay.com", "finance", "cn"),
    ("touchngo", "Touch 'n Go eWallet", "tngdigital.com.my", "finance", "my"),
    ("boost", "Boost", "myboost.com.my", "finance", "my"),

    # ------------------------------------------------------------- transport
    ("uber", "Uber", "uber.com", "transport", "global"),
    ("lyft", "Lyft", "lyft.com", "transport", "us"),
    ("bolt", "Bolt", "bolt.eu", "transport", "eu"),
    ("grab", "Grab", "grab.com", "transport", "asia"),
    ("gojek", "Gojek", "gojek.com", "transport", "id"),
    ("ola", "Ola", "olacabs.com", "transport", "in"),
    ("didi", "DiDi", "didiglobal.com", "transport", "cn"),
    ("careem", "Careem", "careem.com", "transport", "mena"),
    ("indrive", "inDrive", "indrive.com", "transport", "global"),
    ("blablacar", "BlaBlaCar", "blablacar.com", "transport", "eu"),

    # ------------------------------------------------------------------ food
    ("doordash", "DoorDash", "doordash.com", "food", "us"),
    ("ubereats", "Uber Eats", "ubereats.com", "food", "global"),
    ("deliveroo", "Deliveroo", "deliveroo.co.uk", "food", "eu"),
    ("grubhub", "Grubhub", "grubhub.com", "food", "us"),
    ("foodpanda", "foodpanda", "foodpanda.com", "food", "asia"),
    ("zomato", "Zomato", "zomato.com", "food", "in"),
    ("swiggy", "Swiggy", "swiggy.com", "food", "in"),
    ("justeat", "Just Eat", "just-eat.co.uk", "food", "eu"),
    ("talabat", "Talabat", "talabat.com", "food", "mena"),
    ("meituan", "Meituan", "meituan.com", "food", "cn"),

    # ---------------------------------------------------------------- travel
    ("airbnb", "Airbnb", "airbnb.com", "travel", "global"),
    ("booking", "Booking.com", "booking.com", "travel", "global"),
    ("expedia", "Expedia", "expedia.com", "travel", "us"),
    ("agoda", "Agoda", "agoda.com", "travel", "asia"),
    ("tripadvisor", "Tripadvisor", "tripadvisor.com", "travel", "global"),
    ("makemytrip", "MakeMyTrip", "makemytrip.com", "travel", "in"),
    ("traveloka", "Traveloka", "traveloka.com", "travel", "asia"),
    ("tiket", "tiket.com", "tiket.com", "travel", "id"),

    # ---------------------------------------------------------------- gaming
    ("steam", "Steam", "steampowered.com", "gaming", "global"),
    ("epicgames", "Epic Games", "epicgames.com", "gaming", "global"),
    ("riotgames", "Riot Games", "riotgames.com", "gaming", "global"),
    ("ea", "EA", "ea.com", "gaming", "global"),
    ("battlenet", "Battle.net", "battle.net", "gaming", "global"),
    ("playstation", "PlayStation", "playstation.com", "gaming", "global"),
    ("xbox", "Xbox", "xbox.com", "gaming", "global"),
    ("nintendo", "Nintendo", "nintendo.com", "gaming", "global"),
    ("roblox", "Roblox", "roblox.com", "gaming", "global"),
    ("freefire", "Free Fire", "ff.garena.com", "gaming", "asia"),
    ("mobilelegends", "Mobile Legends", "mobilelegends.com", "gaming", "asia"),
    ("pubgmobile", "PUBG Mobile", "pubgmobile.com", "gaming", "global"),
    ("twitch", "Twitch", "twitch.tv", "gaming", "global"),

    # ---------------------------------------------------------------- dating
    ("tinder", "Tinder", "tinder.com", "dating", "global"),
    ("bumble", "Bumble", "bumble.com", "dating", "global"),
    ("hinge", "Hinge", "hinge.co", "dating", "global"),
    ("badoo", "Badoo", "badoo.com", "dating", "global"),
    ("okcupid", "OkCupid", "okcupid.com", "dating", "global"),
    ("pof", "Plenty of Fish", "pof.com", "dating", "global"),
    ("grindr", "Grindr", "grindr.com", "dating", "global"),
    ("happn", "happn", "happn.com", "dating", "eu"),

    # ---------------------------------------------------------- phone intel
    ("truecaller", "Truecaller", "truecaller.com", "intel", "global",
     "search/{iso_lower}/{digits}"),
    ("getcontact", "GetContact", "getcontact.com", "intel", "global"),
    ("syncme", "Sync.me", "sync.me", "intel", "global"),
    ("whoscall", "Whoscall", "whoscall.com", "intel", "asia"),
    ("hiya", "Hiya", "hiya.com", "intel", "global"),
    ("twilio", "Twilio Lookup", "twilio.com", "intel", "global", "", "twilio", True,
     "carrier + line type, needs a key"),
    ("numverify", "Numverify", "numverify.com", "intel", "global", "", "numverify", True,
     "validity + carrier, needs a key"),
    ("abstractapi", "AbstractAPI", "abstractapi.com", "intel", "global", "",
     "abstractapi", True, "validity + carrier, needs a key"),
    ("veriphone", "Veriphone", "veriphone.io", "intel", "global", "", "veriphone", True,
     "free tier available, needs a key"),
    ("ipqualityscore", "IPQualityScore", "ipqualityscore.com", "intel", "global", "",
     "ipqualityscore", False, "validity + fraud score, needs a key"),
    ("vonage", "Vonage Number Insight", "vonage.com", "intel", "global", "",
     "vonage", False, "country + format, needs a key pair"),
    ("telesign", "Telesign PhoneID", "telesign.com", "intel", "global", "",
     "telesign", False, "number type + carrier, needs a key pair"),
    ("plivo", "Plivo Lookup", "plivo.com", "intel", "global", "", "plivo", False,
     "carrier + country, needs a key pair"),
    ("bird", "Bird Lookup", "bird.com", "intel", "global", "", "bird", False,
     "carrier + line type, needs an access key"),
    ("numlookupapi", "numlookupapi", "numlookupapi.com", "intel", "global", "",
     "numlookupapi", False, "free tier available, needs a key"),
    ("trestle", "Trestle Phone Intel", "trestleiq.com", "intel", "global", "",
     "trestle", False, "validity + prepaid flag, needs a key"),
    ("mocean", "Mocean Number Lookup", "moceanapi.com", "intel", "global", "",
     "mocean", False, "carrier + reachability, needs a key pair"),
)

CATEGORIES = ("social", "messaging", "identity", "shopping", "finance",
              "transport", "food", "travel", "gaming", "dating", "intel")


def as_dicts() -> list[dict]:
    return [site._asdict() for site in SITES]


def coverage() -> dict:
    automated = [s for s in SITES if s.probe]
    by_category: dict[str, dict[str, int]] = {}
    for site in SITES:
        bucket = by_category.setdefault(site.category, {"total": 0, "automated": 0})
        bucket["total"] += 1
        bucket["automated"] += 1 if site.probe else 0
    return {
        "total": len(SITES),
        "automated": len(automated),
        "manual": len(SITES) - len(automated),
        "by_category": by_category,
    }


def write_json(path: Path | None = None) -> Path:
    target = path or (STATIC_DIR / "catalog.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_by": "numtrail.catalog",
        "categories": list(CATEGORIES),
        "coverage": coverage(),
        "sites": as_dicts(),
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="numtrail.catalog",
                                     description="Inspect or export the site catalogue")
    parser.add_argument("--write", action="store_true", help="write web/static/catalog.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    stats = coverage()
    print(f"{stats['total']} sites · {stats['automated']} automated · "
          f"{stats['manual']} manual (link-out)")
    print()
    for category, bucket in stats["by_category"].items():
        print(f"  {category:<10} {bucket['total']:>3} total   {bucket['automated']:>2} automated")
    if args.write:
        print(f"\nwrote {write_json()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

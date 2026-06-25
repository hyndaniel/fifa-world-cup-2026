import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import poly_fetch_hk as pf  # noqa: E402


def test_set_proxy_installs_proxyhandler():
    pf.set_proxy("http://127.0.0.1:7897")
    handlers = pf._OPENER.handlers
    phs = [h for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert phs, "应安装 ProxyHandler"
    assert phs[0].proxies.get("https") == "http://127.0.0.1:7897"


def test_set_proxy_none_keeps_default_opener():
    pf.set_proxy(None)
    assert pf._OPENER is not None  # 默认 opener(仍读环境代理)

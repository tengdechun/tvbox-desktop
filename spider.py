"""
爬虫引擎 —— 支持 Type0(JSON API) / Type4(JS) / Type1(JAR) 源
内置 JS 运行时, 优先使用 quickjs 包, 回退 Node.js
兼容 TVBox / CatVod 的 Spider 接口规范
"""

import json
import re
import subprocess
import os
import tempfile
import time
import base64
import hashlib
from typing import List, Optional, Dict, Any
from config import Site, VodItem, Category, FilterGroup

import requests


# ======== JS 运行时桥接代码 ========
# 提供 TVBox JS 源所需的全部全局 API: req / pdf / pd / html / Base64 / MD5 等

JS_BRIDGE = r'''
// ======== TVBox JS 桥接层 ========
var __results = {};

// HTTP 请求函数 (由 Python 注入实现)
function req(url, options) {
    options = options || {};
    options.method = options.method || "GET";
    options.headers = options.headers || {};
    options.data = options.data || null;
    // 调用 Python 端的 HTTP 请求
    var resp = __http_request(JSON.stringify({url: url, method: options.method, headers: options.headers, data: options.data}));
    return JSON.parse(resp);
}

// HTML 解析器 (简易实现, 使用正则)
function html(src) {
    return new HtmlDoc(src);
}

function HtmlDoc(src) {
    this.src = src;
    this.css = function(selector) {
        // 简化 CSS 选择器, 实际使用正则匹配
        return new HtmlNodes(this.src, selector);
    };
    this.text = function() { return this.src.replace(/<[^>]+>/g, ""); };
    this.html = function() { return this.src; };
    return this;
}

function HtmlNodes(src, selector) {
    this.nodes = [];
    // 简单解析: 提取 class/id 标签内容
    var pattern;
    if (selector && selector.startsWith(".")) {
        pattern = new RegExp('class="[^"]*' + selector.substring(1) + '[^"]*"[^>]*>([\\s\\S]*?)<\\/', 'gi');
    } else if (selector && selector.startsWith("#")) {
        pattern = new RegExp('id="' + selector.substring(1) + '"[^>]*>([\\s\\S]*?)<\\/', 'gi');
    } else if (selector) {
        pattern = new RegExp('<' + selector + '[^>]*>([\\s\\S]*?)<\\/' + selector + '>', 'gi');
    }
    if (pattern) {
        var match;
        while ((match = pattern.exec(src)) !== null) {
            this.nodes.push(new HtmlNode(match[1]));
        }
    }
    this.text = function() {
        return this.nodes.map(function(n) { return n.text(); }).join("");
    };
    this.html = function() {
        return this.nodes.map(function(n) { return n.html(); }).join("");
    };
    this.attr = function(name) {
        var result = [];
        var attrPattern = new RegExp(name + '="([^"]*)"', 'i');
        var match = attrPattern.exec(src);
        if (match) result.push(match[1]);
        return result.length > 0 ? result[0] : "";
    };
    return this;
}

function HtmlNode(src) {
    this.src = src;
    this.text = function() { return this.src.replace(/<[^>]+>/g, "").trim(); };
    this.html = function() { return this.src; };
    this.attr = function(name) {
        var pattern = new RegExp(name + '="([^"]*)"');
        var match = pattern.exec(this.src);
        return match ? match[1] : "";
    };
    return this;
}

// pdf (parse detail from) —— 从 HTML 中提取内容
function pdf(html, selector) {
    var doc = new HtmlDoc(html);
    return doc.css(selector);
}

// pd (parse detail) —— 简化版
function pd(html, selector) {
    var doc = new HtmlDoc(html);
    var nodes = doc.css(selector);
    return nodes.text();
}

// Base64 编解码
var Base64 = {
    encode: function(str) {
        return __base64_encode(str);
    },
    decode: function(str) {
        return __base64_decode(str);
    }
};

// MD5 哈希
function MD5(str) {
    return __md5(str);
}

// CryptoJS 简易实现 (AES/DES 等需要额外库)
var CryptoJS = {
    MD5: function(str) { return __md5(str); },
    enc: {
        Utf8: { stringify: function(s) { return s; }, parse: function(s) { return s; } },
        Base64: { stringify: function(s) { return __base64_encode(s); }, parse: function(s) { return __base64_decode(s); } },
        Hex: { stringify: function(s) { return s; }, parse: function(s) { return s; } }
    }
};

// JSON 工具 (内置)
// JSON.parse / JSON.stringify 已内置

// 工具函数
function redirectPrint(msg) {
    if (typeof msg === "object") msg = JSON.stringify(msg);
    __log(String(msg));
}

// 存储
var local = {
    get: function(key) { return __storage_get(key); },
    set: function(key, value) { __storage_set(key, String(value)); },
    delete: function(key) { __storage_delete(key); }
};
'''


class BaseSpider:
    """爬虫基类, 对应 CatVod Spider 接口"""

    def home_content(self, filter: list = None) -> dict:
        raise NotImplementedError

    def category_content(self, tid: str, pg: int = 1, filter: dict = None, extend: dict = None) -> dict:
        raise NotImplementedError

    def search_content(self, key: str, quick: bool = False, pg: int = 1) -> dict:
        raise NotImplementedError

    def detail_content(self, ids: list) -> dict:
        raise NotImplementedError

    def player_content(self, flag: str, id: str, vip_flags: list = None) -> dict:
        return {"parse": 0, "url": id, "header": ""}


class ApiSpider(BaseSpider):
    """Type 0 —— 标准 JSON API 源, 兼容 CMS 苹果 API
    增强: 请求重试 / Cookie管理 / 自定义请求头 / 超时处理
    """

    def __init__(self, site: Site):
        self.site = site
        self.api = site.api
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        # 从站点配置加载自定义请求头
        if site.ext:
            try:
                ext = json.loads(site.ext) if isinstance(site.ext, str) else site.ext
                if isinstance(ext, dict):
                    headers = ext.get("headers", {})
                    if isinstance(headers, dict):
                        self.session.headers.update(headers)
            except Exception:
                pass

        self.categories: List[Category] = []
        self.filters: Dict[str, list] = {}
        self._max_retries = 3
        self._retry_delay = 1

    def _request_with_retry(self, method: str, params: dict) -> dict:
        """带重试的请求"""
        import time as _time
        last_error = None

        for attempt in range(self._max_retries):
            try:
                if method == "GET":
                    resp = self.session.get(self.api, params=params, timeout=15)
                else:
                    resp = self.session.post(self.api, data=params, timeout=15)

                resp.encoding = resp.apparent_encoding

                # 检查状态码
                if resp.status_code == 429:
                    # 限流, 等待更长时间
                    _time.sleep(self._retry_delay * (attempt + 2))
                    continue

                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < self._max_retries - 1:
                        _time.sleep(self._retry_delay * (attempt + 1))
                    continue

                # 尝试 JSON 解析
                try:
                    data = resp.json()
                    return data if isinstance(data, dict) else {}
                except Exception:
                    # 可能返回的是非 JSON 数据
                    text = resp.text.strip()
                    if text:
                        # 尝试从文本中提取 JSON
                        try:
                            import re as _re
                            json_match = _re.search(r'\{[\s\S]*\}', text)
                            if json_match:
                                return json.loads(json_match.group())
                        except Exception:
                            pass
                    last_error = "响应不是有效 JSON"
                    if attempt < self._max_retries - 1:
                        _time.sleep(self._retry_delay * (attempt + 1))

            except requests.Timeout:
                last_error = "请求超时"
                if attempt < self._max_retries - 1:
                    _time.sleep(self._retry_delay * (attempt + 1))
            except requests.ConnectionError:
                last_error = "连接失败"
                if attempt < self._max_retries - 1:
                    _time.sleep(self._retry_delay * (attempt + 1))
            except Exception as e:
                last_error = str(e)
                if attempt < self._max_retries - 1:
                    _time.sleep(self._retry_delay * (attempt + 1))

        print(f"[ApiSpider] 请求失败 {self.api} (重试{self._max_retries}次): {last_error}")
        return {}

    def _get(self, params: dict) -> dict:
        return self._request_with_retry("GET", params)

    def _post(self, params: dict) -> dict:
        return self._request_with_retry("POST", params)

    def _parse_list(self, data: dict) -> tuple:
        items = []
        for v in data.get("list", []):
            items.append(VodItem(
                vod_id=str(v.get("vod_id", "")),
                vod_name=v.get("vod_name", ""),
                vod_pic=v.get("vod_pic", ""),
                vod_remarks=v.get("vod_remarks", ""),
                type_id=str(v.get("type_id", "")),
                type_name=v.get("type_name", ""),
                vod_year=v.get("vod_year", ""),
                vod_area=v.get("vod_area", ""),
            ).to_dict())
        page = data.get("page", 1)
        pagecount = data.get("pagecount", 1)
        limit = data.get("limit", 20)
        total = data.get("total", 0)
        return items, page, pagecount, limit, total

    def home_content(self, filter: list = None) -> dict:
        data = self._get({"ac": "list"})
        cats = []
        for c in data.get("class", []):
            cats.append({"type_id": str(c.get("type_id", "")),
                         "type_name": c.get("type_name", "")})
        self.categories = [Category(c["type_id"], c["type_name"]) for c in cats]

        home_data = self._get({"ac": "detail", "pg": 1})
        items, page, pagecount, limit, total = self._parse_list(home_data)

        # 解析筛选器
        filters = {}
        for f in data.get("filters", []):
            if isinstance(f, dict):
                filters[str(f.get("key", ""))] = f.get("value", [])

        # 也支持 CatVod 的 filters 格式
        cat_filters = data.get("filters", {})
        if isinstance(cat_filters, dict):
            for k, v in cat_filters.items():
                filters[str(k)] = v

        return {"categories": cats, "list": items, "filters": filters}

    def category_content(self, tid: str, pg: int = 1, filter: dict = None, extend: dict = None) -> dict:
        params = {"ac": "detail", "pg": pg, "t": tid}
        if extend:
            params.update(extend)
        data = self._get(params)
        items, page, pagecount, limit, total = self._parse_list(data)
        return {"list": items, "page": page, "pagecount": pagecount,
                "limit": limit, "total": total}

    def search_content(self, key: str, quick: bool = False, pg: int = 1) -> dict:
        params = {"wd": key, "pg": pg}
        if not quick:
            params["ac"] = "detail"
        data = self._get(params)
        items, page, pagecount, limit, total = self._parse_list(data)
        return {"list": items, "page": page, "pagecount": pagecount}

    def detail_content(self, ids: list) -> dict:
        data = self._get({"ac": "detail", "ids": ",".join(ids)})
        items = []
        for v in data.get("list", []):
            items.append(VodItem(
                vod_id=str(v.get("vod_id", "")),
                vod_name=v.get("vod_name", ""),
                vod_pic=v.get("vod_pic", ""),
                vod_remarks=v.get("vod_remarks", ""),
                type_name=v.get("type_name", ""),
                vod_year=v.get("vod_year", ""),
                vod_area=v.get("vod_area", ""),
                vod_actor=v.get("vod_actor", ""),
                vod_director=v.get("vod_director", ""),
                vod_content=v.get("vod_content", ""),
                vod_play_from=v.get("vod_play_from", ""),
                vod_play_url=v.get("vod_play_url", ""),
                vod_score=v.get("vod_score", ""),
            ).to_dict())
        return {"list": items}

    def player_content(self, flag: str, id: str, vip_flags: list = None) -> dict:
        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"}
        # 如果是 VIP 内容, 标记需要解析
        parse_flag = 0
        if vip_flags and flag in vip_flags:
            parse_flag = 1
        return {"parse": parse_flag, "url": id, "header": headers, "flag": flag}


class JsSpider(BaseSpider):
    """Type 4 —— JavaScript 源
    优先使用 quickjs 包 (内置, 无需外部依赖)
    回退到 Node.js (需要安装)
    """

    def __init__(self, site: Site):
        self.site = site
        self.api = site.api
        self.ext = site.ext if site.ext else ""
        self._js_code: Optional[str] = None
        self._quickjs = None
        self._node_available = self._check_node()
        self._runtime = self._detect_runtime()

    def _detect_runtime(self) -> str:
        """检测可用的 JS 运行时: quickjs > node > none"""
        try:
            import quickjs
            self._quickjs = quickjs
            return "quickjs"
        except ImportError:
            pass
        if self._node_available:
            return "node"
        return "none"

    def _check_node(self) -> bool:
        try:
            result = subprocess.run(["node", "--version"], capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def _ensure_loaded(self):
        """下载 JS 源代码"""
        if self._js_code is not None:
            return
        try:
            # JS 源可能是 URL 或内联代码
            if self.api.startswith("http"):
                resp = requests.get(self.api, timeout=15,
                                    headers={"User-Agent": "Mozilla/5.0"})
                self._js_code = resp.text
            else:
                self._js_code = self.api
        except Exception as e:
            self._js_code = ""
            print(f"[JsSpider] 加载 JS 源失败: {e}")

    def _run_quickjs(self, cmd: str, args: dict) -> dict:
        """使用 quickjs 包执行 JS"""
        self._ensure_loaded()
        if not self._js_code:
            return {"error": "JS 源加载失败"}

        try:
            ctx = self._quickjs.Context()

            # 注入桥接函数
            ctx.eval(JS_BRIDGE)

            # 注入 Python 桥接函数
            ctx.add_callable("__http_request", self._js_http_request)
            ctx.add_callable("__base64_encode", lambda s: base64.b64encode(s.encode()).decode())
            ctx.add_callable("__base64_decode", lambda s: base64.b64decode(s).decode())
            ctx.add_callable("__md5", lambda s: hashlib.md5(s.encode()).hexdigest())
            ctx.add_callable("__log", lambda s: print(f"[JS] {s}"))
            ctx.add_callable("__storage_get", lambda k: "")  # 简化存储
            ctx.add_callable("__storage_set", lambda k, v: None)
            ctx.add_callable("__storage_delete", lambda k: None)

            # 加载 spider 代码
            ctx.eval(self._js_code)

            # 执行命令
            js_cmd = f"""
                var __args = {json.dumps(args)};
                var __result;
                switch("{cmd}") {{
                    case "homeContent":
                        __result = homeContent(__args.filter || []);
                        break;
                    case "categoryContent":
                        __result = categoryContent(__args.tid, __args.pg || 1, __args.filter || {{}}, __args.extend || {{}});
                        break;
                    case "searchContent":
                        __result = searchContent(__args.key, __args.quick || false, __args.pg || 1);
                        break;
                    case "detailContent":
                        __result = detailContent(__args.ids || []);
                        break;
                    case "playerContent":
                        __result = playerContent(__args.flag || "", __args.id || "", __args.vipFlags || []);
                        break;
                    default:
                        __result = {{error: "unknown command: " + "{cmd}"}};
                }}
                JSON.stringify(__result);
            """
            result = ctx.eval(js_cmd)
            return json.loads(result) if result else {"error": "空返回"}

        except Exception as e:
            return {"error": f"quickjs 执行错误: {e}"}

    def _js_http_request(self, params_json: str) -> str:
        """JS 桥接: HTTP 请求"""
        try:
            params = json.loads(params_json)
            url = params.get("url", "")
            method = params.get("method", "GET").upper()
            headers = params.get("headers", {})
            data = params.get("data")

            resp = requests.request(
                method, url, headers=headers, data=data, timeout=15
            )
            resp.encoding = resp.apparent_encoding
            return json.dumps({
                "body": resp.text,
                "headers": dict(resp.headers),
                "statusCode": resp.status_code,
                "url": resp.url,
            })
        except Exception as e:
            return json.dumps({"error": str(e), "body": "", "statusCode": 0})

    def _run_node(self, cmd: str, args: dict) -> dict:
        """使用 Node.js 执行 JS (回退方案)"""
        self._ensure_loaded()
        if not self._js_code:
            return {"error": "JS 源加载失败"}

        runner = JS_BRIDGE + "\n" + self._js_code + f"""

// 执行命令
var __args = {json.dumps(args)};
var __result;
try {{
    switch("{cmd}") {{
        case "homeContent":
            __result = homeContent(__args.filter || []);
            break;
        case "categoryContent":
            __result = categoryContent(__args.tid, __args.pg || 1, __args.filter || {{}}, __args.extend || {{}});
            break;
        case "searchContent":
            __result = searchContent(__args.key, __args.quick || false, __args.pg || 1);
            break;
        case "detailContent":
            __result = detailContent(__args.ids || []);
            break;
        case "playerContent":
            __result = playerContent(__args.flag || "", __args.id || "", __args.vipFlags || []);
            break;
        default:
            __result = {{error: "unknown: {cmd}"}};
    }}
}} catch(e) {{
    __result = {{error: String(e)}};
}}
process.stdout.write(JSON.stringify(__result));
"""

        # 写入临时文件
        fd, tmp_path = tempfile.mkstemp(suffix=".js", prefix="tvbox_js_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(runner)

            # 添加 Python 桥接的 Node.js 实现
            bridge_path = self._write_node_bridge()

            result = subprocess.run(
                ["node", "-e", """
                const { execSync } = require('child_process');
                global.__http_request = function(jsonStr) {
                    const params = JSON.parse(jsonStr);
                    try {
                        const reqData = JSON.stringify(params);
                        const result = execSync('python3 -c "import sys,json,requests; p=json.loads(sys.argv[1]); r=requests.request(p.get(\'method\',\'GET\'),p[\'url\'],headers=p.get(\'headers\',{}),data=p.get(\'data\')); print(json.dumps({\'body\':r.text,\'statusCode\':r.status_code,\'headers\':dict(r.headers),\'url\':r.url}))" ' + reqData.replace(/'/g, "'\\\\''")).toString();
                        return result;
                    } catch(e) {
                        return JSON.stringify({error: String(e), body: ''});
                    }
                };
                global.__base64_encode = function(s) { return Buffer.from(s).toString('base64'); };
                global.__base64_decode = function(s) { return Buffer.from(s, 'base64').toString(); };
                global.__md5 = function(s) { return require('crypto').createHash('md5').update(s).digest('hex'); };
                global.__log = function(s) { process.stderr.write(s + '\\n'); };
                global.__storage_get = function(k) { return ''; };
                global.__storage_set = function(k,v) {};
                global.__storage_delete = function(k) {};
                require('__TMP_PATH__');
                """.replace('__TMP_PATH__', tmp_path)],
                capture_output=True, timeout=30, text=True
            )

            if result.returncode != 0:
                return {"error": f"Node.js 执行错误: {result.stderr[:500]}"}
            return json.loads(result.stdout) if result.stdout else {"error": "空返回"}

        except json.JSONDecodeError as e:
            return {"error": f"JSON 解析失败: {e}"}
        except subprocess.TimeoutExpired:
            return {"error": "JS 执行超时"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _write_node_bridge(self) -> str:
        """写入 Node.js 桥接模块"""
        fd, path = tempfile.mkstemp(suffix=".js", prefix="tvbox_bridge_")
        with os.fdopen(fd, "w") as f:
            f.write(JS_BRIDGE)
        return path

    def _run(self, cmd: str, args: dict) -> dict:
        """执行 JS 命令"""
        if self._runtime == "quickjs":
            return self._run_quickjs(cmd, args)
        elif self._runtime == "node":
            return self._run_node(cmd, args)
        else:
            return {"error": "无可用 JS 运行时, 请安装 quickjs (pip install quickjs) 或 Node.js"}

    def home_content(self, filter: list = None) -> dict:
        return self._run("homeContent", {"filter": filter or []})

    def category_content(self, tid: str, pg: int = 1, filter: dict = None, extend: dict = None) -> dict:
        return self._run("categoryContent", {"tid": tid, "pg": pg, "filter": filter, "extend": extend})

    def search_content(self, key: str, quick: bool = False, pg: int = 1) -> dict:
        return self._run("searchContent", {"key": key, "quick": quick, "pg": pg})

    def detail_content(self, ids: list) -> dict:
        return self._run("detailContent", {"ids": ids})

    def player_content(self, flag: str, id: str, vip_flags: list = None) -> dict:
        return self._run("playerContent", {"flag": flag, "id": id, "vipFlags": vip_flags or []})


class JarSpider(BaseSpider):
    """Type 1 —— JAR 源 (不支持, 桌面端无法运行 Dalvik 字节码)"""

    def __init__(self, site: Site):
        self.site = site

    def home_content(self, filter: list = None) -> dict:
        return {"error": "JAR 源(Type1)不支持桌面端, 请使用 API(Type0)或 JS(Type4)源"}

    def category_content(self, tid: str, pg: int = 1, filter: dict = None, extend: dict = None) -> dict:
        return {"error": "JAR 源不支持"}

    def search_content(self, key: str, quick: bool = False, pg: int = 1) -> dict:
        return {"error": "JAR 源不支持"}

    def detail_content(self, ids: list) -> dict:
        return {"error": "JAR 源不支持"}


class SpiderManager:
    """爬虫管理器"""

    def __init__(self):
        self._spiders: Dict[str, BaseSpider] = {}

    def get_spider(self, site: Site) -> BaseSpider:
        if site.key in self._spiders:
            return self._spiders[site.key]

        if site.type == 0:
            spider = ApiSpider(site)
        elif site.type == 4:
            spider = JsSpider(site)
        elif site.type == 1:
            spider = JarSpider(site)
        elif site.type == 3:
            spider = JarSpider(site)
        else:
            spider = ApiSpider(site)

        self._spiders[site.key] = spider
        return spider

    def home_content(self, site: Site) -> dict:
        return self.get_spider(site).home_content()

    def category_content(self, site: Site, tid: str, pg: int = 1, extend: dict = None) -> dict:
        return self.get_spider(site).category_content(tid, pg, extend=extend)

    def search_content(self, site: Site, key: str, pg: int = 1) -> dict:
        return self.get_spider(site).search_content(key, pg=pg)

    def detail_content(self, site: Site, ids: list) -> dict:
        return self.get_spider(site).detail_content(ids)

    def player_content(self, site: Site, flag: str, vid: str) -> dict:
        return self.get_spider(site).player_content(flag, vid)

    def search_all(self, sites: List[Site], key: str) -> List[dict]:
        """搜索多站点"""
        import concurrent.futures
        results = []
        searchable = [s for s in sites if s.searchable == 1]

        def search_one(site):
            try:
                data = self.search_content(site, key)
                items = data.get("list", [])
                for item in items:
                    item["site_key"] = site.key
                    item["site_name"] = site.name
                if items:
                    return {
                        "site_key": site.key,
                        "site_name": site.name,
                        "list": items,
                    }
            except Exception as e:
                print(f"[搜索] {site.name} 失败: {e}")
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(search_one, s): s for s in searchable}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        return results

    def clear_cache(self):
        """清除缓存的 spider 实例"""
        self._spiders.clear()

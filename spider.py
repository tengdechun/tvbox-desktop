"""
爬虫引擎 —— 支持 Type0(JSON API) / Type4(JS) / Type1(JAR) 源
内置 JS 运行时, 优先使用 quickjs 包, 回退 Node.js
兼容 TVBox / CatVod 的 Spider 接口规范
完整复刻 FongMi/TV 的 Spider 桥接 API
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


# ======== 繁体转简体映射表 (常用字) ========

T2S_MAP = {
    "東": "东", "東": "东", "車": "车", "馬": "马", "龍": "龙",
    "鳳": "凤", "龜": "龟", "華": "华", "國": "国", "學": "学",
    "業": "业", "葉": "叶", "電": "电", "廣": "广", "慶": "庆",
    "會": "会", "開": "开", "關": "关", "見": "见", "觀": "观",
    "讓": "让", "說": "说", "話": "话", "請": "请", "語": "语",
    "讀": "读", "誰": "谁", "產": "产", "農": "农", "醫": "医",
    "網": "网", "萬": "万", "來": "来", "兩": "两", "個": "个",
    "們": "们", "這": "这", "那": "那", "裡": "里", "還": "还",
    "進": "进", "過": "过", "運": "运", "回": "回", "給": "给",
    "從": "从", "為": "为", "對": "对", "於": "于", "與": "与",
    "員": "员", "動": "动", "場": "场", "處": "处", "帶": "带",
    "後": "后", "現": "现", "環": "环", "靜": "静", "經": "经",
    "練": "练", "紀": "纪", "記": "记", "論": "论", "認": "认",
    "證": "证", "識": "识", "選": "选", "選": "选", "錢": "钱",
    "鐵": "铁", "銀": "银", "銅": "铜", "錄": "录", "針": "针",
    "鐘": "钟", "鏡": "镜", "驗": "验", "驚": "惊", "驅": "驱",
    "驟": "骤", "體": "体", "髮": "发", "鬚": "须", "鳥": "鸟",
    "雞": "鸡", "鴨": "鸭", "魚": "鱼", "蝦": "虾", "蟲": "虫",
    "蜂": "蜂", "螢": "萤", "蛇": "蛇", "貝": "贝", "牛": "牛",
    "豬": "猪", "貓": "猫", "狗": "狗", "獅": "狮", "虎": "虎",
    "兔": "兔", "龍": "龙", "蛇": "蛇", "馬": "马", "羊": "羊",
    "猴": "猴", "雞": "鸡", "狗": "狗", "豬": "猪",
    "廟": "庙", "廠": "厂", "廣": "广", "廳": "厅", "庫": "库",
    "應": "应", "態": "态", "憶": "忆", "懷": "怀", "懶": "懒",
    "戲": "戏", "護": "护", "擔": "担", "擇": "择", "擊": "击",
    "損": "损", "搶": "抢", "換": "换", "權": "权", "歡": "欢",
    "歐": "欧", "齒": "齿", "齡": "龄", "龍": "龙", "龜": "龟",
    "優": "优", "儲": "储", "償": "偿", "側": "侧", "偵": "侦",
    "傳": "传", "傷": "伤", "傾": "倾", "僅": "仅", "像": "像",
    "價": "价", "儀": "仪", "億": "亿", "兩": "两", "免": "免",
    "冊": "册", "軍": "军", "農": "农", "冠": "冠", "冬": "冬",
    "冰": "冰", "衝": "冲", "決": "决", "淨": "净", "涼": "凉",
    "減": "减", "湊": "凑", "準": "准", "凝": "凝", "几": "几",
    "鳳": "凤", "擊": "击", "劃": "划", "劇": "剧", "剩": "剩",
    "務": "务", "勝": "胜", "勞": "劳", "募": "募", "勢": "势",
    "勤": "勤", "區": "区", "醫": "医", "卉": "卉", "單": "单",
    "賣": "卖", "佔": "占", "卡": "卡", "衛": "卫", "壓": "压",
    "縣": "县", "參": "参", "發": "发", "變": "变", "疊": "叠",
    "葉": "叶", "號": "号", "詠": "咏", "響": "响", "喬": "乔",
    "單": "单", "嗶": "哔", "圖": "图", "團": "团", "園": "园",
    "圓": "圆", "圍": "围", "國": "国", "聖": "圣", "堅": "坚",
    "場": "场", "堤": "堤", "塊": "块", "壇": "坛", "壢": "坜",
    "壩": "坝", "塢": "坞", "墳": "坟", "壓": "压", "壘": "垒",
    "墾": "垦", "壚": "垆", "壺": "壶", "壹": "壹", "壽": "寿",
    "夾": "夹", "奧": "奥", "妝": "妆", "妳": "你", "婁": "娄",
    "媧": "娲", "嫻": "娴", "嬰": "婴", "嫿": "婳", "嬋": "婵",
    "嬡": "嫒", "寧": "宁", "寶": "宝", "實": "实", "審": "审",
    "宮": "宫", "宰": "宰", "害": "害", "宴": "宴", "宵": "宵",
    "家": "家", "容": "容", "寬": "宽", "賓": "宾", "宿": "宿",
    "寂": "寂", "寄": "寄", "密": "密", "富": "富", "寒": "寒",
    "察": "察", "寡": "寡", "寨": "寨", "審": "审", "寫": "写",
    "屍": "尸", "屆": "届", "層": "层", "屢": "屡", "屜": "屉",
    "屬": "属", "屢": "屡", "岡": "冈", "岡": "岢", "島": "岛",
    "嶺": "岭", "嶽": "岳", "嶠": "峤", "嶢": "峣", "嶧": "峱",
    "巒": "峦", "嶸": "嵘", "巔": "巅", "巰": "巯", "幣": "币",
    "幹": "干", "幺": "幺", "幾": "几", "庫": "库", "應": "应",
    "廂": "厢", "廄": "厩", "廈": "厦", "廚": "厨", "廠": "厂",
    "廝": "厮", "廟": "庙", "廠": "厂", "廢": "废", "廡": "庑",
    "廣": "广", "廠": "厂", "廳": "厅", "廢": "废", "弒": "弑",
    " 強": "强", "歸": "归", "結": "结", "絕": "绝", "繼": "继",
    "維": "维", "綱": "纲", "網": "网", "綢": "绸", "綜": "综",
    "綻": "绽", "綠": "绿", "綴": "缀", "網": "网", "綸": "纶",
    "綺": "绮", "綉": "绣", "綫": "线", "緣": "缘", "緊": "紧",
    "緩": "缓", "締": "缔", "緯": "纬", "緝": "缉", "緒": "绪",
    "練": "练", "線": "线", "緻": "致", "總": "总", "縣": "县",
    "縫": "缝", "縮": "缩", "縱": "纵", "縷": "缕", "總": "总",
    "繃": "绷", "織": "织", "繕": "缮", "繚": "缭", "繞": "绕",
    "繡": "绣", "繩": "绳", "繪": "绘", "繫": "系", "繭": "茧",
    "繯": "缳", "繮": "缰", "繳": "缴", "繹": "绎", "羅": "罗",
    "罰": "罚", "罵": "骂", "罷": "罢", "羅": "罗", "罰": "罚",
    "罳": "罳", "罵": "骂", "罷": "罢", "羆": "罴", "義": "义",
    "習": "习", "翹": "翘", "耬": "耧", "耮": "耢", "聯": "联",
    "聰": "聪", "聲": "声", "聳": "耸", "聵": "聩", "聶": "聂",
    "職": "职", "聹": "聍", "聽": "听", "聾": "聋", "肅": "肃",
    "膚": "肤", "肺": "肺", "腎": "肾", "腫": "肿", "腸": "肠",
    "胃": "胃", "膽": "胆", "膿": "脓", "肢": "肢", "脾": "脾",
    "腔": "腔", "腰": "腰", "腸": "肠", "膚": "肤", "膠": "胶",
    "膩": "腻", "膽": "胆", "膿": "脓", "臉": "脸", "臍": "脐",
    "臏": "膑", "臕": "膘", "臘": "腊", "臚": "胪", "臟": "脏",
    "臠": "脔", "臺": "台", "與": "与", "舊": "旧", "舍": "舍",
    "艦": "舰", "艙": "舱", "艱": "艰", "艷": "艳", "芻": "刍",
    "苧": "苎", "苒": "苒", "芳": "芳", "芻": "刍", "芬": "芬",
    "芮": "芮", "芯": "芯", "花": "花", "芳": "芳", "芷": "芷",
    "芸": "芸", "芹": "芹", "芻": "刍", "芽": "芽", "苑": "苑",
    "蒼": "苍", "蓋": "盖", "蓮": "莲", "蓯": "苁", "蓴": "莼",
    "蓽": "荜", "蔘": "参", "蔞": "蒌", "蔣": "蒋", "蔥": "葱",
    "蔚": "蔚", "蔽": "蔽", "蕭": "萧", "薩": "萨", "蕪": "芜",
    "薑": "姜", "薈": "荟", "薊": "蓟", "薌": "芗", "薔": "蔷",
    "藎": "荩", "藝": "艺", "藥": "药", "藪": "薮", "藜": "藜",
    "藹": "蔼", "藺": "蔺", "蘆": "芦", "蘇": "苏", "蘊": "蕴",
    "蘋": "苹", "蘚": "藓", "蘞": "蔹", "蘢": "茏", "蘭": "兰",
    "蘺": "蓠", "蘿": "萝", "處": "处", "虛": "虚", "虜": "虏",
    "虞": "虞", "號": "号", "蛻": "蜕", "蝸": "蜗", "蝕": "蚀",
    "螢": "萤", "螞": "蚂", "螢": "萤", "蟲": "虫", "蟣": "虮",
    "蟬": "蝉", "蟯": "蛲", "蟲": "虫", "蟹": "蟹", "蟾": "蟾",
    "膵": "膵", "蠅": "蝇", "蠍": "蝎", "蠑": "蝾", "蠟": "蜡",
    "蠣": "蛎", "蠨": "蟏", "蠱": "蛊", "蠶": "蚕", "蠻": "蛮",
    "眾": "众", "瞿": "瞿", "碼": "码", "磑": "碨", "磚": "砖",
    "磣": "碜", "磧": "碛", "磯": "矶", "磽": "硗", "礄": "硚",
    "礎": "础", "礙": "碍", "礦": "矿", "礪": "砺", "礫": "砾",
    "Sac": "Sac", "祿": "禄", "禎": "祯", "禰": "祢", "禱": "祷",
    "離": "离", "種": "种", "稱": "称", "稅": "税", "稈": "秆",
    "稟": "禀", "稠": "稠", "稱": "称", "種": "种", "稅": "税",
    "積": "积", "稱": "称", "穀": "谷", "穋": "稑", "穡": "穑",
    "穢": "秽", "穩": "稳", "穫": "获", "穭": "稆", "穹": "穹",
    "空": "空", "穿": "穿", "突": "突", "竊": "窃", "竇": "窦",
    "竅": "窍", "笈": "笈", "笪": "笪", "笳": "笳", "笵": "范",
    "笻": "筇", "筆": "笔", "筍": "笋", "筏": "筏", "筐": "筐",
    "築": "筑", "答": "答", "策": "策", "簡": "简", "箇": "个",
    "箋": "笺", "箏": "筝", "箏": "筝", "箔": "箔", "箕": "箕",
    "箝": "箝", "管": "管", "箴": "箴", "範": "范", "篆": "篆",
    "篇": "篇", "築": "筑", "篋": "箧", "篔": "筼", "篝": "篝",
    "篠": "筱", "篤": "笃", "篩": "筛", "篪": "篪", "篩": "筛",
    "篰": "篰", "篲": "彗", "篳": "筚", "篶": "篶", "篸": "簪",
    "篹": "纂", "篺": "篺", "篻": "篻", "篼": "篼", "篽": "篽",
    "篾": "篾", "篿": "篿", "簇": "簇", "簀": "箦", "簁": "筛",
    "簃": "簃", "簄": "簄", "簅": "簅", "簆": "簆", "簇": "簇",
    "簈": "簈", "簉": "簉", "簊": "簊", "簋": "簋", "簌": "簌",
    "簍": "篓", "簎": "簎", "簏": "簏", "簐": "簐", "簑": "蓑",
    "簒": "篡", "簓": "簓", "簔": "蓑", "簕": "簕", "簖": "签",
    "簗": "簗", "簘": "箫", "簙": "博", "簚": "簚", "簛": "簛",
    "簜": "簜", "簝": "簝", "簞": "簞", "簟": "簟", "簠": "簠",
    "簡": "简", "簢": "簢", "簣": "箧", "簤": "簤", "簥": "簥",
    "簦": "簦", "簧": "簧", "簨": "簨", "簩": "簩", "簪": "簪",
    "簫": "箫", "簬": "簬", "簭": "簭", "簮": "簪", "簯": "簯",
    "簰": "簰", "簱": "簱", "簲": "簲", "簳": "簳", "簴": "簴",
    "簵": "簵", "簶": "簶", "簷": "檐", "簸": "簸", "簹": "簹",
    "簺": "簺", "簻": "簻", "簼": "簼", "簽": "签", "簾": "帘",
    "籀": "籀", "籁": "籁", "籂": "籂", "籃": "篮", "籄": "籄",
    "籅": "籅", "籆": "籆", "籇": "籇", "籈": "籈", "籉": "籉",
    "籊": "籊", "籋": "籋", "籌": "筹", "籍": "籍", "籎": "籎",
    "籏": "籏", "籐": "藤", "籑": "纂", "籒": "籒", "籓": "籓",
    "籔": "籔", "籕": "籕", "籖": "籖", "籗": "籗", "籘": "籘",
    "籙": "籙", "籚": "籚", "籛": "籛", "籜": "籜", "籝": "籝",
    "籞": "籞", "籟": "籁", "籠": "笼", "籡": "籡", "籢": "籢",
    "籣": "籣", "籤": "签", "籥": "龠", "籦": "籦", "籧": "籧",
    "籨": "籨", "籩": "笾", "籪": "籪", "籫": "籫", "籬": "篱",
    "籭": "籭", "籮": "箩", "籯": "籯", "籰": "籰", "籱": "籱",
    "籲": "吁",
}


def t2s(text: str) -> str:
    """繁体转简体 (简单映射表实现)"""
    if not text:
        return text
    result = []
    for ch in text:
        result.append(T2S_MAP.get(ch, ch))
    return "".join(result)


# ======== 播放集数解析 ========

def parse_play_url(vod_play_from: str, vod_play_url: str) -> List[dict]:
    """解析播放地址
    vod_play_from: 线路名称, 用 $$$ 分隔多个线路
                   如 "线路1$$$线路2$$$线路3"
    vod_play_url: 播放地址, 用 $$$ 分隔线路, 用 # 分隔集数, 用 $ 分隔名称和URL
                  如 "第1集$url1#第2集$url2$$$第1集$url3#第2集$url4"

    返回: [
        {"from": "线路1", "episodes": [{"name": "第1集", "url": "url1"}, ...]},
        {"from": "线路2", "episodes": [{"name": "第1集", "url": "url3"}, ...]},
    ]
    集数倒序排序 (< 300 集时)
    """
    if not vod_play_from and not vod_play_url:
        return []

    from_list = vod_play_from.split("$$$") if vod_play_from else []
    url_parts = vod_play_url.split("$$$") if vod_play_url else []

    # 如果没有 from 列表, 创建默认线路名
    if not from_list:
        line_count = len(url_parts)
        from_list = [f"线路{i+1}" for i in range(line_count)]

    result = []
    total_episodes = 0

    for i, from_name in enumerate(from_list):
        from_name = from_name.strip() if from_name else f"线路{i+1}"
        url_part = url_parts[i] if i < len(url_parts) else ""

        episodes = []
        for ep in url_part.split("#"):
            ep = ep.strip()
            if not ep:
                continue
            # 用 $ 分隔名称和 URL
            parts = ep.split("$", 1)
            if len(parts) == 2:
                ep_name = parts[0].strip()
                ep_url = parts[1].strip()
                if ep_url:
                    episodes.append({"name": ep_name, "url": ep_url})
            else:
                # 没有分隔符, 整个作为 URL
                if ep:
                    episodes.append({"name": str(len(episodes) + 1), "url": ep})

        total_episodes += len(episodes)
        result.append({"from": from_name, "episodes": episodes})

    # 倒序排序: 集数 > 0 且 < 300 时反转每条线路的集数列表
    if 0 < total_episodes < 300:
        for line in result:
            line["episodes"].reverse()

    return result


# ======== JS 运行时桥接代码 ========
# 提供 TVBox JS 源所需的全部全局 API: req / pdf / pd / pdfa / pdfh / html / Base64 / MD5 等
# 同时提供 Spider 接口的全部函数声明

JS_BRIDGE = r'''
// ======== TVBox JS 桥接层 ========

// ======== HTML 解析工具 ========

// 查找匹配的闭合标签位置 (支持嵌套)
function _findClosingTag(src, tagName, startPos) {
    var openCount = 1;
    var pos = startPos;
    var openRegex = new RegExp("<" + tagName + "(?=[\\s>])[^>]*>", "gi");
    var closeRegex = new RegExp("</" + tagName + ">", "gi");

    while (openCount > 0 && pos < src.length) {
        openRegex.lastIndex = pos;
        closeRegex.lastIndex = pos;

        var openMatch = openRegex.exec(src);
        var closeMatch = closeRegex.exec(src);

        if (closeMatch === null) return -1;

        if (openMatch !== null && openMatch.index < closeMatch.index) {
            openCount++;
            pos = openMatch.index + openMatch[0].length;
        } else {
            openCount--;
            if (openCount === 0) {
                return closeMatch.index + closeMatch[0].length;
            }
            pos = closeMatch.index + closeMatch[0].length;
        }
    }
    return -1;
}

// HTML 节点
function HtmlNode(outerHtml, innerHtml, attrs, tagName) {
    this._outerHtml = outerHtml || "";
    this._innerHtml = innerHtml || "";
    this._attrs = attrs || "";
    this._tagName = (tagName || "").toLowerCase();
    this._parent = null;

    // 获取纯文本内容 (去掉所有标签)
    this.text = function() {
        return this._innerHtml.replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').trim();
    };

    // 获取内部 HTML
    this.html = function() {
        return this._innerHtml;
    };

    // 获取外部 HTML (含标签本身)
    this.outerHtml = function() {
        return this._outerHtml;
    };

    // 获取属性值
    this.attr = function(name) {
        var pattern = new RegExp(name + "\\s*=\\s*[\"']([^\"']*)[\"']", "i");
        var match = (this._attrs + " " + this._outerHtml).match(pattern);
        return match ? match[1] : "";
    };

    // 获取子节点
    this.children = function() {
        var childNodes = [];
        var src = this._innerHtml;
        var pattern = /<(\w+)([^>]*)>/gi;
        var match;
        while ((match = pattern.exec(src)) !== null) {
            var childTagName = match[1];
            var childAttrs = match[2];
            var childStart = match.index;
            var childEnd = _findClosingTag(src, childTagName, childStart + match[0].length);
            if (childEnd === -1) {
                // 自闭合标签
                var selfClosing = src.substring(childStart, childStart + match[0].length);
                var node = new HtmlNode(selfClosing, "", childAttrs, childTagName);
                node._parent = this;
                childNodes.push(node);
            } else {
                var closeTagLen = childTagName.length + 3;
                var childOuter = src.substring(childStart, childEnd);
                var childInner = src.substring(childStart + match[0].length, childEnd - closeTagLen);
                var node = new HtmlNode(childOuter, childInner, childAttrs, childTagName);
                node._parent = this;
                childNodes.push(node);
            }
        }
        return childNodes;
    };

    // 获取父节点
    this.parent = function() {
        return this._parent;
    };

    // 获取标签名
    this.tagName = function() {
        return this._tagName;
    };

    return this;
}

// HTML 节点集合
function HtmlNodes(nodes) {
    this.nodes = nodes || [];

    this.text = function() {
        if (this.nodes.length === 0) return "";
        return this.nodes[0].text();
    };

    this.html = function() {
        if (this.nodes.length === 0) return "";
        return this.nodes[0].html();
    };

    this.allText = function() {
        var result = [];
        for (var i = 0; i < this.nodes.length; i++) {
            result.push(this.nodes[i].text());
        }
        return result.join("");
    };

    this.attr = function(name) {
        if (this.nodes.length === 0) return "";
        return this.nodes[0].attr(name);
    };

    this.length = function() {
        return this.nodes.length;
    };

    this.eq = function(index) {
        if (index < 0 || index >= this.nodes.length) return new HtmlNodes([]);
        return new HtmlNodes([this.nodes[index]]);
    };

    this.first = function() {
        return this.eq(0);
    };

    this.last = function() {
        return this.eq(this.nodes.length - 1);
    };

    return this;
}

// HTML 文档
function HtmlDoc(src) {
    this.src = src || "";

    // CSS 选择器: 支持 标签名 / .class / #id / [attr=val] / :eq(n) / :lt(n) / :gt(n) / :first / :last
    this.css = function(selector) {
        selector = (selector || "").trim();
        if (!selector) return new HtmlNodes([]);

        var nodes = [];

        // 提取伪类过滤
        var pseudoFilter = null;
        var pseudoMatch = selector.match(/:(eq|lt|gt|first|last)\((\d*)\)/);
        if (pseudoMatch) {
            var ptype = pseudoMatch[1];
            var parg = pseudoMatch[2] ? parseInt(pseudoMatch[2]) : 0;
            if (ptype === "first") {
                pseudoFilter = function(arr) { return arr.length > 0 ? [arr[0]] : []; };
            } else if (ptype === "last") {
                pseudoFilter = function(arr) { return arr.length > 0 ? [arr[arr.length - 1]] : []; };
            } else if (ptype === "eq") {
                pseudoFilter = function(arr) { return parg >= 0 && parg < arr.length ? [arr[parg]] : []; };
            } else if (ptype === "lt") {
                pseudoFilter = function(arr) { return arr.slice(0, parg); };
            } else if (ptype === "gt") {
                pseudoFilter = function(arr) { return arr.slice(parg + 1); };
            }
            selector = selector.replace(/:(eq|lt|gt|first|last)\(\d*\)/, "");
        }

        // 提取属性选择器 [attr=value]
        var attrFilters = [];
        var attrPattern = /\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]/g;
        var attrMatch;
        while ((attrMatch = attrPattern.exec(selector)) !== null) {
            attrFilters.push({name: attrMatch[1], value: attrMatch[2] !== undefined ? attrMatch[2] : null});
        }
        selector = selector.replace(/\[[^\]]+\]/g, "");

        // 提取 id
        var idVal = "";
        var idMatch = selector.match(/#([\w-]+)/);
        if (idMatch) {
            idVal = idMatch[1];
            selector = selector.replace(/#[\w-]+/, "");
        }

        // 提取 class
        var classVal = "";
        var classMatch = selector.match(/\.([\w-]+)/);
        if (classMatch) {
            classVal = classMatch[1];
            selector = selector.replace(/\.[\w-]+/, "");
        }

        // 剩余为标签名
        var tagName = selector.trim().toLowerCase();

        // 构建匹配模式
        var tagPattern = tagName ? tagName : "\\w+";
        var openRegex = new RegExp("<(" + tagPattern + ")(?=[\\s>])[^>]*>", "gi");
        var match;

        while ((match = openRegex.exec(this.src)) !== null) {
            var fullOpenTag = match[0];
            var matchedTagName = match[1].toLowerCase();
            var tagAttrs = match[0].substring(match[0].indexOf(matchedTagName) + matchedTagName.length);

            // 检查 class
            if (classVal) {
                var classAttr = "";
                var classAttrMatch = tagAttrs.match(/class\s*=\s*["']([^"']*)["']/i);
                if (classAttrMatch) classAttr = classAttrMatch[1];
                var classList = classAttr.split(/\s+/);
                if (classList.indexOf(classVal) === -1) continue;
            }

            // 检查 id
            if (idVal) {
                var idAttr = "";
                var idAttrMatch = tagAttrs.match(/id\s*=\s*["']([^"']*)["']/i);
                if (idAttrMatch) idAttr = idAttrMatch[1];
                if (idAttr !== idVal) continue;
            }

            // 检查属性
            var skipNode = false;
            for (var ai = 0; ai < attrFilters.length; ai++) {
                var af = attrFilters[ai];
                var afRegex = new RegExp(af.name + "\\s*=\\s*[\"']([^\"']*)[\"']", "i");
                var afMatch = tagAttrs.match(afRegex);
                var afVal = afMatch ? afMatch[1] : "";
                if (af.value !== null && afVal !== af.value) {
                    skipNode = true;
                    break;
                }
                if (af.value === null && !afMatch) {
                    skipNode = true;
                    break;
                }
            }
            if (skipNode) continue;

            // 查找闭合标签
            var startIdx = match.index;
            var endIdx = _findClosingTag(this.src, matchedTagName, startIdx + fullOpenTag.length);

            if (endIdx === -1) {
                // 自闭合标签 (如 <img>, <br>)
                var node = new HtmlNode(fullOpenTag, "", tagAttrs, matchedTagName);
                nodes.push(node);
            } else {
                var closeTagLen = matchedTagName.length + 3; // </tagname>
                var fullElement = this.src.substring(startIdx, endIdx);
                var innerHtml = this.src.substring(startIdx + fullOpenTag.length, endIdx - closeTagLen);
                var node = new HtmlNode(fullElement, innerHtml, tagAttrs, matchedTagName);
                nodes.push(node);
            }
        }

        // 应用伪类过滤
        if (pseudoFilter) {
            nodes = pseudoFilter(nodes);
        }

        return new HtmlNodes(nodes);
    };

    this.text = function() {
        return this.src.replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").trim();
    };

    this.html = function() {
        return this.src;
    };

    return this;
}

// 创建 HTML 文档
function html(src) {
    return new HtmlDoc(src);
}

// pdfa — parse detail from array, 返回匹配元素的数组
function pdfa(htmlSrc, selector) {
    var src;
    if (typeof htmlSrc === "string") {
        src = htmlSrc;
    } else if (htmlSrc instanceof HtmlNode) {
        src = htmlSrc.html();
    } else if (htmlSrc instanceof HtmlDoc) {
        src = htmlSrc.html();
    } else if (htmlSrc && typeof htmlSrc.toString === "function") {
        src = htmlSrc.toString();
    } else {
        src = String(htmlSrc || "");
    }

    var parts = selector.split("&&");
    var currentSrc = src;
    var nodes = [];

    // 应用第一个 CSS 选择器
    var doc = new HtmlDoc(currentSrc);
    var nodesObj = doc.css(parts[0].trim());
    nodes = nodesObj.nodes;

    // 应用后续 CSS 选择器 (跳过提取命令)
    for (var i = 1; i < parts.length; i++) {
        var part = parts[i].trim();
        // 提取命令: Text, Html, @attr
        if (part === "Text" || part === "Html" || part.charAt(0) === "@") {
            break;
        }
        var newNodes = [];
        for (var j = 0; j < nodes.length; j++) {
            var subDoc = new HtmlDoc(nodes[j].html());
            var subNodes = subDoc.css(part).nodes;
            for (var k = 0; k < subNodes.length; k++) {
                newNodes.push(subNodes[k]);
            }
        }
        nodes = newNodes;
    }

    return nodes;
}

// pdfh — parse detail from html, 返回单个值 (文本/属性)
function pdfh(htmlSrc, selector) {
    var parts = selector.split("&&");
    var lastPart = parts[parts.length - 1].trim();

    // 判断最后部分是否为提取命令
    var isExtraction = (lastPart === "Text" || lastPart === "Html" || lastPart.charAt(0) === "@");

    // 构建 CSS 选择器部分
    var cssSelector;
    if (isExtraction && parts.length > 1) {
        cssSelector = parts.slice(0, -1).join("&&");
    } else {
        cssSelector = selector;
        isExtraction = false;
    }

    var nodes = pdfa(htmlSrc, cssSelector);
    if (nodes.length === 0) return "";

    var node = nodes[0];

    if (!isExtraction) return node.text();
    if (lastPart === "Text") return node.text();
    if (lastPart === "Html") return node.html();
    if (lastPart.charAt(0) === "@") return node.attr(lastPart.substring(1));
    return node.text();
}

// pd — 同 pdfh
function pd(htmlSrc, selector) {
    return pdfh(htmlSrc, selector);
}

// ======== HTTP 请求 ========

function req(url, options) {
    options = options || {};
    options.method = options.method || "GET";
    options.headers = options.headers || {};
    options.data = options.data || null;
    options.timeout = options.timeout || 15000;
    var resp = __http_request(JSON.stringify({
        url: url,
        method: options.method,
        headers: options.headers,
        data: options.data,
        timeout: options.timeout
    }));
    return JSON.parse(resp);
}

// ======== 编解码工具 ========

var Base64 = {
    encode: function(str) {
        return __base64_encode(String(str));
    },
    decode: function(str) {
        return __base64_decode(String(str));
    }
};

function MD5(str) {
    return __md5(String(str));
}

// CryptoJS 简易实现
var CryptoJS = {
    MD5: function(str) { return __md5(String(str)); },
    enc: {
        Utf8: {
            stringify: function(s) { return s; },
            parse: function(s) { return s; }
        },
        Base64: {
            stringify: function(s) { return __base64_encode(String(s)); },
            parse: function(s) { return __base64_decode(String(s)); }
        },
        Hex: {
            stringify: function(s) { return s; },
            parse: function(s) { return s; }
        }
    }
};

// ======== 日志 ========

function redirectPrint(msg) {
    if (typeof msg === "object") msg = JSON.stringify(msg);
    __log(String(msg));
}

// ======== 本地存储 ========

var local = {
    get: function(key) { return __storage_get(key); },
    set: function(key, value) { __storage_set(key, String(value)); },
    delete: function(key) { __storage_delete(key); }
};

// ======== Spider 接口默认实现 (子类可覆盖) ========
// 这些函数由 Spider JS 源代码定义, 这里提供空默认值

function init(cfg) { return {}; }
function homeContent(filter) { return {}; }
function homeVideoContent() { return {}; }
function categoryContent(tid, pg, filter, extend) { return {}; }
function detailContent(ids) { return {}; }
function searchContent(key, quick, pg) { return {}; }
function playerContent(flag, id, vipFlags) { return {}; }
function liveContent(url) { return ""; }
function proxy(params) { return [200, "text/plain", ""]; }
function action(actionStr) { return {}; }
function manualVideoCheck() { return false; }
function isVideoFormat(url) { return false; }
function destroy() { return null; }
'''


# ======== Node.js 原生桥接实现 ========
# 在 Node.js 环境中提供 __http_request / __base64_encode 等函数的本地实现

NODE_NATIVE = r'''
// ======== Node.js 原生桥接 ========
var __node_native = true;

var _node_crypto = require("crypto");
var _node_child = require("child_process");

global.__http_request = function(jsonStr) {
    var params = JSON.parse(jsonStr);
    var url = params.url;
    var method = params.method || "GET";
    var headers = params.headers || {};
    var data = params.data;
    var timeout = params.timeout || 15000;

    var args = ["-s", "-X", method, "--max-time", String(Math.floor(timeout / 1000) + 5), "-L",
                "-w", "\n%{http_code}", "-A", "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"];
    for (var key in headers) {
        args.push("-H", key + ": " + headers[key]);
    }
    if (data) {
        args.push("-d", String(data));
    }
    args.push(url);

    try {
        var output = _node_child.execFileSync("curl", args, {
            encoding: "utf-8",
            timeout: timeout + 10000,
            maxBuffer: 20 * 1024 * 1024
        });
        var lines = output.split("\n");
        var statusCode = parseInt(lines[lines.length - 1]) || 200;
        var body = lines.slice(0, -1).join("\n");
        return JSON.stringify({body: body, statusCode: statusCode, headers: {}, url: url});
    } catch(e) {
        return JSON.stringify({error: String(e), body: "", statusCode: 0, url: url});
    }
};

global.__base64_encode = function(s) {
    return Buffer.from(String(s)).toString("base64");
};

global.__base64_decode = function(s) {
    return Buffer.from(String(s), "base64").toString();
};

global.__md5 = function(s) {
    return _node_crypto.createHash("md5").update(String(s)).digest("hex");
};

global.__log = function(s) {
    process.stderr.write(String(s) + "\n");
};

var __storage_data = {};
global.__storage_get = function(k) { return __storage_data[k] || ""; };
global.__storage_set = function(k, v) { __storage_data[k] = String(v); };
global.__storage_delete = function(k) { delete __storage_data[k]; };
'''


class BaseSpider:
    """爬虫基类, 对应 CatVod Spider 接口
    完整定义所有 Spider 接口方法, 子类按需覆盖
    """

    def init(self, cfg: dict = None) -> dict:
        """初始化 Spider"""
        return {}

    def home_content(self, filter: list = None) -> dict:
        """首页内容 (分类 + 推荐)"""
        raise NotImplementedError

    def home_video_content(self) -> dict:
        """首页推荐影片"""
        return {}

    def category_content(self, tid: str, pg: int = 1, filter: dict = None, extend: dict = None) -> dict:
        """分类内容"""
        raise NotImplementedError

    def detail_content(self, ids: list) -> dict:
        """详情内容"""
        raise NotImplementedError

    def search_content(self, key: str, quick: bool = False, pg: int = 1) -> dict:
        """搜索内容"""
        raise NotImplementedError

    def player_content(self, flag: str, id: str, vip_flags: list = None) -> dict:
        """播放地址"""
        return {"parse": 0, "url": id, "header": "", "flag": flag}

    def live_content(self, url: str) -> str:
        """直播频道列表解析"""
        return ""

    def proxy(self, params) -> list:
        """本地代理, 返回 [statusCode, mimeType, body]"""
        return [200, "text/plain", ""]

    def action(self, action: str) -> dict:
        """自定义动作指令"""
        return {}

    def manual_video_check(self) -> bool:
        """是否人工判断影片格式"""
        return False

    def is_video_format(self, url: str) -> bool:
        """URL 是否为有效媒体 URL"""
        return False

    def destroy(self):
        """销毁资源"""
        pass


class ApiSpider(BaseSpider):
    """Type 0 —— 标准 JSON API 源, 兼容 CMS 苹果 API
    支持:
    - 标准模式 (ac=detail&pg=1&t=xxx)
    - Filter 模式 (f=JSON参数, 用于 filterable=1 的源)
    - Base64 Ext 模式 (ext=Base64参数, 用于带扩展配置的源)
    - 请求重试 / Cookie管理 / 自定义请求头 / 超时处理
    """

    # 常见视频后缀
    VIDEO_EXTS = [".m3u8", ".mp4", ".flv", ".ts", ".mkv", ".avi",
                  ".mov", ".webm", ".mpd", ".m4a", ".m4v", ".wmv"]

    def __init__(self, site: Site):
        self.site = site
        self.api = site.api
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

        # 从站点 header 配置加载自定义请求头
        if site.header and isinstance(site.header, dict):
            self.session.headers.update(site.header)

        # 从站点 ext 配置加载自定义请求头 (兼容旧格式)
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
        self._timeout = site.timeout if site.timeout > 0 else 15
        self._ext_base64 = ""

    def _build_params(self, base: dict, extend: dict = None) -> dict:
        """构建请求参数, 支持标准/Filter/Base64 Ext 三种模式"""
        params = dict(base)
        if extend:
            params.update(extend)

        # Filter 模式: filterable=1 时发送 f 参数 (JSON 编码)
        if self.site.filterable == 1 and extend:
            filter_data = {}
            for k, v in extend.items():
                if k not in ("pg", "t", "ac", "wd"):
                    filter_data[k] = v
            if filter_data:
                params["f"] = json.dumps(filter_data, ensure_ascii=False)

        # Base64 Ext 模式: 站点配置了 ext 字段时发送 ext 参数
        if self.site.ext and self.site.filterable == 1:
            ext_str = self.site.ext if isinstance(self.site.ext, str) else json.dumps(self.site.ext)
            try:
                self._ext_base64 = base64.b64encode(ext_str.encode("utf-8")).decode("ascii")
                params["ext"] = self._ext_base64
            except Exception:
                pass

        return params

    def _request_with_retry(self, method: str, params: dict) -> dict:
        """带重试的请求"""
        import time as _time
        last_error = None

        for attempt in range(self._max_retries):
            try:
                if method == "GET":
                    resp = self.session.get(self.api, params=params, timeout=self._timeout)
                else:
                    resp = self.session.post(self.api, data=params, timeout=self._timeout)

                resp.encoding = resp.apparent_encoding

                # 检查状态码
                if resp.status_code == 429:
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
                    text = resp.text.strip()
                    if text:
                        try:
                            json_match = re.search(r"\{[\s\S]*\}", text)
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
        """解析列表数据, 返回 (items, page, pagecount, limit, total)"""
        items = []
        for v in data.get("list", []):
            item = VodItem(
                vod_id=str(v.get("vod_id", "")),
                vod_name=v.get("vod_name", ""),
                vod_pic=v.get("vod_pic", ""),
                vod_remarks=v.get("vod_remarks", ""),
                type_id=str(v.get("type_id", "")),
                type_name=v.get("type_name", ""),
                vod_year=v.get("vod_year", ""),
                vod_area=v.get("vod_area", ""),
            )
            # 保留 vod_tag 字段
            if v.get("vod_tag"):
                item.vod_tag = v.get("vod_tag")
            items.append(item.to_dict())
        page = data.get("page", 1)
        pagecount = data.get("pagecount", 1)
        limit = data.get("limit", 20)
        total = data.get("total", 0)
        return items, page, pagecount, limit, total

    def home_content(self, filter: list = None) -> dict:
        """首页内容 (分类 + 推荐)"""
        data = self._get(self._build_params({"ac": "list"}))
        cats = []
        for c in data.get("class", []):
            cats.append({"type_id": str(c.get("type_id", "")),
                         "type_name": c.get("type_name", "")})
        self.categories = [Category(c["type_id"], c["type_name"]) for c in cats]

        home_data = self._get(self._build_params({"ac": "detail", "pg": 1}))
        items, page, pagecount, limit, total = self._parse_list(home_data)

        # 解析筛选器
        filters = {}
        for f in data.get("filters", []):
            if isinstance(f, dict):
                filters[str(f.get("key", ""))] = f.get("value", [])

        cat_filters = data.get("filters", {})
        if isinstance(cat_filters, dict):
            for k, v in cat_filters.items():
                filters[str(k)] = v

        return {"categories": cats, "list": items, "filters": filters}

    def home_video_content(self) -> dict:
        """首页推荐影片"""
        data = self._get(self._build_params({"ac": "detail", "pg": 1}))
        items, page, pagecount, limit, total = self._parse_list(data)
        return {"list": items, "page": page, "pagecount": pagecount}

    def category_content(self, tid: str, pg: int = 1, filter: dict = None, extend: dict = None) -> dict:
        """分类内容"""
        params = self._build_params({"ac": "detail", "pg": pg, "t": tid}, extend)
        data = self._get(params)
        items, page, pagecount, limit, total = self._parse_list(data)
        return {"list": items, "page": page, "pagecount": pagecount,
                "limit": limit, "total": total}

    def search_content(self, key: str, quick: bool = False, pg: int = 1) -> dict:
        """搜索内容"""
        params = {"wd": key, "pg": pg}
        if not quick:
            params["ac"] = "detail"
        params = self._build_params(params)
        data = self._get(params)
        items, page, pagecount, limit, total = self._parse_list(data)
        return {"list": items, "page": page, "pagecount": pagecount}

    def detail_content(self, ids: list) -> dict:
        """详情内容"""
        data = self._get(self._build_params({"ac": "detail", "ids": ",".join(ids)}))
        items = []
        for v in data.get("list", []):
            item = VodItem(
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
            )
            if v.get("vod_tag"):
                item.vod_tag = v.get("vod_tag")
            items.append(item.to_dict())
        return {"list": items}

    def player_content(self, flag: str, id: str, vip_flags: list = None) -> dict:
        """播放地址"""
        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"}
        if self.site.header and isinstance(self.site.header, dict):
            headers.update(self.site.header)
        parse_flag = 0
        if vip_flags and flag in vip_flags:
            parse_flag = 1
        return {"parse": parse_flag, "url": id, "header": headers, "flag": flag}

    def is_video_format(self, url: str) -> bool:
        """检查 URL 是否为有效媒体 URL"""
        if not url:
            return False
        lower = url.lower().split("?")[0].split("#")[0]
        for ext in self.VIDEO_EXTS:
            if lower.endswith(ext):
                return True
        # 检查 URL 中是否包含媒体标识
        media_patterns = ["/m3u8", "/mp4", "/flv", "stream", "video"]
        for pat in media_patterns:
            if pat in lower:
                return True
        return False

    def manual_video_check(self) -> bool:
        """是否需要人工判断影片格式"""
        return False

    def destroy(self):
        """销毁资源"""
        try:
            self.session.close()
        except Exception:
            pass


class JsSpider(BaseSpider):
    """Type 4 —— JavaScript 源
    优先使用 quickjs 包 (内置, 无需外部依赖)
    回退到 Node.js (需要安装)
    完整支持所有 Spider 桥接 API
    """

    def __init__(self, site: Site):
        self.site = site
        self.api = site.api
        self.ext = site.ext if site.ext else ""
        self.jar = site.jar or ""
        self._js_code: Optional[str] = None
        self._quickjs = None
        self._node_available = self._check_node()
        self._runtime = self._detect_runtime()
        self._ctx = None  # quickjs context 缓存
        self._storage: Dict[str, str] = {}  # 本地存储

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
            if self.api.startswith("http"):
                resp = requests.get(self.api, timeout=15,
                                    headers={"User-Agent": "Mozilla/5.0"})
                self._js_code = resp.text
            else:
                self._js_code = self.api
        except Exception as e:
            self._js_code = ""
            print(f"[JsSpider] 加载 JS 源失败: {e}")

    def _build_js_command(self, cmd: str, args: dict) -> str:
        """构建 JS 命令字符串 (避免 f-string 中的反斜杠问题)"""
        args_json = json.dumps(args, ensure_ascii=False)
        # 使用字符串拼接而非 f-string, 避免 Python 3.8 反斜杠限制
        parts = []
        parts.append('var __args = ' + args_json + ';')
        parts.append('var __result;')
        parts.append('try {')
        parts.append('    switch("' + cmd + '") {')
        parts.append('        case "init":')
        parts.append('            __result = typeof init === "function" ? init(__args.cfg || {}) : {};')
        parts.append('            break;')
        parts.append('        case "homeContent":')
        parts.append('            __result = typeof homeContent === "function" ? homeContent(__args.filter || []) : {};')
        parts.append('            break;')
        parts.append('        case "homeVideoContent":')
        parts.append('            __result = typeof homeVideoContent === "function" ? homeVideoContent() : {};')
        parts.append('            break;')
        parts.append('        case "categoryContent":')
        parts.append('            __result = typeof categoryContent === "function" ? categoryContent(__args.tid, __args.pg || 1, __args.filter || {}, __args.extend || {}) : {};')
        parts.append('            break;')
        parts.append('        case "searchContent":')
        parts.append('            __result = typeof searchContent === "function" ? searchContent(__args.key, __args.quick || false, __args.pg || 1) : {};')
        parts.append('            break;')
        parts.append('        case "detailContent":')
        parts.append('            __result = typeof detailContent === "function" ? detailContent(__args.ids || []) : {};')
        parts.append('            break;')
        parts.append('        case "playerContent":')
        parts.append('            __result = typeof playerContent === "function" ? playerContent(__args.flag || "", __args.id || "", __args.vipFlags || []) : {};')
        parts.append('            break;')
        parts.append('        case "liveContent":')
        parts.append('            __result = typeof liveContent === "function" ? liveContent(__args.url || "") : "";')
        parts.append('            break;')
        parts.append('        case "proxy":')
        parts.append('            __result = typeof proxy === "function" ? proxy(__args.params || {}) : [200, "text/plain", ""];')
        parts.append('            break;')
        parts.append('        case "action":')
        parts.append('            __result = typeof action === "function" ? action(__args.action || "") : {};')
        parts.append('            break;')
        parts.append('        case "manualVideoCheck":')
        parts.append('            __result = typeof manualVideoCheck === "function" ? manualVideoCheck() : false;')
        parts.append('            break;')
        parts.append('        case "isVideoFormat":')
        parts.append('            __result = typeof isVideoFormat === "function" ? isVideoFormat(__args.url || "") : false;')
        parts.append('            break;')
        parts.append('        case "destroy":')
        parts.append('            __result = typeof destroy === "function" ? destroy() : null;')
        parts.append('            break;')
        parts.append('        default:')
        parts.append('            __result = {error: "unknown command: ' + cmd + '"};')
        parts.append('    }')
        parts.append('} catch(e) {')
        parts.append('    __result = {error: String(e)};')
        parts.append('}')
        parts.append('JSON.stringify(__result === undefined ? null : __result);')
        return "\n".join(parts)

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
            ctx.add_callable("__base64_encode", lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii"))
            ctx.add_callable("__base64_decode", lambda s: base64.b64decode(s).decode("utf-8"))
            ctx.add_callable("__md5", lambda s: hashlib.md5(s.encode("utf-8")).hexdigest())
            ctx.add_callable("__log", lambda s: print(f"[JS] {s}"))
            ctx.add_callable("__storage_get", lambda k: self._storage.get(k, ""))
            ctx.add_callable("__storage_set", lambda k, v: self._storage.update({k: str(v)}))
            ctx.add_callable("__storage_delete", lambda k: self._storage.pop(k, None))

            # 加载 spider 代码
            ctx.eval(self._js_code)

            # 执行命令
            js_cmd = self._build_js_command(cmd, args)
            result = ctx.eval(js_cmd)

            if result and result != "undefined":
                return json.loads(result)
            return {}

        except Exception as e:
            return {"error": f"quickjs 执行错误: {e}"}

    def _run_node(self, cmd: str, args: dict) -> dict:
        """使用 Node.js 执行 JS (回退方案)"""
        self._ensure_loaded()
        if not self._js_code:
            return {"error": "JS 源加载失败"}

        # 组合完整的 JS 代码: 原生桥接 + JS_BRIDGE + Spider代码 + 命令
        js_cmd = self._build_js_command(cmd, args)
        full_code = NODE_NATIVE + "\n" + JS_BRIDGE + "\n" + self._js_code + "\n" + js_cmd + "\nprocess.stdout.write(JSON.stringify(__result === undefined ? null : __result));"

        # 写入临时文件
        fd, tmp_path = tempfile.mkstemp(suffix=".js", prefix="tvbox_js_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(full_code)

            result = subprocess.run(
                ["node", tmp_path],
                capture_output=True, timeout=30, text=True
            )

            if result.returncode != 0:
                error_msg = result.stderr[:500] if result.stderr else "未知错误"
                return {"error": f"Node.js 执行错误: {error_msg}"}

            if result.stdout:
                return json.loads(result.stdout)
            return {}

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

    def _run(self, cmd: str, args: dict) -> Any:
        """执行 JS 命令"""
        if self._runtime == "quickjs":
            return self._run_quickjs(cmd, args)
        elif self._runtime == "node":
            return self._run_node(cmd, args)
        else:
            return {"error": "无可用 JS 运行时, 请安装 quickjs (pip install quickjs) 或 Node.js"}

    def _js_http_request(self, params_json: str) -> str:
        """JS 桥接: HTTP 请求"""
        try:
            params = json.loads(params_json)
            url = params.get("url", "")
            method = params.get("method", "GET").upper()
            headers = params.get("headers", {})
            data = params.get("data")
            timeout = params.get("timeout", 15000) / 1000

            resp = requests.request(
                method, url, headers=headers, data=data, timeout=timeout
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

    def _js_proxy(self, params: dict) -> list:
        """代理请求实现"""
        result = self._run("proxy", {"params": params})
        if isinstance(result, list) and len(result) >= 3:
            return result
        return [200, "text/plain", ""]

    def _js_live_content(self, url: str) -> str:
        """直播解析"""
        result = self._run("liveContent", {"url": url})
        if isinstance(result, str):
            return result
        if isinstance(result, dict) and result.get("error"):
            print(f"[JsSpider] liveContent 错误: {result.get('error')}")
        return ""

    def _js_action(self, action: str) -> dict:
        """自定义动作"""
        result = self._run("action", {"action": action})
        if isinstance(result, dict):
            return result
        return {}

    # ======== Spider 接口方法 ========

    def home_content(self, filter: list = None) -> dict:
        return self._run("homeContent", {"filter": filter or []})

    def home_video_content(self) -> dict:
        return self._run("homeVideoContent", {})

    def category_content(self, tid: str, pg: int = 1, filter: dict = None, extend: dict = None) -> dict:
        return self._run("categoryContent", {"tid": tid, "pg": pg, "filter": filter, "extend": extend})

    def search_content(self, key: str, quick: bool = False, pg: int = 1) -> dict:
        return self._run("searchContent", {"key": key, "quick": quick, "pg": pg})

    def detail_content(self, ids: list) -> dict:
        return self._run("detailContent", {"ids": ids})

    def player_content(self, flag: str, id: str, vip_flags: list = None) -> dict:
        return self._run("playerContent", {"flag": flag, "id": id, "vipFlags": vip_flags or []})

    def live_content(self, url: str) -> str:
        return self._js_live_content(url)

    def proxy(self, params) -> list:
        return self._js_proxy(params)

    def action(self, action: str) -> dict:
        return self._js_action(action)

    def manual_video_check(self) -> bool:
        result = self._run("manualVideoCheck", {})
        if isinstance(result, bool):
            return result
        return False

    def is_video_format(self, url: str) -> bool:
        result = self._run("isVideoFormat", {"url": url})
        if isinstance(result, bool):
            return result
        return False

    def destroy(self):
        """销毁资源"""
        try:
            self._run("destroy", {})
        except Exception:
            pass
        self._ctx = None
        self._storage.clear()


class JarSpider(BaseSpider):
    """Type 1 —— JAR 源 (不支持, 桌面端无法运行 Dalvik 字节码)
    所有方法返回错误信息, 提示用户使用 API(Type0) 或 JS(Type4) 源
    """

    def __init__(self, site: Site):
        self.site = site

    def home_content(self, filter: list = None) -> dict:
        return {"error": "JAR 源(Type1)不支持桌面端, 请使用 API(Type0)或 JS(Type4)源"}

    def home_video_content(self) -> dict:
        return {"error": "JAR 源不支持"}

    def category_content(self, tid: str, pg: int = 1, filter: dict = None, extend: dict = None) -> dict:
        return {"error": "JAR 源不支持"}

    def search_content(self, key: str, quick: bool = False, pg: int = 1) -> dict:
        return {"error": "JAR 源不支持"}

    def detail_content(self, ids: list) -> dict:
        return {"error": "JAR 源不支持"}

    def player_content(self, flag: str, id: str, vip_flags: list = None) -> dict:
        return {"error": "JAR 源不支持", "parse": 0, "url": id, "header": "", "flag": flag}

    def live_content(self, url: str) -> str:
        return ""

    def is_video_format(self, url: str) -> bool:
        return False

    def manual_video_check(self) -> bool:
        return False

    def destroy(self):
        pass


class SpiderManager:
    """爬虫管理器
    - 根据 site.type 正确分发到 Type0/1/4
    - 缓存 spider 实例
    - 支持 jar 参数传递 (全局 spider JAR)
    - search_all 支持繁转简
    """

    def __init__(self):
        self._spiders: Dict[str, BaseSpider] = {}
        self._global_jar: str = ""

    def set_jar(self, jar: str):
        """设置全局 Spider JAR 路径/URL"""
        self._global_jar = jar or ""

    def get_spider(self, site: Site) -> BaseSpider:
        """获取或创建 spider 实例 (带缓存)"""
        if site.key in self._spiders:
            return self._spiders[site.key]

        # 根据 site.type 分发
        if site.type == 0:
            spider = ApiSpider(site)
        elif site.type == 4:
            spider = JsSpider(site)
        elif site.type == 1:
            spider = JarSpider(site)
        elif site.type == 3:
            # Type 3 (Python) 桌面端不支持, 回退到 JarSpider 的错误提示
            spider = JarSpider(site)
        else:
            # 默认使用 ApiSpider
            spider = ApiSpider(site)

        # 传递 jar 参数
        if hasattr(spider, "jar") and not getattr(spider, "jar", ""):
            spider.jar = self._global_jar

        self._spiders[site.key] = spider
        return spider

    def home_content(self, site: Site) -> dict:
        return self.get_spider(site).home_content()

    def home_video_content(self, site: Site) -> dict:
        return self.get_spider(site).home_video_content()

    def category_content(self, site: Site, tid: str, pg: int = 1, extend: dict = None) -> dict:
        return self.get_spider(site).category_content(tid, pg, extend=extend)

    def search_content(self, site: Site, key: str, pg: int = 1) -> dict:
        return self.get_spider(site).search_content(key, pg=pg)

    def detail_content(self, site: Site, ids: list) -> dict:
        return self.get_spider(site).detail_content(ids)

    def player_content(self, site: Site, flag: str, vid: str) -> dict:
        return self.get_spider(site).player_content(flag, vid)

    def live_content(self, site: Site, url: str) -> str:
        return self.get_spider(site).live_content(url)

    def is_video_format(self, site: Site, url: str) -> bool:
        return self.get_spider(site).is_video_format(url)

    def manual_video_check(self, site: Site) -> bool:
        return self.get_spider(site).manual_video_check()

    def search_all(self, sites: List[Site], key: str) -> List[dict]:
        """多站点搜索, 支持繁转简
        先用原始关键词搜索, 如果没有结果则用简体关键词搜索
        """
        import concurrent.futures

        results = []
        searchable = [s for s in sites if s.searchable == 1 and s.hide == 0]

        # 繁转简
        simplified_key = t2s(key)

        def search_one(site, search_key):
            try:
                data = self.search_content(site, search_key)
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

        # 先用原始关键词搜索
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(search_one, s, key): s for s in searchable}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        # 如果原始关键词没有结果且简体关键词不同, 再用简体搜索
        if not results and simplified_key != key:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(search_one, s, simplified_key): s for s in searchable}
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result:
                        results.append(result)

        return results

    def destroy_spider(self, site_key: str):
        """销毁指定站点的 spider 实例"""
        spider = self._spiders.pop(site_key, None)
        if spider:
            try:
                spider.destroy()
            except Exception:
                pass

    def clear_cache(self):
        """清除所有缓存的 spider 实例"""
        for spider in self._spiders.values():
            try:
                spider.destroy()
            except Exception:
                pass
        self._spiders.clear()
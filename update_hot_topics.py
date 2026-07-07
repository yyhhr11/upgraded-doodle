#!/usr/bin/env python3
"""
热门话题自动更新脚本
每天抓取东方财富快讯、新浪财经、华尔街见闻的过去24小时热门内容，
生成 HOT_TOPICS 数组并更新 index.html。

用于 GitHub Actions 定时任务（每天 UTC 01:00 = 北京时间 09:00）。
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from collections import Counter

import requests

# ===== 配置 =====
INDEX_FILE = "index.html"
TOPIC_COUNT = 12  # 生成话题数
HOURS_BACK = 24   # 回溯时间

# 板块关键词映射（用于从标题中识别热门板块）
SECTOR_KEYWORDS = {
    "AI/算力": ["算力", "AI", "人工智能", "GPU", "英伟达", "NVIDIA", "大模型", "LLM", "智能体", "智算"],
    "CPO/光通信": ["CPO", "光模块", "光通信", "光纤", "光芯片", "硅光"],
    "存储芯片": ["存储", "HBM", "DRAM", "NAND", "美光", "闪迪", "SK海力士", "兆易创新", "澜起"],
    "PCB": ["PCB", "印制电路板", "电路板", "HDI", "深南电路", "胜宏科技"],
    "半导体/芯片": ["半导体", "芯片", "晶圆", "光刻", "中芯国际", "台积电", "麒麟", "EDA", "封装", "流片", "代工"],
    "机器人": ["机器人", "人形机器人", "Optimus", "宇树", "Figure"],
    "新能源/光伏": ["光伏", "储能", "锂电", "电池", "宁德时代", "比亚迪", "固态电池", "逆变器"],
    "医药/创新药": ["医药", "创新药", "CXO", "生物医药", "医疗器械", "药明", "制药", "汇宇"],
    "消费/猪肉": ["消费", "猪肉", "生猪", "白酒", "食品", "养殖", "猪价", "农牧"],
    "黄金/贵金属": ["黄金", "贵金属", "金价", "白银", "黄金储备"],
    "汽车": ["汽车", "新能源车", "特斯拉", "智能驾驶", "小米汽车", "华为汽车", "SpaceX"],
    "军工/航天": ["军工", "航天", "卫星", "导弹", "国防", "SpaceX"],
    "金融": ["银行", "券商", "保险", "金融", "降息", "加息", "再保险", "外汇"],
    "MLCC/被动元件": ["MLCC", "被动元件", "电容", "国巨", "村田", "离型膜"],
    "建筑/基建": ["中标", "EPC", "施工", "天然气管道", "建工"],
    "电力/能源": ["电力", "纯碱", "铜", "石化", "能源", "油价"],
}

# 热度等级映射
HOT_LEVELS = [
    ("🔥 最热", ["暴增", "暴涨", "涨停", "重挫", "崩盘", "失守", "突破"]),
    ("📈 热议", ["大涨", "大跌", "飙升", "创新高", "新低", "异动", "涨停"]),
    ("💬 关注", ["增长", "下滑", "反弹", "回调", "利好", "利空"]),
    ("🌐 全球", []),  # 兜底
]


def fetch_eastmoney_news():
    """从东方财富快讯抓取最近50条新闻"""
    try:
        resp = requests.get(
            "https://newsapi.eastmoney.com/kuaixun/v2/api/list",
            params={"page": 1, "size": 50},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        data = resp.json()
        if data.get("rc") != 1:
            print(f"  [东方财富快讯] rc={data.get('rc')}, 数据为空")
            return []
        news = data.get("news", [])
        print(f"  [东方财富快讯] 获取 {len(news)} 条")
        return news
    except Exception as e:
        print(f"  [东方财富快讯] 错误: {e}")
        return []


def fetch_sina_news():
    """从新浪财经抓取A股滚动新闻"""
    try:
        resp = requests.get(
            "https://feed.mix.sina.com.cn/api/roll/get",
            params={"pageid": 153, "lid": 2512, "k": "", "num": 50, "page": 1},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        data = resp.json()
        items = data.get("result", {}).get("data", [])
        print(f"  [新浪财经] 获取 {len(items)} 条")
        return items
    except Exception as e:
        print(f"  [新浪财经] 错误: {e}")
        return []


def fetch_wallstreetcn():
    """从华尔街见闻抓取全球财经快讯"""
    try:
        resp = requests.get(
            "https://api-one.wallstcn.com/apiv1/content/lives",
            params={
                "channel": "global-channel",
                "client": "pc",
                "limit": 50,
                "first_page": "true",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        print(f"  [华尔街见闻] 获取 {len(items)} 条")
        return items
    except Exception as e:
        print(f"  [华尔街见闻] 错误: {e}")
        return []


def parse_time(time_str):
    """解析各种格式的时间字符串，返回 datetime 对象"""
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M",
        "%m月%d日 %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue

    # 尝试 "2026-07-07T16:04:54+08:00" 格式
    try:
        return datetime.fromisoformat(time_str)
    except (ValueError, TypeError):
        pass

    return None


def normalize_news(raw_items):
    """
    将不同数据源的新闻统一为标准格式
    返回: [{title, summary, time, source, comment_count}]
    """
    # 使用 naive datetime，统一用北京时间 (UTC+8)
    now = datetime.now()
    cutoff = now - timedelta(hours=HOURS_BACK)
    normalized = []

    for item in raw_items:
        # 东方财富快讯格式
        if "showtime" in item and "newsid" in item:
            t = parse_time(item.get("showtime", ""))
            if not t:
                continue
            if t < cutoff:
                continue
            normalized.append({
                "title": item.get("title", ""),
                "summary": item.get("digest", "").replace("【", "").replace("】", " ").strip(),
                "time": t,
                "source": "东方财富快讯",
                "comment_count": int(item.get("commentnum", 0) or 0),
            })

        # 新浪财经格式
        elif "ctime" in item and "title" in item:
            t = parse_time(item.get("ctime", ""))
            if not t:
                continue
            if t < cutoff:
                continue
            normalized.append({
                "title": item.get("title", ""),
                "summary": item.get("intro", ""),
                "time": t,
                "source": "新浪财经",
                "comment_count": 0,
            })

        # 华尔街见闻格式 (display_time 是 Unix 时间戳 int)
        elif "content" in item and "display_time" in item:
            display_time = item.get("display_time", 0)
            if isinstance(display_time, (int, float)):
                t = datetime.fromtimestamp(display_time)
            else:
                t = parse_time(str(display_time))
            if not t:
                continue
            if t < cutoff:
                continue
            title = item.get("title", "") or ""
            content = item.get("content", "")
            # 去除 HTML 标签
            content_text = re.sub(r"<[^>]+>", "", content).strip()
            if not title:
                title = content_text[:60]
            normalized.append({
                "title": title,
                "summary": content_text[:200],
                "time": t,
                "source": "华尔街见闻",
                "comment_count": item.get("comment_count", 0) or 0,
            })

    # 按时间倒序
    normalized.sort(key=lambda x: x["time"], reverse=True)
    return normalized


def extract_tags(title, summary):
    """从标题和摘要中提取板块关键词标签"""
    text = title + " " + summary
    tags = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                tags.append(kw)
                break  # 每个板块只加一个标签
    # 去重并限制数量
    seen = set()
    unique = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique[:4]


def determine_hot_level(title, summary):
    """根据标题和摘要判断热度等级"""
    text = title + summary
    for level, keywords in HOT_LEVELS:
        if not keywords:  # 兜底
            continue
        for kw in keywords:
            if kw in text:
                return level
    return "🌐 全球"


def select_market_overview(news):
    """选择大盘行情相关话题"""
    keywords = ["沪指", "A股", "三大指数", "大盘", "上证", "深证", "创业板", "科创板", "沪深", "收盘", "指数"]
    for n in news:
        title = n["title"]
        # 优先匹配含"A股"或"沪指"的
        for kw in ["A股", "沪指", "三大指数", "大盘"]:
            if kw in title:
                return n
    # 退而求其次
    for n in news:
        for kw in keywords:
            if kw in n["title"]:
                return n
    return None


def select_sector_hotspots(news):
    """选择热门板块/概念相关话题（去重聚合）"""
    sector_topics = []
    seen_sectors = set()

    for n in news:
        tags = extract_tags(n["title"], n["summary"])
        if not tags:
            continue
        # 用第一个标签作为板块标识
        sector_key = tags[0]
        if sector_key in seen_sectors:
            continue
        seen_sectors.add(sector_key)
        sector_topics.append(n)

    return sector_topics[:4]


def select_heavyweight(news):
    """选择重磅个股/行业新闻"""
    keywords = ["暴增", "暴涨", "涨停", "重挫", "崩盘", "飙升", "创新高",
                "业绩", "净利润", "营收", "中标", "IPO", "上市"]
    heavy = []
    for n in news:
        for kw in keywords:
            if kw in n["title"]:
                heavy.append(n)
                break
    # 按评论数排序取前几
    heavy.sort(key=lambda x: x["comment_count"], reverse=True)
    return heavy[:4]


def select_global_macro(news):
    """选择全球宏观/外围话题"""
    keywords = ["美股", "美联储", "黄金", "原油", "美元", "欧股", "港股",
                "日本", "韩国", "加息", "降息", "通胀", "GDP", "PMI"]
    macro = []
    for n in news:
        for kw in keywords:
            if kw in n["title"]:
                macro.append(n)
                break
    return macro[:3]


def generate_topics(news):
    """从所有新闻中生成最终的 12 条话题"""
    topics = []

    # 1. 大盘行情 (1条)
    market = select_market_overview(news)
    if market:
        topics.append(market)
        news = [n for n in news if n != market]

    # 2. 热门板块 (3-4条)
    sectors = select_sector_hotspots(news)
    for s in sectors:
        topics.append(s)
        news = [n for n in news if n != s]

    # 3. 重磅个股/行业 (3-4条)
    heavy = select_heavyweight(news)
    for h in heavy:
        if h not in topics:
            topics.append(h)
            news = [n for n in news if n != h]

    # 4. 全球宏观 (2-3条)
    macro = select_global_macro(news)
    for m in macro:
        if m not in topics:
            topics.append(m)
            news = [n for n in news if n != m]

    # 5. 补充剩余到12条（按评论数排序，并去重标题相似度）
    remaining = [n for n in news if n not in topics]
    remaining.sort(key=lambda x: x["comment_count"], reverse=True)
    seen_titles = set()
    for t in topics:
        # 用标题前10个字符做简易去重
        seen_titles.add(t["title"][:10])
    for r in remaining:
        if len(topics) >= TOPIC_COUNT:
            break
        if r not in topics and r["title"][:10] not in seen_titles:
            topics.append(r)
            seen_titles.add(r["title"][:10])

    # 确保至少有6条
    if len(topics) < 6:
        for r in news:
            if len(topics) >= 6:
                break
            if r not in topics and r["title"][:10] not in seen_titles:
                topics.append(r)
                seen_titles.add(r["title"][:10])

    # 最终去重（按标题前15字符），然后截取
    final = []
    seen = set()
    for t in topics:
        key = t["title"][:15]
        if key not in seen:
            seen.add(key)
            final.append(t)

    # 如果去重后不足 TOPIC_COUNT，从剩余中补充
    if len(final) < TOPIC_COUNT:
        for r in remaining:
            if len(final) >= TOPIC_COUNT:
                break
            key = r["title"][:15]
            if key not in seen:
                seen.add(key)
                final.append(r)

    return final[:TOPIC_COUNT]


def format_topic_entry(topic):
    """将话题格式化为 JS 对象字符串"""
    title = topic["title"].replace("'", "\\'").replace('"', '\\"')
    summary = topic["summary"].replace("'", "\\'").replace('"', '\\"').replace("\n", " ")[:200]
    tags = extract_tags(topic["title"], topic["summary"])
    hot = determine_hot_level(topic["title"], topic["summary"])
    source = topic["source"]
    time_str = topic["time"].strftime("%m/%d %H:%M")

    # 生成 emoji 前缀
    emoji_map = {"🔥 最热": "🔴", "📈 热议": "🟡", "💬 关注": "🟢", "🌐 全球": "⚪"}
    emoji = emoji_map.get(hot, "⚪")

    tags_str = ",".join(f"'{t}'" for t in tags)

    return (
        f"  {{title:'{emoji} {title}',"
        f"summary:'{summary}',"
        f"tags:[{tags_str}],"
        f"hot:'{hot}',"
        f"source:'{source}',"
        f"time:'{time_str}'}}"
    )


def update_index_html(topics):
    """替换 index.html 中的 HOT_TOPICS 数组"""
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 构建新的 HOT_TOPICS 数组
    today_str = datetime.now().strftime("%Y-%m-%d")
    header = f"// ===== 全球热点话题（每日更新） =====\n"
    header += f"// 更新日期：{today_str}\n"
    header += f"// 数据来源：东方财富快讯 + 新浪财经 + 华尔街见闻\n"
    header += "const HOT_TOPICS=[\n"

    entries = [format_topic_entry(t) for t in topics]
    body = ",\n".join(entries)

    footer = "\n];"

    new_array = header + body + footer

    # 正则匹配：从 "// ===== 全球热点话题" 注释头开始，到 "];" 结束
    pattern = r"// ===== 全球热点话题（每日更新） =====.*?const HOT_TOPICS=\[.*?\];"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        print("ERROR: 未找到 HOT_TOPICS 数组！")
        sys.exit(1)

    old_block = match.group(0)
    content = content.replace(old_block, new_array, 1)

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"✅ 已更新 index.html，共 {len(topics)} 条话题")


def main():
    print(f"=== 热门话题自动更新 ===")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"回溯时间: {HOURS_BACK} 小时")
    print()

    # 1. 抓取数据
    print("📡 抓取数据...")
    raw_em = fetch_eastmoney_news()
    raw_sina = fetch_sina_news()
    raw_ws = fetch_wallstreetcn()

    # 2. 标准化
    all_news = normalize_news(raw_em + raw_sina + raw_ws)
    print(f"\n📊 过去 {HOURS_BACK} 小时内有效新闻: {len(all_news)} 条")

    if len(all_news) < 5:
        print("⚠️ 新闻数量不足，可能数据源异常，跳过更新")
        sys.exit(0)

    # 3. 生成话题
    topics = generate_topics(all_news)
    print(f"\n📝 生成话题: {len(topics)} 条")
    for i, t in enumerate(topics):
        tags = extract_tags(t["title"], t["summary"])
        hot = determine_hot_level(t["title"], t["summary"])
        print(f"  {i+1}. [{hot}] {t['title'][:50]}... | {t['source']}")

    # 4. 更新 index.html
    print()
    update_index_html(topics)

    print("\n✅ 完成!")


if __name__ == "__main__":
    main()

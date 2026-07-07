#!/usr/bin/env python3
"""
热门话题自动更新脚本 v2
每天抓取东方财富股吧热门帖子、华尔街见闻热门文章，
过滤掉公告/业绩预告/复盘类内容，只保留真正的热点讨论话题。

用于 GitHub Actions 定时任务（每天 UTC 01:00 = 北京时间 09:00）。
"""

import json
import re
import sys
import html as html_mod
from datetime import datetime, timedelta
from collections import Counter

import requests

# ===== 配置 =====
INDEX_FILE = "index.html"
TOPIC_COUNT = 12
HOURS_BACK = 24

# 过滤关键词：标题含这些词的内容是公告/复盘，不是讨论帖
BORING_FILTER = [
    "预计.*净利润", "预计.*归母", "公告", "年报", "季报", "半年报",
    "外汇储备", "外汇管理局", "央行.*增持", "央行连续",
    "收盘：", "收盘，", "港股收盘", "A股收盘", "复盘",
    "中标", "获得政府补助", "分红款", "收到.*分红",
    "启动防汛", "应急响应", "市场监管总局",
    "外交部回应", "批准.*发布实施", "印发",
    "签署.*战略合作", "进一步加码", "以旧换新",
    "环比变动", "销售收入.*同比", "同比下降",
    "生猪销售", "活禽销售", "商品猪",
    "有民营银行", "欧洲央行管委", "日本最高外汇",
    "淡马锡领投", "大马士革", "法国日前",
    "美国22个州反对", "俄公布最新",
    "华尔街给予", "摩根资管",
    "加快上海", "金融监管总局",
    "松辽委", "上半年国内新建影院",
    "Salesforce", "报道：叙利亚",
    "早餐FM", "早餐.*FM", "FM-Radio",
    "晒晒.*收益", "晒收益", "晒晒半年",
]


def fetch_guba_hot_posts():
    """
    从东方财富股吧抓取热门帖子。
    尝试多个可能的接口路径。
    """
    posts = []

    # 方法1: gubatopic.eastmoney.com POST 接口
    try:
        resp = requests.post(
            "https://gubatopic.eastmoney.com/interface/GetData.aspx",
            data={
                "path": "newtopic/api/Topic/HomePageListRead",
                "param": "ps=30&p=1&type=0",
                "env": "2",
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://guba.eastmoney.com/",
            },
            timeout=15,
        )
        if resp.status_code == 200 and len(resp.text) > 50:
            try:
                data = resp.json()
                topics = data.get("re", [])
                for t in topics:
                    # gubatopic 字段: nickname=标题, desc=描述, postNumber=帖子数, clickNumber=点击数
                    title = t.get("nickname", "") or t.get("title", "")
                    desc = t.get("desc", "") or t.get("introduction", "")
                    post_num = int(t.get("postNumber", 0) or 0)
                    click_num = int(t.get("clickNumber", 0) or 0)
                    posts.append({
                        "title": title,
                        "summary": desc,
                        "read_count": click_num,
                        "comment_count": post_num,
                        "time_str": "",
                        "source": "东方财富股吧",
                    })
                print(f"  [东方财富股吧-话题] 获取 {len(posts)} 条")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  [东方财富股吧-话题] 解析错误: {e}")
    except Exception as e:
        print(f"  [东方财富股吧-话题] 错误: {e}")

    # 方法2: 如果方法1没拿到数据，尝试 push2 热门股票论坛接口
    if len(posts) < 5:
        try:
            # 获取热门股票列表，再抓它们的帖子
            resp = requests.get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "fid": "f3",
                    "po": "1",
                    "pz": "10",
                    "pn": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fields": "f12,f14,f3,f104",
                    "fs": "m:0+t:6+f:!2",
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://quote.eastmoney.com/",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                stocks = data.get("data", {}).get("diff", [])
                print(f"  [东方财富-push2] 获取热门股票 {len(stocks)} 只")
                # 对每只热门股票抓论坛帖子
                for stock in stocks[:5]:
                    code = stock.get("f12", "")
                    name = stock.get("f14", "")
                    try:
                        r2 = requests.get(
                            f"https://guba.eastmoney.com/interface/GetData.aspx",
                            params={
                                "path": f"stockdata/{code}",
                                "ps": "3",
                                "p": "1",
                                "type": "1",
                            },
                            headers={"User-Agent": "Mozilla/5.0"},
                            timeout=10,
                        )
                        if r2.status_code == 200 and len(r2.text) > 20:
                            try:
                                d2 = r2.json()
                                for post in d2.get("re", [])[:2]:
                                    posts.append({
                                        "title": f"{name}: {post.get('title', '')}",
                                        "summary": post.get("summary", "") or post.get("content", ""),
                                        "read_count": post.get("rc", 0),
                                        "comment_count": post.get("cc", 0),
                                        "time_str": post.get("time", ""),
                                        "source": "东方财富股吧",
                                    })
                            except (json.JSONDecodeError, KeyError):
                                pass
                    except Exception:
                        pass
                print(f"  [东方财富股吧-stock] 共获取 {len(posts)} 条帖子")
        except Exception as e:
            print(f"  [东方财富-push2] 错误: {e}")

    return posts


def fetch_wallstreetcn_hot():
    """从华尔街见闻抓取热门文章+快讯"""
    items = []

    # 热门文章列表
    try:
        resp = requests.get(
            "https://api-one.wallstcn.com/apiv1/content/articles/hot",
            params={"period": "all", "limit": 15},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        data = resp.json()
        raw_items = data.get("data", {}).get("day_items", [])
        for item in raw_items:
            title = item.get("title", "")
            # 热门列表没有 content，用标题作为摘要
            items.append({
                "title": title,
                "summary": title,  # 没有正文，用标题
                "read_count": item.get("pageviews", 0) or 0,
                "comment_count": item.get("comment_count", 0) or 0,
                "time_str": str(item.get("display_time", "")),
                "source": "华尔街见闻",
            })
        print(f"  [华尔街见闻-热门] 获取 {len(items)} 条")
    except Exception as e:
        print(f"  [华尔街见闻-热门] 错误: {e}")

    # 快讯（lives）有 content
    try:
        resp2 = requests.get(
            "https://api-one.wallstcn.com/apiv1/content/lives",
            params={
                "channel": "global-channel",
                "client": "pc",
                "limit": 20,
                "first_page": "true",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        data2 = resp2.json()
        live_items = data2.get("data", {}).get("items", [])
        live_count = 0
        for item in live_items:
            title = item.get("title", "")
            content = item.get("content", "")
            content_text = re.sub(r"<[^>]+>", "", content).strip()
            if not title:
                title = content_text[:60]
            if is_boring(title, content_text):
                continue
            items.append({
                "title": title,
                "summary": content_text[:200],
                "read_count": 5000,  # 快讯给个基础热度
                "comment_count": item.get("comment_count", 0) or 0,
                "time_str": str(item.get("display_time", "")),
                "source": "华尔街见闻",
            })
            live_count += 1
        print(f"  [华尔街见闻-快讯] 获取 {live_count} 条")
    except Exception as e:
        print(f"  [华尔街见闻-快讯] 错误: {e}")

    return items


def is_boring(title, summary):
    """判断是否属于公告/复盘/例行新闻，应该被过滤"""
    text = title + " " + summary
    for pattern in BORING_FILTER:
        if re.search(pattern, text):
            return True
    return False


def normalize_posts(raw_items):
    """标准化所有帖子/文章"""
    now = datetime.now()
    cutoff = now - timedelta(hours=HOURS_BACK)
    normalized = []

    for item in raw_items:
        title = item.get("title", "").strip()
        summary = item.get("summary", "").strip()

        # 跳过空标题
        if not title or len(title) < 4:
            continue

        # 过滤公告/复盘
        if is_boring(title, summary):
            continue

        # 解析时间
        t = now  # 默认当前时间
        time_str = item.get("time_str", "")
        if time_str:
            t = parse_time_str(time_str) or now

        # 跳过太旧的
        if t < cutoff:
            continue

        read_count = int(item.get("read_count", 0) or 0)
        comment_count = int(item.get("comment_count", 0) or 0)

        # 热度分数：阅读数 + 评论数*10
        hot_score = read_count + comment_count * 10

        normalized.append({
            "title": title,
            "summary": summary,
            "time": t,
            "source": item.get("source", ""),
            "read_count": read_count,
            "comment_count": comment_count,
            "hot_score": hot_score,
        })

    # 去重（按标题前20字符）
    seen = set()
    unique = []
    for n in normalized:
        key = n["title"][:20]
        if key not in seen:
            seen.add(key)
            unique.append(n)

    # 按热度排序
    unique.sort(key=lambda x: x["hot_score"], reverse=True)
    return unique


def parse_time_str(s):
    """解析时间字符串"""
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m-%d %H:%M",
        "%m月%d日 %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        pass
    return None


def extract_tags(title, summary):
    """从标题和摘要中提取板块关键词标签"""
    SECTOR_KW = {
        "AI/算力": ["算力", "AI", "人工智能", "GPU", "英伟达", "NVIDIA", "大模型", "LLM", "智能体", "智算", "Agent"],
        "CPO/光通信": ["CPO", "光模块", "光通信", "光纤", "光芯片", "硅光"],
        "存储芯片": ["存储", "HBM", "DRAM", "NAND", "美光", "闪迪", "SK海力士", "兆易创新", "澜起"],
        "PCB": ["PCB", "电路板", "HDI", "深南电路", "胜宏科技"],
        "半导体/芯片": ["半导体", "芯片", "晶圆", "光刻", "中芯国际", "台积电", "麒麟", "EDA", "封装", "流片", "代工", "寒武纪", "海光"],
        "机器人": ["机器人", "人形机器人", "Optimus", "宇树", "Figure", "具身智能"],
        "新能源": ["光伏", "储能", "锂电", "电池", "宁德时代", "比亚迪", "固态电池", "逆变器"],
        "医药": ["医药", "创新药", "CXO", "生物医药", "医疗器械", "药明", "减肥药"],
        "消费": ["消费", "猪肉", "生猪", "白酒", "食品", "养殖", "猪价"],
        "黄金": ["黄金", "贵金属", "金价", "白银", "黄金储备"],
        "汽车": ["汽车", "新能源车", "特斯拉", "智能驾驶", "小米汽车", "华为汽车"],
        "军工": ["军工", "航天", "卫星", "导弹", "国防"],
        "金融": ["银行", "券商", "保险", "金融", "降息", "加息"],
        "MLCC": ["MLCC", "被动元件", "电容", "国巨", "村田"],
        "低空经济": ["低空", "eVTOL", "无人机", "飞行汽车"],
        "量子": ["量子", "量子计算", "量子芯片"],
        "脑机接口": ["脑机", "脑机接口", "Neuralink"],
        "鸿蒙": ["鸿蒙", "华为", "欧拉", "昇腾"],
    }

    text = title + " " + summary
    tags = []
    for sector, keywords in SECTOR_KW.items():
        for kw in keywords:
            if kw in text:
                tags.append(kw)
                break

    seen = set()
    unique = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique[:4]


def determine_hot_level(title, summary, hot_score):
    """根据热度分数和内容判断等级"""
    text = title + summary
    if hot_score > 5000000:
        return "🔥 最热"
    if hot_score > 1000000:
        return "📈 热议"
    if hot_score > 100000:
        return "💬 讨论"
    for kw in ["暴增", "暴涨", "涨停", "重挫", "崩盘", "失守", "突破", "炸裂"]:
        if kw in text:
            return "📈 热议"
    return "💬 讨论"


def generate_topics(posts):
    """生成最终12条话题，优先选热度最高的讨论帖"""
    topics = posts[:TOPIC_COUNT]

    # 确保多样性：如果前12条都是同一个 source，混入其他 source
    sources = Counter(t["source"] for t in topics)
    if len(sources) == 1 and len(posts) > TOPIC_COUNT:
        # 从后面找不同 source 的替换最后2条
        dominant = list(sources.keys())[0]
        replaced = 0
        for p in posts[TOPIC_COUNT:]:
            if replaced >= 3:
                break
            if p["source"] != dominant:
                topics[-1 - replaced] = p
                replaced += 1

    return topics[:TOPIC_COUNT]


def format_topic_entry(topic):
    """将话题格式化为 JS 对象字符串"""
    title = topic["title"].replace("\\", "\\\\").replace("'", "\\'")
    summary = topic["summary"].replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")[:200]
    tags = extract_tags(topic["title"], topic["summary"])
    hot = determine_hot_level(topic["title"], topic["summary"], topic["hot_score"])
    source = topic["source"]
    time_str = topic["time"].strftime("%m/%d %H:%M")

    emoji_map = {"🔥 最热": "🔴", "📈 热议": "🟡", "💬 讨论": "🟢"}
    emoji = emoji_map.get(hot, "🟢")

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

    today_str = datetime.now().strftime("%Y-%m-%d")
    header = "// ===== 全球热点话题（每日更新） =====\n"
    header += f"// 更新日期：{today_str}\n"
    header += f"// 数据来源：东方财富股吧 + 华尔街见闻热门\n"
    header += "const HOT_TOPICS=[\n"

    entries = [format_topic_entry(t) for t in topics]
    body = ",\n".join(entries)
    footer = "\n];"

    new_array = header + body + footer

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
    print(f"=== 热门话题自动更新 v2 ===")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"回溯时间: {HOURS_BACK} 小时")
    print()

    # 1. 抓取数据
    print("📡 抓取论坛热门帖子...")
    raw_guba = fetch_guba_hot_posts()
    raw_ws = fetch_wallstreetcn_hot()

    all_raw = raw_guba + raw_ws

    # 2. 标准化+过滤
    posts = normalize_posts(all_raw)
    print(f"\n📊 过滤后有效讨论帖: {len(posts)} 条")

    if len(posts) < 3:
        print("⚠️ 帖子数量不足，可能数据源异常，跳过更新")
        sys.exit(0)

    # 3. 生成话题
    topics = generate_topics(posts)
    print(f"\n📝 生成话题: {len(topics)} 条")
    for i, t in enumerate(topics):
        tags = extract_tags(t["title"], t["summary"])
        hot = determine_hot_level(t["title"], t["summary"], t["hot_score"])
        print(f"  {i+1}. [{hot}] {t['title'][:60]} | {t['source']} | 热度:{t['hot_score']}")

    # 4. 更新 index.html
    print()
    update_index_html(topics)
    print("\n✅ 完成!")


if __name__ == "__main__":
    main()

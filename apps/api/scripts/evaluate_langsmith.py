"""Run the voice-shopping quality and performance suites in LangSmith.

The datasets contain synthetic utterances and memories only. Recommendation
evaluation reads the local seeded catalog and its PGVector embeddings, but it
does not persist sessions, messages, profile data, or orders.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import math
import os
from collections import Counter
from collections.abc import Callable, Sequence
from statistics import fmean
from time import perf_counter
from typing import Any

from langgraph.store.base import PutOp
from langgraph.store.memory import InMemoryStore
from langsmith import Client, tracing_context

from voice_shopping_api.agents import model as agent_model
from voice_shopping_api.agents.graph import shopping_workflow
from voice_shopping_api.agents.nodes.intent import recognize_intent
from voice_shopping_api.agents.service import _catalog, _taxonomy_context
from voice_shopping_api.agents.state import ShoppingRuntimeDependencies, ShoppingState
from voice_shopping_api.agents.store import _embed_texts
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.database import async_session_factory, engine

INTENT_DATASET = "voice-shopping-intent-routing-v1"
RECOMMENDATION_DATASET = "voice-shopping-recommendation-perf-v1"
MEMORY_DATASET = "voice-shopping-memory-recall-v2"

INTENT_LABELS = (
    "REQUIREMENT_CLARIFICATION",
    "PRODUCT_RECOMMENDATION",
    "PRODUCT_COMPARE",
    "PRODUCT_ORDER",
    "CHAT",
    "UNSUPPORTED_REQUEST",
)

# Current Beijing-region, non-batch, <=32K rates in CNY per million tokens.
# Source checked on 2026-08-13:
# https://help.aliyun.com/zh/model-studio/model-pricing
CHAT_INPUT_CNY_PER_MILLION = 0.2
CHAT_OUTPUT_CNY_PER_MILLION = 0.8
EMBEDDING_INPUT_CNY_PER_MILLION = 0.5

_USAGE_EVENTS: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "voice_shopping_eval_usage", default=None
)
_ORIGINAL_MARK_MODEL_RUN = agent_model._mark_model_run
_TAXONOMY_CONTEXT: dict[str, Any] = {}
_MEMORY_STORE: InMemoryStore | None = None
_MEMORY_NAMESPACE = ("eval", "voice-shopping", "semantic")


def _record_model_usage(model: str, usage: dict[str, Any] | None = None) -> None:
    """Capture per-target provider usage while preserving production tracing."""
    events = _USAGE_EVENTS.get()
    if events is not None and usage:
        events.append({"model": model, "usage": dict(usage)})
    _ORIGINAL_MARK_MODEL_RUN(model, usage)


agent_model._mark_model_run = _record_model_usage


def _dummy_cards() -> list[dict[str, Any]]:
    return [
        {"productId": "eval-product-1", "name": "耳机甲", "price": 599, "stock": 12},
        {"productId": "eval-product-2", "name": "耳机乙", "price": 899, "stock": 8},
        {"productId": "eval-product-3", "name": "耳机丙", "price": 1299, "stock": 4},
    ]


def _intent_example(
    utterance: str,
    intent_type: str,
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "inputs": {"utterance": utterance, "state": state or {}},
        "outputs": {"intent_type": intent_type},
        "metadata": {"intent_type": intent_type, "synthetic": True},
    }


def _clarification_state(category: str, slot: str) -> dict[str, Any]:
    return {
        "product_category": category,
        "pending_question": {"slot": slot, "question": f"请补充 {slot}"},
        "slots": {},
    }


def _intent_examples() -> list[dict[str, Any]]:
    cards = _dummy_cards()
    recommendation = [
        "想买一副蓝牙入耳式耳机，帮我推荐一下",
        "预算五百，给我挑个降噪耳机",
        "想选一支哑光番茄红口红",
        "推荐一双女款38码公路跑鞋",
        "我需要一块自动机械表",
        "帮我选个半自动咖啡机",
        "想买一个1.5升能保温的电热水壶",
        "嗯啊，帮我看看头戴式有线耳机",
        "算了，不买耳机了，改买口红",
        "换个品类，推荐点别的",
        "帮我挑双越野跑鞋，男款42码",
        "家里想添个胶囊咖啡机",
        "需要一只石英表",
        "来个珊瑚色缎光口红",
        "蓝牙头戴式耳机有推荐吗",
    ]
    compare = [
        "这三款有什么区别",
        "第一款多少钱",
        "耳机乙还有库存吗",
        "比较一下前两款",
        "给我介绍一下这款",
        "耳机甲怎么样",
        "哪款续航更长",
        "索尼 XM5 多少钱",
        "对比一下蓝牙耳机和有线耳机",
        "这款咖啡机库存还有多少",
        "第二双跑鞋怎么样",
        "帮我查查口红甲的情况",
        "第一款和第三款的区别是什么",
        "这几个哪个更便宜",
        "把这几款的参数对比一下",
    ]
    order_states = [
        ({"previous_product_cards": cards}, "买第二款"),
        ({"previous_product_cards": cards}, "就要第一款，帮我下单"),
        ({"previous_product_cards": cards}, "耳机甲给我来一个"),
        ({"previous_product_cards": cards}, "这款不错，下单吧"),
        ({"previous_product_cards": cards}, "我买第三个"),
        ({"previous_product_cards": cards}, "就买耳机乙"),
        ({"previous_product_cards": cards}, "帮我买第一件"),
        ({"previous_product_cards": cards}, "第二款直接下单"),
        ({"previous_product_cards": cards}, "那款耳机甲我要了"),
        ({"previous_product_cards": cards}, "给我下单第三款"),
        ({"pending_order": {"id": "eval-order", "status": "pending"}}, "确认下单"),
        ({"pending_order": {"id": "eval-order", "status": "pending"}}, "确定，确认购买"),
        ({"pending_order": {"id": "eval-order", "status": "pending"}}, "就这样确认下单"),
        ({"pending_order": {"id": "eval-order", "status": "pending"}}, "取消订单"),
        ({"pending_order": {"id": "eval-order", "status": "pending"}}, "不要了，取消订单"),
    ]
    clarification = [
        ("蓝牙的", "HEADPHONES", "connectivity"),
        ("入耳式", "HEADPHONES", "form"),
        ("38码", "RUNNING_SHOES", "size"),
        ("女款", "RUNNING_SHOES", "gender"),
        ("主要跑公路", "RUNNING_SHOES", "terrain"),
        ("1.5升", "ELECTRIC_KETTLE", "capacityL"),
        ("哑光的", "LIPSTICK", "finish"),
        ("番茄红", "LIPSTICK", "shade"),
        ("半自动", "COFFEE_MACHINE", "type"),
        ("机械的吧", "WATCHES", "movement"),
        ("预算八百", "HEADPHONES", "budgetMax"),
        ("要保温的", "ELECTRIC_KETTLE", "keepWarm"),
        ("选有降噪的", "HEADPHONES", "noiseCancellation"),
        ("42码", "RUNNING_SHOES", "size"),
        ("胶囊式", "COFFEE_MACHINE", "type"),
    ]
    chat = [
        "你好",
        "谢谢你的推荐",
        "早上好",
        "晚安",
        "你叫什么名字",
        "今天心情怎么样",
        "哈哈，你真有意思",
        "再见",
        "辛苦了",
        "很高兴认识你",
        "嗨，在吗",
        "你是机器人吗",
        "刚才讲得很清楚，谢谢",
        "祝你周末愉快",
        "你好呀，很高兴见到你",
    ]
    unsupported = [
        "帮我订明天去上海的机票",
        "写一首关于春天的诗",
        "查一下今天的天气",
        "帮我转账一万元",
        "给我诊断一下头痛",
        "帮我写一段 Python 代码",
        "播放一首歌",
        "预订今晚的酒店",
        "今天炒股买哪只",
        "帮我查快递到哪了",
        "帮我叫一份外卖",
        "设置明早七点的闹钟",
        "把这段英文翻译成中文",
        "生成一张海边日落图片",
        "告诉我怎么破解银行卡密码",
    ]

    examples = [
        *[_intent_example(value, "PRODUCT_RECOMMENDATION") for value in recommendation],
        *[
            _intent_example(value, "PRODUCT_COMPARE", state={"previous_product_cards": cards})
            for value in compare
        ],
        *[_intent_example(value, "PRODUCT_ORDER", state=state) for state, value in order_states],
        *[
            _intent_example(
                value,
                "REQUIREMENT_CLARIFICATION",
                state=_clarification_state(category, slot),
            )
            for value, category, slot in clarification
        ],
        *[_intent_example(value, "CHAT") for value in chat],
        *[_intent_example(value, "UNSUPPORTED_REQUEST") for value in unsupported],
    ]
    counts = Counter(example["outputs"]["intent_type"] for example in examples)
    if set(counts) != set(INTENT_LABELS) or any(counts[label] != 15 for label in INTENT_LABELS):
        raise AssertionError(f"Intent dataset must have 15 examples per class: {counts}")
    return examples


def _recommendation_examples() -> list[dict[str, Any]]:
    utterances = [
        ("headphones-bluetooth", "给我推荐蓝牙入耳式耳机，预算2000元以内"),
        ("headphones-wired", "想买有线头戴式耳机，预算4000元"),
        ("lipstick-matte", "推荐哑光番茄红口红，预算500元"),
        ("lipstick-satin", "想买缎光奶茶色口红，预算500元"),
        ("watches-automatic", "推荐自动机械表，预算8000元"),
        ("watches-quartz", "想买石英表，预算2000元"),
        ("coffee-capsule", "帮我选胶囊咖啡机，预算3000元"),
        ("coffee-semi-auto", "推荐半自动咖啡机，预算12000元"),
        ("kettle-1-5", "推荐1.5升电热水壶，预算1000元"),
        ("kettle-1-7", "想买1.7升电热水壶，预算2000元"),
        ("shoes-road", "推荐女款38码公路跑鞋，预算2000元"),
        ("shoes-trail", "想买男款42码越野跑鞋，预算2500元"),
    ]
    return [
        {
            "inputs": {"case_id": case_id, "utterance": utterance},
            "outputs": {"minimum_cards": 1},
            "metadata": {"synthetic": True},
        }
        for case_id, utterance in utterances
    ]


def _memory_facts() -> list[tuple[str, str, str]]:
    """Return 200 scoped facts with paraphrased queries for Top-5 recall.

    Each shopping scenario contributes ten deliberately similar memories. The
    query must therefore identify both the scenario and the requested
    preference instead of succeeding from a category keyword alone.
    """
    templates: dict[str, list[tuple[str, str, str]]] = {
        "headphones": [
            ("form", "佩戴形态偏好{form}", "喜欢哪种佩戴形态"),
            ("connectivity", "连接方式要求{connectivity}", "连接方式有什么要求"),
            ("budget", "预算上限为{budget}元", "最高预算是多少"),
            ("noise", "降噪需求是{noise}", "需要怎样的降噪能力"),
            ("battery", "续航至少要{battery}小时", "最低续航要求是多少"),
            ("comfort", "舒适性方面关注{comfort}", "佩戴舒适性最关注什么"),
            ("microphone", "麦克风要求{microphone}", "对麦克风有什么要求"),
            ("water", "防水要求为{water}", "需要什么防水能力"),
            ("color", "外观颜色倾向{color}", "偏爱什么外观颜色"),
            ("avoid", "明确不要{avoid}", "选购时要避开什么"),
        ],
        "lipstick": [
            ("finish", "妆效偏好{finish}", "喜欢什么妆效"),
            ("shade", "色号倾向{shade}", "偏爱哪种色号"),
            ("skin", "肤质相关需求是{skin}", "肤质带来什么要求"),
            ("budget", "预算上限为{budget}元", "最高预算是多少"),
            ("occasion", "主要使用场合是{occasion}", "主要在哪种场合使用"),
            ("undertone", "更适合{undertone}", "适合什么肤色基调"),
            ("coverage", "显色和遮盖偏好{coverage}", "需要怎样的显色遮盖"),
            ("fragrance", "香味要求是{fragrance}", "对香味有什么要求"),
            ("durability", "持妆需求为{durability}", "需要保持多久"),
            ("avoid", "明确避开{avoid}", "选购时要避开什么"),
        ],
        "shoes": [
            ("size", "跑鞋尺码是{size}码", "穿多大尺码"),
            ("gender", "版型选择{gender}", "需要什么版型"),
            ("terrain", "主要适用路面是{terrain}", "主要跑什么路面"),
            ("cushion", "缓震偏好{cushion}", "需要怎样的缓震"),
            ("foot", "足型和支撑需求为{foot}", "足型需要什么支撑"),
            ("drop", "前后掌落差偏好{drop}", "喜欢多大落差"),
            ("budget", "预算上限为{budget}元", "最高预算是多少"),
            ("distance", "单次距离通常是{distance}", "通常一次跑多远"),
            ("upper", "鞋面需求是{upper}", "鞋面最看重什么"),
            ("avoid", "明确不要{avoid}", "选鞋时要避开什么"),
        ],
        "watch": [
            ("movement", "机芯偏好{movement}", "偏爱哪种机芯"),
            ("budget", "预算上限为{budget}元", "最高预算是多少"),
            ("material", "表壳材质倾向{material}", "喜欢什么表壳材质"),
            ("water", "防水要求为{water}", "需要什么防水能力"),
            ("diameter", "表径偏好{diameter}", "适合多大表径"),
            ("style", "外观风格偏好{style}", "喜欢什么外观风格"),
            ("strap", "表带倾向{strap}", "偏好什么表带"),
            ("feature", "功能需求是{feature}", "最需要哪项功能"),
            ("weight", "重量要求为{weight}", "对重量有什么要求"),
            ("avoid", "明确不要{avoid}", "选表时要避开什么"),
        ],
        "coffee": [
            ("type", "机器类型偏好{type}", "适合哪种机器类型"),
            ("budget", "预算上限为{budget}元", "最高预算是多少"),
            ("pressure", "泵压要求为{pressure}", "需要多大泵压"),
            ("tank", "水箱至少要{tank}", "水箱最低多大"),
            ("steam", "奶泡需求是{steam}", "是否需要奶泡功能"),
            ("drink", "常做的饮品是{drink}", "主要制作什么饮品"),
            ("cleaning", "清洁偏好{cleaning}", "清洁方面有什么要求"),
            ("footprint", "摆放空间限制为{footprint}", "机器尺寸受什么限制"),
            ("volume", "每天预计制作{volume}", "每天大约做多少杯"),
            ("avoid", "明确不要{avoid}", "选购时要避开什么"),
        ],
        "kettle": [
            ("capacity", "容量需求是{capacity}", "需要多大容量"),
            ("budget", "预算上限为{budget}元", "最高预算是多少"),
            ("temperature", "温控需求是{temperature}", "需要怎样的温控"),
            ("warm", "保温需求为{warm}", "需要怎样的保温能力"),
            ("material", "内胆材质偏好{material}", "喜欢什么内胆材质"),
            ("noise", "工作声音要求{noise}", "对烧水声音有什么要求"),
            ("spout", "出水设计偏好{spout}", "喜欢怎样的出水设计"),
            ("safety", "安全功能要求{safety}", "最需要什么安全功能"),
            ("cleaning", "清洁需求是{cleaning}", "清洁方面有什么要求"),
            ("avoid", "明确不要{avoid}", "选购时要避开什么"),
        ],
    }
    scenarios = [
        {
            "key": "metro-commute-headphones",
            "category": "headphones",
            "fact_context": "用户为工作日地铁通勤挑耳机时",
            "query_context": "我坐地铁上下班用的耳机",
            "form": "入耳式",
            "connectivity": "蓝牙双设备连接",
            "budget": 800,
            "noise": "强主动降噪",
            "battery": 30,
            "comfort": "单耳重量不超过6克",
            "microphone": "嘈杂车厢里通话清晰",
            "water": "至少IPX4",
            "color": "深灰色",
            "avoid": "夹耳和听诊器效应",
        },
        {
            "key": "office-meeting-headphones",
            "category": "headphones",
            "fact_context": "用户为办公室视频会议挑耳机时",
            "query_context": "我开远程会议用的耳机",
            "form": "头戴式",
            "connectivity": "无线并保留3.5毫米有线",
            "budget": 1500,
            "noise": "无需强降噪但要隔绝人声",
            "battery": 45,
            "comfort": "透气耳罩适合全天佩戴",
            "microphone": "带可翻转静音杆麦",
            "water": "不要求防水",
            "color": "银白色",
            "avoid": "夸张的游戏灯效",
        },
        {
            "key": "father-tv-headphones",
            "category": "headphones",
            "fact_context": "用户给父亲看电视配耳机时",
            "query_context": "给爸爸看电视准备的耳机",
            "form": "轻量头戴式",
            "connectivity": "低延迟2.4G连接",
            "budget": 600,
            "noise": "不需要主动降噪",
            "battery": 20,
            "comfort": "眼镜腿处不能有明显压迫",
            "microphone": "不需要麦克风",
            "water": "不要求防水",
            "color": "黑色",
            "avoid": "复杂触控手势",
        },
        {
            "key": "partner-flight-headphones",
            "category": "headphones",
            "fact_context": "用户给伴侣长途飞行准备耳机时",
            "query_context": "伴侣坐长途飞机用的耳机",
            "form": "小巧入耳式",
            "connectivity": "蓝牙并支持航空转接",
            "budget": 2000,
            "noise": "针对发动机低频的强降噪",
            "battery": 36,
            "comfort": "侧睡时不硌耳朵",
            "microphone": "能抑制风噪",
            "water": "至少IPX4",
            "color": "珍珠白",
            "avoid": "续航不足一个航程",
        },
        {
            "key": "daily-office-lipstick",
            "category": "lipstick",
            "fact_context": "用户为日常上班选口红时",
            "query_context": "我工作日通勤涂的口红",
            "finish": "柔雾哑光",
            "shade": "低饱和奶茶色",
            "skin": "干唇需要滋润打底",
            "budget": 300,
            "occasion": "办公室日常淡妆",
            "undertone": "暖黄皮",
            "coverage": "薄涂也能均匀显色",
            "fragrance": "无明显香精味",
            "durability": "午饭后只需补一次",
            "avoid": "荧光橘色",
        },
        {
            "key": "mother-birthday-lipstick",
            "category": "lipstick",
            "fact_context": "用户给母亲挑生日口红时",
            "query_context": "送妈妈生日礼物的口红",
            "finish": "滋润缎光",
            "shade": "端庄豆沙玫瑰色",
            "skin": "成熟唇纹需要顺滑不显纹",
            "budget": 500,
            "occasion": "聚会和正式宴席",
            "undertone": "中性偏暖肤色",
            "coverage": "一遍即可遮住原生唇色",
            "fragrance": "淡雅花香可以接受",
            "durability": "至少保持六小时",
            "avoid": "过深的姨妈色",
        },
        {
            "key": "partner-dinner-lipstick",
            "category": "lipstick",
            "fact_context": "用户给伴侣准备周年晚宴口红时",
            "query_context": "周年纪念晚餐要用的口红",
            "finish": "镜面水光",
            "shade": "明亮正红色",
            "skin": "正常唇况但容易脱妆",
            "budget": 450,
            "occasion": "夜间正式晚宴",
            "undertone": "冷白皮",
            "coverage": "高饱和且边缘利落",
            "fragrance": "不要甜腻果香",
            "durability": "喝酒后仍保留底色",
            "avoid": "偏棕的土色",
        },
        {
            "key": "road-10k-shoes",
            "category": "shoes",
            "fact_context": "用户为每周公路十公里训练选跑鞋时",
            "query_context": "我每周跑公路十公里穿的鞋",
            "size": 42,
            "gender": "男款或中性版",
            "terrain": "柏油公路",
            "cushion": "中等缓震并有回弹",
            "foot": "正常足弓使用中性支撑",
            "drop": "8毫米左右",
            "budget": 1300,
            "distance": "8到12公里",
            "upper": "夏天透气",
            "avoid": "过软且卸力的厚底",
        },
        {
            "key": "weekend-trail-shoes",
            "category": "shoes",
            "fact_context": "用户为周末山地越野选跑鞋时",
            "query_context": "我周末跑山路穿的越野鞋",
            "size": 43,
            "gender": "男款",
            "terrain": "碎石和泥地山路",
            "cushion": "适中缓震并重视路感",
            "foot": "前掌偏宽需要宽楦",
            "drop": "6毫米左右",
            "budget": 1600,
            "distance": "15到25公里",
            "upper": "防刮并快速排水",
            "avoid": "湿地抓地差的浅齿外底",
        },
        {
            "key": "father-walking-shoes",
            "category": "shoes",
            "fact_context": "用户给父亲康复快走选运动鞋时",
            "query_context": "爸爸康复快走穿的运动鞋",
            "size": 41,
            "gender": "男款宽楦",
            "terrain": "公园塑胶步道",
            "cushion": "高缓震但落地稳定",
            "foot": "扁平足需要足弓支撑",
            "drop": "10毫米左右",
            "budget": 1000,
            "distance": "每天3到5公里",
            "upper": "鞋口柔软方便穿脱",
            "avoid": "摇晃明显的竞速结构",
        },
        {
            "key": "partner-marathon-shoes",
            "category": "shoes",
            "fact_context": "用户给伴侣备战马拉松选跑鞋时",
            "query_context": "伴侣准备全马比赛穿的鞋",
            "size": 38,
            "gender": "女款",
            "terrain": "城市公路赛道",
            "cushion": "长距离高缓震并保留推进感",
            "foot": "轻微内旋需要稳定引导",
            "drop": "8毫米",
            "budget": 2000,
            "distance": "30到42公里",
            "upper": "轻薄并牢固锁定中足",
            "avoid": "未经训练适应的激进碳板",
        },
        {
            "key": "self-business-watch",
            "category": "watch",
            "fact_context": "用户为商务会议选日常手表时",
            "query_context": "我参加商务会议戴的表",
            "movement": "自动机械机芯",
            "budget": 5000,
            "material": "拉丝不锈钢",
            "water": "至少50米防水",
            "diameter": "39到41毫米",
            "style": "简洁三针商务风",
            "strap": "深棕色皮带",
            "feature": "带日期显示",
            "weight": "不超过120克",
            "avoid": "夸张镂空和超大表盘",
        },
        {
            "key": "father-swim-watch",
            "category": "watch",
            "fact_context": "用户给父亲游泳锻炼选手表时",
            "query_context": "爸爸游泳训练要戴的表",
            "movement": "太阳能石英机芯",
            "budget": 3000,
            "material": "轻量钛合金",
            "water": "至少200米防水",
            "diameter": "42毫米左右",
            "style": "清晰易读的运动风",
            "strap": "防水橡胶表带",
            "feature": "旋入式表冠和夜光刻度",
            "weight": "尽量低于100克",
            "avoid": "遇水易坏的皮表带",
        },
        {
            "key": "partner-travel-watch",
            "category": "watch",
            "fact_context": "用户给伴侣跨国旅行选手表时",
            "query_context": "伴侣出国旅行佩戴的表",
            "movement": "高精度石英机芯",
            "budget": 4000,
            "material": "耐刮精钢表壳",
            "water": "100米防水",
            "diameter": "36到38毫米",
            "style": "简约复古旅行风",
            "strap": "可快拆钢带",
            "feature": "双时区显示",
            "weight": "低于110克",
            "avoid": "需要频繁手动校时的款式",
        },
        {
            "key": "home-latte-coffee",
            "category": "coffee",
            "fact_context": "用户为家里每天做拿铁选咖啡机时",
            "query_context": "我在家每天做拿铁的咖啡机",
            "type": "半自动意式",
            "budget": 3500,
            "pressure": "15巴以上",
            "tank": "1500毫升",
            "steam": "需要有力的蒸汽棒",
            "drink": "拿铁和卡布奇诺",
            "cleaning": "冲煮头和接水盘易拆洗",
            "footprint": "宽度不超过30厘米",
            "volume": "两到四杯",
            "avoid": "只能使用专用胶囊",
        },
        {
            "key": "office-pod-coffee",
            "category": "coffee",
            "fact_context": "用户为小办公室添置共享咖啡机时",
            "query_context": "我们小办公室共用的咖啡机",
            "type": "全自动豆机",
            "budget": 8000,
            "pressure": "至少19巴",
            "tank": "2000毫升",
            "steam": "要自动奶泡系统",
            "drink": "美式和拿铁",
            "cleaning": "支持自动冲洗提醒",
            "footprint": "可放进40厘米深的茶水柜",
            "volume": "十五到二十杯",
            "avoid": "每杯都要手动压粉",
        },
        {
            "key": "parents-capsule-coffee",
            "category": "coffee",
            "fact_context": "用户给父母准备操作简单的咖啡机时",
            "query_context": "给爸妈用的简易咖啡机",
            "type": "一键胶囊式",
            "budget": 1200,
            "pressure": "15巴",
            "tank": "800毫升",
            "steam": "不需要蒸汽棒",
            "drink": "浓缩和大杯咖啡",
            "cleaning": "只需清空胶囊盒和水盘",
            "footprint": "机身宽度不超过15厘米",
            "volume": "一到两杯",
            "avoid": "菜单层级复杂的触摸屏",
        },
        {
            "key": "home-tea-kettle",
            "category": "kettle",
            "fact_context": "用户为家中泡茶选电热水壶时",
            "query_context": "我在家泡茶用的电热水壶",
            "capacity": "1.5升",
            "budget": 600,
            "temperature": "40到100度多档调温",
            "warm": "保温两小时",
            "material": "食品级不锈钢内胆",
            "noise": "沸腾声尽量小",
            "spout": "细流防滴漏壶嘴",
            "safety": "防干烧和自动断电",
            "cleaning": "大口径方便除水垢",
            "avoid": "内壁接触塑料",
        },
        {
            "key": "office-kettle",
            "category": "kettle",
            "fact_context": "用户为办公室茶水间选电热水壶时",
            "query_context": "我们办公室茶水间用的水壶",
            "capacity": "2升",
            "budget": 500,
            "temperature": "一键沸腾即可",
            "warm": "至少保温四小时",
            "material": "双层不锈钢",
            "noise": "提示音可以关闭",
            "spout": "大水流且不挂水",
            "safety": "防烫外壳和童锁",
            "cleaning": "可拆卸滤网",
            "avoid": "玻璃壶身易碰碎",
        },
        {
            "key": "dorm-kettle",
            "category": "kettle",
            "fact_context": "用户给住宿舍的妹妹选电热水壶时",
            "query_context": "妹妹住校宿舍要用的水壶",
            "capacity": "1升",
            "budget": 250,
            "temperature": "支持45度和100度两档",
            "warm": "无需长时间保温",
            "material": "轻量不锈钢内胆",
            "noise": "夜间烧水不要有蜂鸣",
            "spout": "倒水时不飞溅",
            "safety": "底座稳定并自动断电",
            "cleaning": "壶盖能完全打开",
            "avoid": "功率过高导致宿舍跳闸",
        },
    ]
    facts = [
        (
            f"{scenario['key']}-{dimension}",
            f"{scenario['fact_context']}，{fact_template.format(**scenario)}。",
            f"{scenario['query_context']}，{query_template}？",
        )
        for scenario in scenarios
        for dimension, fact_template, query_template in templates[str(scenario["category"])]
    ]
    keys = [key for key, _, _ in facts]
    if len(facts) != 200 or len(set(keys)) != 200:
        raise AssertionError(f"Memory v2 must contain 200 unique facts, got {len(facts)}")
    return facts


def _memory_examples() -> list[dict[str, Any]]:
    return [
        {
            "inputs": {"query": query},
            "outputs": {"expected_key": key},
            "metadata": {"synthetic": True, "memory_key": key},
        }
        for key, _, query in _memory_facts()
    ]


def _usage_totals(events: Sequence[dict[str, Any]]) -> dict[str, int | float]:
    settings = get_settings()
    chat_input = 0
    chat_output = 0
    embedding_input = 0
    for event in events:
        usage = event["usage"]
        model = event["model"]
        input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        if model == settings.embedding_model:
            embedding_input += input_tokens or int(usage.get("total_tokens") or 0)
        else:
            chat_input += input_tokens
            chat_output += output_tokens
    cost_cny = (
        chat_input * CHAT_INPUT_CNY_PER_MILLION
        + chat_output * CHAT_OUTPUT_CNY_PER_MILLION
        + embedding_input * EMBEDDING_INPUT_CNY_PER_MILLION
    ) / 1_000_000
    return {
        "chat_input_tokens": chat_input,
        "chat_output_tokens": chat_output,
        "embedding_input_tokens": embedding_input,
        "total_tokens": chat_input + chat_output + embedding_input,
        "cost_cny": cost_cny,
    }


async def _intent_target(inputs: dict[str, Any]) -> dict[str, Any]:
    state: ShoppingState = {
        **_TAXONOMY_CONTEXT,
        **dict(inputs.get("state") or {}),
        "utterance": str(inputs["utterance"]),
        "conversation_history": list((inputs.get("state") or {}).get("conversation_history", [])),
        "model_enabled": True,
        "slots": dict((inputs.get("state") or {}).get("slots", {})),
    }
    result = await recognize_intent(state)
    intent = dict(result.get("intent") or {})
    return {
        "predicted_intent": intent.get("type"),
        "confidence": intent.get("confidence"),
        "action": intent.get("action"),
        "product_category": intent.get("product_category"),
    }


async def _recommendation_target(inputs: dict[str, Any]) -> dict[str, Any]:
    usage_events: list[dict[str, Any]] = []
    token = _USAGE_EVENTS.set(usage_events)
    started = perf_counter()
    try:
        async with async_session_factory() as session:

            async def catalog_loader(
                query: str, enabled: bool, filters: dict[str, Any]
            ) -> list[dict[str, Any]]:
                return await _catalog(session, query, enabled, filters)

            case_id = str(inputs["case_id"])
            state: ShoppingState = {
                **_TAXONOMY_CONTEXT,
                "session_id": f"langsmith-eval-{case_id}",
                "turn_id": "single-recommendation",
                "user_id": "synthetic-langsmith-eval-user",
                "utterance": str(inputs["utterance"]),
                "conversation_history": [],
                "model_enabled": True,
                "slots": {},
                "pending_question": None,
                "user_profile_updates": {},
                "user_profile_snapshot": {},
                "previous_product_cards": [],
                "product_cards": [],
                "pending_order": None,
                "catalog_products": [],
            }
            result = await shopping_workflow.ainvoke(
                state,
                config={
                    "run_name": "voice-shopping-recommendation-eval",
                    "tags": ["langsmith-eval", "single-recommendation"],
                    "metadata": {"case_id": case_id, "synthetic": True},
                },
                context=ShoppingRuntimeDependencies(catalog_loader=catalog_loader),
            )
        elapsed = perf_counter() - started
        totals = _usage_totals(usage_events)
        cards = list(result.get("product_cards") or [])
        return {
            "latency_s": elapsed,
            "cost_cny": totals["cost_cny"],
            "usage": totals,
            "card_count": len(cards),
            "product_ids": [str(card.get("productId")) for card in cards],
            "intent": (result.get("intent") or {}).get("type"),
            "clarification_status": result.get("clarification_status"),
        }
    finally:
        _USAGE_EVENTS.reset(token)


async def _memory_target(inputs: dict[str, Any]) -> dict[str, Any]:
    if _MEMORY_STORE is None:
        raise RuntimeError("Memory store was not prepared")
    started = perf_counter()
    # The evaluation target itself is the useful LangSmith row. Suppress the
    # provider child span here so one Recall@5 suite consumes one trace per
    # query instead of two; provider usage is not part of this metric.
    with tracing_context(enabled=False):
        results = await _MEMORY_STORE.asearch(
            _MEMORY_NAMESPACE,
            query=str(inputs["query"]),
            limit=5,
        )
    return {
        "retrieved_keys": [item.key for item in results],
        "scores": [float(item.score) if item.score is not None else None for item in results],
        "latency_s": perf_counter() - started,
    }


def _intent_exact_match(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict:
    predicted = outputs.get("predicted_intent")
    expected = reference_outputs.get("intent_type")
    return {
        "key": "intent_exact_match",
        "score": int(predicted == expected),
        "comment": None if predicted == expected else f"expected={expected}, predicted={predicted}",
    }


def _macro_f1_values(
    outputs: Sequence[dict[str, Any]], reference_outputs: Sequence[dict[str, Any]]
) -> tuple[float, dict[str, float]]:
    predictions = [output.get("predicted_intent") for output in outputs]
    expected = [reference.get("intent_type") for reference in reference_outputs]
    per_class: dict[str, float] = {}
    for label in INTENT_LABELS:
        pairs = zip(predictions, expected, strict=True)
        true_positive = sum(p == label and e == label for p, e in pairs)
        pairs = zip(predictions, expected, strict=True)
        false_positive = sum(p == label and e != label for p, e in pairs)
        pairs = zip(predictions, expected, strict=True)
        false_negative = sum(p != label and e == label for p, e in pairs)
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0
        )
        per_class[label] = (
            2 * precision * recall / (precision + recall) if precision + recall else 0
        )
    return fmean(per_class.values()), per_class


def _intent_macro_f1(
    outputs: list[dict[str, Any]], reference_outputs: list[dict[str, Any]]
) -> dict:
    score, per_class = _macro_f1_values(outputs, reference_outputs)
    return {
        "key": "intent_routing_macro_f1",
        "score": score,
        "comment": json.dumps(per_class, ensure_ascii=False, sort_keys=True),
    }


def _recommendation_succeeded(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict:
    minimum_cards = int(reference_outputs.get("minimum_cards", 1))
    succeeded = (
        outputs.get("intent") == "PRODUCT_RECOMMENDATION"
        and outputs.get("clarification_status") == "READY"
        and int(outputs.get("card_count") or 0) >= minimum_cards
    )
    return {"key": "recommendation_succeeded", "score": int(succeeded)}


def _recommendation_latency(outputs: dict[str, Any], _: dict[str, Any]) -> dict:
    return {"key": "recommendation_latency_s", "score": outputs.get("latency_s")}


def _recommendation_cost(outputs: dict[str, Any], _: dict[str, Any]) -> dict:
    return {"key": "recommendation_cost_cny", "score": outputs.get("cost_cny")}


def _average_output_metric(key: str, feedback_key: str) -> Callable:
    def evaluator(outputs: list[dict[str, Any]], reference_outputs: list[dict[str, Any]]) -> dict:
        del reference_outputs
        values = [float(output[key]) for output in outputs if output.get(key) is not None]
        return {"key": feedback_key, "score": fmean(values) if values else None}

    evaluator.__name__ = feedback_key
    return evaluator


def _successful_recommendation(output: dict[str, Any]) -> bool:
    return (
        output.get("intent") == "PRODUCT_RECOMMENDATION"
        and output.get("clarification_status") == "READY"
        and int(output.get("card_count") or 0) >= 1
    )


def _average_successful_output_metric(key: str, feedback_key: str) -> Callable:
    def evaluator(outputs: list[dict[str, Any]], reference_outputs: list[dict[str, Any]]) -> dict:
        del reference_outputs
        values = [
            float(output[key])
            for output in outputs
            if _successful_recommendation(output) and output.get(key) is not None
        ]
        return {"key": feedback_key, "score": fmean(values) if values else None}

    evaluator.__name__ = feedback_key
    return evaluator


def _recommendation_success_rate_summary(
    outputs: list[dict[str, Any]], reference_outputs: list[dict[str, Any]]
) -> dict:
    del reference_outputs
    scores = [int(_successful_recommendation(output)) for output in outputs]
    return {"key": "recommendation_success_rate", "score": fmean(scores) if scores else None}


def _memory_recall_at_5(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict:
    expected = reference_outputs.get("expected_key")
    recalled = expected in list(outputs.get("retrieved_keys") or [])[:5]
    return {"key": "memory_recall_at_5", "score": int(recalled)}


def _mean_memory_recall_at_5(
    outputs: list[dict[str, Any]], reference_outputs: list[dict[str, Any]]
) -> dict:
    scores = [
        int(reference.get("expected_key") in list(output.get("retrieved_keys") or [])[:5])
        for output, reference in zip(outputs, reference_outputs, strict=True)
    ]
    return {"key": "mean_memory_recall_at_5", "score": fmean(scores) if scores else None}


def _ensure_dataset(
    client: Client,
    *,
    name: str,
    description: str,
    examples: list[dict[str, Any]],
) -> str:
    existing = list(client.list_datasets(dataset_name=name, limit=1))
    if existing:
        dataset = existing[0]
        count = sum(1 for _ in client.list_examples(dataset_id=dataset.id))
        if count != len(examples):
            raise RuntimeError(
                f"Dataset {name!r} already has {count} examples; expected {len(examples)}. "
                "Create a new version instead of mixing labels."
            )
        print(f"Using existing LangSmith dataset {name} ({count} examples)", flush=True)
        return str(dataset.id)
    dataset = client.create_dataset(
        name,
        description=description,
        metadata={"synthetic": True, "repository": "voice-shopping-agents", "version": 1},
    )
    client.create_examples(dataset_id=dataset.id, examples=examples, max_concurrency=3)
    print(f"Created LangSmith dataset {name} ({len(examples)} examples)", flush=True)
    return str(dataset.id)


async def _prepare_memory_store() -> None:
    global _MEMORY_STORE
    settings = get_settings()
    store = InMemoryStore(
        index={
            "dims": settings.langgraph_store_embedding_dimensions,
            "embed": _embed_texts,
            "fields": ["text"],
        }
    )
    # Index construction is setup rather than a measured retrieval. Keep those
    # embedding spans out of the experiment so Recall@5 contains query work only.
    with tracing_context(enabled=False):
        facts = _memory_facts()
        # Bound provider concurrency while avoiding 200 sequential round trips.
        for offset in range(0, len(facts), 10):
            await store.abatch(
                [
                    PutOp(_MEMORY_NAMESPACE, key, {"text": fact})
                    for key, fact, _ in facts[offset : offset + 10]
                ]
            )
    _MEMORY_STORE = store


def _result_rows(result: Any) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    rows = []
    for row in result._results:
        run = row["run"]
        example = row["example"]
        rows.append(
            (dict(run.outputs or {}), dict(example.outputs or {}), dict(example.inputs or {}))
        )
    return rows


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


async def _run_intent_suite(client: Client) -> dict[str, Any]:
    examples = _intent_examples()
    dataset_id = _ensure_dataset(
        client,
        name=INTENT_DATASET,
        description="90 balanced synthetic cases for the six production intent routes.",
        examples=examples,
    )
    result = await client.aevaluate(
        _intent_target,
        data=INTENT_DATASET,
        evaluators=[_intent_exact_match],
        summary_evaluators=[_intent_macro_f1],
        experiment_prefix="voice-shopping-intent-routing",
        description="Production intent node with Qwen, taxonomy, state guards, and slot ownership.",
        metadata={"model": get_settings().agent_model, "dataset_version": 1},
        max_concurrency=6,
    )
    await result.wait()
    rows = _result_rows(result)
    outputs = [row[0] for row in rows]
    references = [row[1] for row in rows]
    macro_f1, per_class = _macro_f1_values(outputs, references)
    confusion: dict[str, dict[str, int]] = {label: {} for label in INTENT_LABELS}
    failures = []
    for output, reference, inputs in rows:
        expected = str(reference.get("intent_type"))
        predicted = str(output.get("predicted_intent"))
        confusion[expected][predicted] = confusion[expected].get(predicted, 0) + 1
        if expected != predicted:
            failures.append(
                {
                    "utterance": inputs.get("utterance"),
                    "expected": expected,
                    "predicted": predicted,
                }
            )
    return {
        "dataset": INTENT_DATASET,
        "dataset_id": dataset_id,
        "sample_count": len(rows),
        "experiment": result.experiment_name,
        "experiment_id": str(result.experiment_id),
        "url": result.url,
        "macro_f1": macro_f1,
        "per_class_f1": per_class,
        "confusion_matrix": confusion,
        "failures": failures,
    }


async def _run_recommendation_suite(client: Client) -> dict[str, Any]:
    examples = _recommendation_examples()
    dataset_id = _ensure_dataset(
        client,
        name=RECOMMENDATION_DATASET,
        description=(
            "12 complete single-turn recommendation paths across all six catalog categories."
        ),
        examples=examples,
    )
    result = await client.aevaluate(
        _recommendation_target,
        data=RECOMMENDATION_DATASET,
        evaluators=[
            _recommendation_succeeded,
            _recommendation_latency,
            _recommendation_cost,
        ],
        summary_evaluators=[
            _average_output_metric("latency_s", "average_recommendation_latency_s"),
            _average_output_metric("cost_cny", "average_recommendation_cost_cny"),
            _average_successful_output_metric(
                "latency_s", "average_successful_recommendation_latency_s"
            ),
            _average_successful_output_metric(
                "cost_cny", "average_successful_recommendation_cost_cny"
            ),
            _recommendation_success_rate_summary,
        ],
        experiment_prefix="voice-shopping-recommendation-perf",
        description=(
            "Isolated full graph turns using live Qwen and the seeded PostgreSQL/PGVector catalog."
        ),
        metadata={
            "model": get_settings().agent_model,
            "embedding_model": get_settings().embedding_model,
            "pricing_region": "cn-beijing",
            "dataset_version": 1,
        },
        # Sequential runs measure isolated request latency without provider-side
        # contention from the benchmark itself.
        max_concurrency=0,
    )
    await result.wait()
    rows = _result_rows(result)
    outputs = [row[0] for row in rows]
    latencies = [float(output["latency_s"]) for output in outputs if output.get("latency_s")]
    costs = [float(output["cost_cny"]) for output in outputs if output.get("cost_cny") is not None]
    successful = [output for output in outputs if _successful_recommendation(output)]
    successful_latencies = [float(output["latency_s"]) for output in successful]
    successful_costs = [float(output["cost_cny"]) for output in successful]
    return {
        "dataset": RECOMMENDATION_DATASET,
        "dataset_id": dataset_id,
        "sample_count": len(rows),
        "experiment": result.experiment_name,
        "experiment_id": str(result.experiment_id),
        "url": result.url,
        "success_rate": len(successful) / len(rows) if rows else None,
        "average_latency_s": fmean(latencies) if latencies else None,
        "average_successful_latency_s": (
            fmean(successful_latencies) if successful_latencies else None
        ),
        "p50_latency_s": _percentile(latencies, 0.50),
        "p95_latency_s": _percentile(latencies, 0.95),
        "average_cost_cny": fmean(costs) if costs else None,
        "average_successful_cost_cny": fmean(successful_costs) if successful_costs else None,
        "total_cost_cny": sum(costs),
        "pricing": {
            "chat_input_cny_per_million": CHAT_INPUT_CNY_PER_MILLION,
            "chat_output_cny_per_million": CHAT_OUTPUT_CNY_PER_MILLION,
            "embedding_input_cny_per_million": EMBEDDING_INPUT_CNY_PER_MILLION,
        },
        "cases": [
            {
                "case_id": inputs.get("case_id"),
                "latency_s": output.get("latency_s"),
                "cost_cny": output.get("cost_cny"),
                "card_count": output.get("card_count"),
                "usage": output.get("usage"),
            }
            for output, _, inputs in rows
        ],
    }


async def _run_memory_suite(client: Client) -> dict[str, Any]:
    examples = _memory_examples()
    dataset_id = _ensure_dataset(
        client,
        name=MEMORY_DATASET,
        description=(
            "200 scoped synthetic shopping memories and paraphrased queries; each scenario "
            "contains ten hard-negative facts."
        ),
        examples=examples,
    )
    print("Indexing the synthetic memory corpus with the production embedding model", flush=True)
    await _prepare_memory_store()
    result = await client.aevaluate(
        _memory_target,
        data=MEMORY_DATASET,
        evaluators=[_memory_recall_at_5],
        summary_evaluators=[_mean_memory_recall_at_5],
        experiment_prefix="voice-shopping-memory-recall",
        description=(
            "Top-5 semantic recall with the production 1024-dimensional Qwen embedding model."
        ),
        metadata={"embedding_model": get_settings().embedding_model, "dataset_version": 2},
        max_concurrency=5,
    )
    await result.wait()
    rows = _result_rows(result)
    recalled = []
    misses = []
    for output, reference, inputs in rows:
        expected = reference.get("expected_key")
        hit = expected in list(output.get("retrieved_keys") or [])[:5]
        recalled.append(int(hit))
        if not hit:
            misses.append(
                {
                    "query": inputs.get("query"),
                    "expected_key": expected,
                    "retrieved_keys": output.get("retrieved_keys"),
                }
            )
    return {
        "dataset": MEMORY_DATASET,
        "dataset_id": dataset_id,
        "sample_count": len(rows),
        "experiment": result.experiment_name,
        "experiment_id": str(result.experiment_id),
        "url": result.url,
        "recall_at_5": fmean(recalled) if recalled else None,
        "misses": misses,
    }


async def _main(suite: str) -> None:
    settings = get_settings()
    if not settings.langsmith_api_key:
        raise RuntimeError("LANGSMITH_API_KEY is required")
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required")
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_TRACING"] = "true"

    client = Client(api_key=settings.langsmith_api_key)
    async with async_session_factory() as session:
        _TAXONOMY_CONTEXT.update(await _taxonomy_context(session))

    report: dict[str, Any] = {
        "models": {
            "agent": settings.agent_model,
            "embedding": settings.embedding_model,
        }
    }
    try:
        if suite in {"all", "intent"}:
            print("Running intent routing suite", flush=True)
            report["intent_routing"] = await _run_intent_suite(client)
        if suite in {"all", "recommendation"}:
            print("Running recommendation performance suite", flush=True)
            report["recommendation"] = await _run_recommendation_suite(client)
        if suite in {"all", "memory"}:
            print("Running memory recall suite", flush=True)
            report["memory"] = await _run_memory_suite(client)
    finally:
        await engine.dispose()

    print("LANGSMITH_EVAL_REPORT")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite",
        choices=("all", "intent", "recommendation", "memory"),
        default="all",
    )
    arguments = parser.parse_args()
    asyncio.run(_main(arguments.suite))

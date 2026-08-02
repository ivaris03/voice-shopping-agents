import re
from typing import Any

from voice_shopping_api.core.taxonomy import REQUIRED_ATTRIBUTE_KEYS_BY_CATEGORY

REQUIRED_SLOTS: dict[str, list[str]] = {
    category: list(keys) for category, keys in REQUIRED_ATTRIBUTE_KEYS_BY_CATEGORY.items()
}

CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "HEADPHONES": ("耳机", "蓝牙耳机", "降噪耳机"),
    "COFFEE_MACHINE": ("咖啡机", "胶囊机"),
    "ELECTRIC_KETTLE": ("电水壶", "热水壶", "恒温水壶", "水壶"),
    "RUNNING_SHOES": ("跑鞋", "跑步鞋", "运动鞋", "鞋子", "鞋"),
    "WATCHES": ("手表", "腕表", "表"),
    "LIPSTICK": ("口红", "唇膏"),
}

QUESTIONS = {
    "productCategory": "你想购买哪一类商品？",
    "noiseCancellation": "你需要主动降噪吗？",
    "form": "你想要入耳式还是头戴式？",
    "connectivity": "你希望使用蓝牙还是有线连接？",
    "batteryHours": "你希望续航至少多少小时？",
    "type": "你想要胶囊式还是半自动咖啡机？",
    "steamWand": "你需要蒸汽棒打奶泡吗？",
    "pressureBar": "你希望萃取压力至少多少 Bar？",
    "waterTankMl": "你希望水箱容量至少多少毫升？",
    "capacityL": "你希望水壶容量至少多少升？",
    "temperatureControl": "你需要多档温控吗？",
    "keepWarm": "你需要保温功能吗？",
    "gender": "你需要男款、女款还是中性款？",
    "size": "你需要多大尺码？",
    "terrain": "主要用于公路还是越野路面？",
    "cushion": "你偏好高缓震还是适中缓震？",
    "footType": "你的足型是正常足、扁平足还是过度内旋？",
    "movement": "你偏好机械、石英还是光动能机芯？",
    "material": "你偏好钢、钛金属还是树脂材质？",
    "waterResistance": "你希望至少达到多少米防水？",
    "shade": "你偏好什么色调？",
    "finish": "你偏好哑光、缎光还是水光妆效？",
    "skinType": "你的肤质是干性、油性还是中性？",
}

SLOT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "noiseCancellation": {"type": "boolean", "meaning": "是否需要主动降噪"},
    "form": {
        "type": "enum",
        "meaning": "耳机佩戴形式",
        "values": {"in-ear": "入耳式", "over-ear": "头戴式"},
    },
    "connectivity": {
        "type": "enum",
        "meaning": "连接方式",
        "values": {"bluetooth": "蓝牙或无线", "wired": "有线"},
    },
    "batteryHours": {"type": "number", "meaning": "最低续航小时数", "minimum": 1},
    "type": {
        "type": "enum",
        "meaning": "咖啡机类型",
        "values": {"capsule": "胶囊式", "semi-automatic": "半自动"},
    },
    "steamWand": {"type": "boolean", "meaning": "是否需要蒸汽棒"},
    "pressureBar": {"type": "number", "meaning": "最低萃取压力 Bar", "minimum": 1},
    "waterTankMl": {"type": "number", "meaning": "最低水箱容量毫升", "minimum": 1},
    "capacityL": {"type": "number", "meaning": "最低容量升数", "minimum": 0.1},
    "temperatureControl": {"type": "boolean", "meaning": "是否需要多档温控"},
    "keepWarm": {"type": "boolean", "meaning": "是否需要保温"},
    "gender": {
        "type": "enum",
        "meaning": "适用性别",
        "values": {"male": "男款", "female": "女款", "unisex": "中性款"},
    },
    "size": {
        "type": "number",
        "meaning": "用户需要的鞋码",
        "minimum": 1,
        "productAttribute": "sizeRange",
        "matchMode": "range_contains",
    },
    "terrain": {"type": "enum", "meaning": "跑步路面", "values": {"road": "公路", "trail": "越野"}},
    "cushion": {
        "type": "enum",
        "meaning": "缓震偏好",
        "values": {"high": "高缓震", "medium": "适中缓震"},
    },
    "footType": {
        "type": "enum",
        "meaning": "足型",
        "values": {"neutral": "正常足", "flat": "扁平足", "overpronation": "过度内旋"},
    },
    "movement": {
        "type": "enum",
        "meaning": "手表机芯",
        "values": {"automatic": "机械", "quartz": "石英", "eco-drive": "光动能"},
    },
    "material": {
        "type": "enum",
        "meaning": "手表材质",
        "values": {"steel": "钢", "titanium": "钛金属", "resin": "树脂"},
    },
    "waterResistance": {"type": "number", "meaning": "最低防水米数", "minimum": 1},
    "shade": {
        "type": "enum",
        "meaning": "口红色调",
        "values": {
            "milk-tea": "奶茶色",
            "tomato-red": "番茄红",
            "coral": "珊瑚色",
            "rose": "豆沙或玫瑰色",
            "ruby-red": "正红色",
        },
    },
    "finish": {
        "type": "enum",
        "meaning": "口红妆效",
        "values": {"matte": "哑光", "satin": "缎光", "glossy": "水光"},
    },
    "skinType": {
        "type": "enum",
        "meaning": "肤质",
        "values": {"dry": "干性", "oily": "油性", "normal": "中性"},
    },
    "budgetMax": {"type": "number", "meaning": "最高预算元数", "minimum": 1},
}

COMPLIANCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (r"百分百", r"绝对(?:有效|安全)", r"包治", r"国家级", r"稳赚不赔")
)
COMPLIANCE_FALLBACK = "抱歉，这段推荐话术未通过合规检查。你可以查看商品事实后再做选择。"

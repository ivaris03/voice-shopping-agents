"""商品向量卡片：把结构化商品字段拼成一段用户视角的中文文本。

商品向量与用户话语共享同一向量空间，因此商品文本要尽量写成用户会
如何描述一件商品的样子：中文、带字段标签、按固定顺序排列。平台的
品类、槽位和枚举值都是英文代码，入库前先映射成中文；未知的品类、
槽位键或枚举值回退到原始代码，保证拼装永不报错。
"""
from collections.abc import Mapping

CATEGORY_L1_LABELS = {
    "ELECTRONICS": "数码电子",
    "HOME_APPLIANCES": "家用电器",
    "SPORTS": "运动户外",
    "FASHION": "时尚配饰",
    "BEAUTY": "美妆个护",
    # 演示数据中腕表商品曾使用 ACCESSORIES 作为一级品类代码。
    "ACCESSORIES": "时尚配饰",
}

CATEGORY_L2_LABELS = {
    "HEADPHONES": "耳机",
    "COFFEE_MACHINE": "咖啡机",
    "ELECTRIC_KETTLE": "电水壶",
    "RUNNING_SHOES": "跑鞋",
    "WATCHES": "腕表",
    "LIPSTICK": "口红",
}

ATTRIBUTE_KEY_LABELS = {
    # HEADPHONES
    "form": "形态",
    "connectivity": "连接方式",
    "noiseCancellation": "主动降噪",
    "noiseCancellationLevel": "降噪等级",
    "batteryHours": "续航时长",
    # COFFEE_MACHINE / ELECTRIC_KETTLE
    "type": "类型",
    "steamWand": "蒸汽棒",
    "pressureBar": "萃取压力",
    "waterTankMl": "水箱容量",
    "capacityL": "容量",
    "temperatureControl": "温度控制",
    "keepWarm": "保温",
    # RUNNING_SHOES
    "gender": "适用性别",
    "size": "尺码",
    "sizeRange": "尺码范围",
    "terrain": "适用路面",
    "cushion": "缓震",
    "footType": "足型",
    "weightClass": "重量级别",
    "plate": "中底板材",
    "outsole": "大底",
    # WATCHES
    "movement": "机芯",
    "material": "表壳材质",
    "waterResistance": "防水深度",
    "radioControlled": "电波校时",
    "powerReserveHours": "动力储存",
    "dial": "表盘",
    "diameterMm": "表径",
    "gmt": "GMT双时区",
    "limitedUnits": "限量数量",
    "bluetooth": "蓝牙连接",
    # LIPSTICK
    "shade": "色号",
    "finish": "妆效",
    "skinType": "适用肤质",
    # 通用
    "color": "颜色",
    "ecosystem": "生态",
    "originalPrice": "原价",
    "isNewArrival": "新款",
}

ATTRIBUTE_VALUE_LABELS = {
    "form": {"in-ear": "入耳式", "over-ear": "头戴式"},
    "connectivity": {"bluetooth": "蓝牙", "wired": "有线"},
    "type": {"capsule": "胶囊式", "semi-automatic": "半自动"},
    "terrain": {"road": "公路", "trail": "越野"},
    "cushion": {"high": "高缓震", "medium": "中等缓震"},
    "footType": {"neutral": "正常足型", "flat": "扁平足", "overpronation": "过度内旋"},
    "gender": {"male": "男款", "female": "女款", "unisex": "男女通用"},
    "movement": {
        "automatic": "自动机械",
        "quartz": "石英",
        "eco-drive": "光动能",
        "digital": "电子",
    },
    "material": {"steel": "钢制", "titanium": "钛金属", "resin": "树脂"},
    "shade": {
        "soft-rose": "豆沙色",
        "classic-red": "正红色",
        "milk-tea": "奶茶色",
        "retro-red": "复古红",
        "tomato-red": "番茄红",
    },
    "finish": {"matte": "哑光", "satin": "缎光", "glossy": "亮面"},
    "skinType": {"dry": "干性", "oily": "油性", "normal": "中性", "all": "所有肤质"},
    "weightClass": {"ultralight": "极轻量", "light": "轻量", "medium": "中等", "heavy": "厚重"},
    "plate": {"nylon": "尼龙板", "carbon": "碳板"},
    "noiseCancellationLevel": {"high": "强", "medium": "中"},
}

# 数值属性渲染时追加的单位；防水深度既可能是整数 100 也可能是字符串 "100m"。
ATTRIBUTE_UNIT_LABELS = {
    "batteryHours": "小时",
    "pressureBar": "巴",
    "waterTankMl": "毫升",
    "capacityL": "升",
    "waterResistance": "米",
    "powerReserveHours": "小时",
    "diameterMm": "毫米",
    "limitedUnits": "枚",
}

# 价格带与用户口语（"千元以内"、"500 上下"）对齐；真实价格不做数值嵌入。
PRICE_BANDS = (
    (0, "300元以内"),
    (300, "300-500元"),
    (500, "500-1000元"),
    (1000, "1000-2000元"),
    (2000, "2000-5000元"),
    (5000, "5000元以上"),
)


def price_band_label(price: object) -> str:
    value = float(price)
    label = PRICE_BANDS[-1][1]
    for lower, band in PRICE_BANDS:
        if value < lower:
            break
        label = band
    return label


def _render_attribute_value(key: str, value: object) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, str):
        labels = ATTRIBUTE_VALUE_LABELS.get(key, {})
        if value in labels:
            return labels[value]
        unit = ATTRIBUTE_UNIT_LABELS.get(key)
        digits = value.removesuffix("m")
        if unit is not None and digits.isdigit():
            return f"{digits}{unit}"
        return value
    if isinstance(value, list):
        if key == "sizeRange" and len(value) == 2:
            return f"{value[0]}-{value[1]}码"
        return "、".join(_render_attribute_value(key, item) for item in value)
    if isinstance(value, (int, float)):
        unit = ATTRIBUTE_UNIT_LABELS.get(key)
        return f"{value:g}{unit}" if unit is not None else f"{value:g}"
    return str(value)


def build_product_embedding_text(
    *,
    name: str,
    category_l1: str,
    category_l2: str,
    brand: str | None = None,
    description: str = "",
    attributes: dict[str, object] | None = None,
    selling_points: list[str] | None = None,
    price: object | None = None,
) -> str:
    """把商品字段拼成一段用户视角的中文卡片，作为生成向量的输入文本。"""
    category_l1_label = CATEGORY_L1_LABELS.get(category_l1, category_l1)
    category_l2_label = CATEGORY_L2_LABELS.get(category_l2, category_l2)
    sections = [
        f"商品：{name}",
        f"品类：{category_l1_label}-{category_l2_label}",
    ]
    if brand:
        sections.append(f"品牌：{brand}")
    if selling_points:
        sections.append("卖点：" + "、".join(selling_points))
    if description:
        sections.append(f"描述：{description}")
    if attributes:
        parts = []
        for key in sorted(attributes):
            value = attributes[key]
            if value is None or value == "" or value == []:
                continue
            label = ATTRIBUTE_KEY_LABELS.get(key, key)
            parts.append(f"{label}：{_render_attribute_value(key, value)}")
        if parts:
            sections.append("属性：" + "、".join(parts))
    if price is not None:
        sections.append(f"价格：{price_band_label(price)}")
    return "；".join(sections)


def embedding_text_for_product(product: Mapping[str, object]) -> str:
    """从商品行（查询结果或待写参数）拼出向量卡片文本。"""
    return build_product_embedding_text(
        name=str(product["name"]),
        category_l1=str(product["category_l1"]),
        category_l2=str(product["category_l2"]),
        brand=product.get("brand"),
        description=str(product.get("description") or ""),
        attributes=dict(product.get("attributes") or {}),
        selling_points=list(product.get("selling_points") or []),
        price=product.get("price"),
    )

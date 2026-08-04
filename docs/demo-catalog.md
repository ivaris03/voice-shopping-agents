# 本地演示目录说明

`sql/data.sql` 中的演示目录保留 5 个演示商家账号、20 家演示集合店和 200 件商品。
商品名称、品牌和色号/型号使用公开可查的真实产品标识，不虚构品牌系列或型号；集合店名称均以“声选”标明，不能被理解为品牌官方店或授权经销商。

价格、库存和店铺归属只服务于本地推荐、下单和后台流程演示，并不代表实时零售价、可售状态或实际库存。

## 规格与筛选

平台当前的属性是有限枚举，而不同厂商的原始规格口径并不完全相同。种子中的 `attributes` 因此是用于筛选的规范化值：

- 耳机按佩戴形态、连接方式、降噪和可映射的续航值收录。
- 咖啡机按胶囊/半自动、蒸汽棒、压力和可映射的水箱容量收录。
- 电水壶按容量、温控和保温状态收录。
- 跑鞋按路面、缓震、支撑倾向和可售尺码范围收录。
- 腕表按机芯、表壳材质和防水筛选档位收录。
- 口红按色调桶与妆效收录，不把主观肤质推荐伪装成品牌事实。

每次播种后，SQL 都会复核一级/二级品类归属、未知属性键、必填槽位和所有枚举值，避免出现跨品类或枚举外的脏数据。

## 核验入口

下列官方页面是目录中部分产品规格的核验入口；目录中的型号以厂商公开目录为准：

- [Sony WH-1000XM5 规格](https://www.sony.com/electronics/support/wireless-headphones-bluetooth-headphones/wh-1000xm5/specifications)
- [Sony WF-1000XM5 规格](https://www.sony.com/electronics/support/wireless-headphones-bluetooth-headphones/wf-1000xm5/specifications)
- [Nespresso Essenza Mini 规格](https://www.nespresso.com/za/en/coffee-machines/original/essenza-mini-c)
- [Xiaomi Mi Smart Kettle Pro](https://www.mi.com/global/product/mi-smart-kettle-pro/)
- [Xiaomi Smart Kettle 2 Pro](https://www.mi.com/global/product/xiaomi-smart-kettle-2-pro/)
- [Seiko 5 Sports](https://www.seikowatches.com/us-en/products/5sports)
- [MAC Retro Matte Lipstick](https://www.maccosmetics.com/product/13854/52593/products/makeup/lips/lipstick/retro-matte-lipstick)

商品卡图片使用真实拍摄的品类示意图，不声称与每个型号一一对应；这样不会把非官方素材误标为某个特定品牌或型号的主图。

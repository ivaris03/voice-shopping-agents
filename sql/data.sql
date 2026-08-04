-- 语音导购平台演示数据
-- 可重复执行；仅用于本地开发/演示。演示账号的初始密码均为 Demo1234!。
-- 目录规模：5 个商家账号、20 家店铺、200 件商品（每店 10 件）。

BEGIN;

-- 固定 UUID 前缀仅由本演示种子使用。先清理该范围，避免旧目录遗留属性
-- 与当前 taxonomy 不一致，也让重复播种恢复到相同的演示状态。
DELETE FROM session_states
WHERE session_id IN (
    '30000000-0000-4000-8000-000000000001',
    '30000000-0000-4000-8000-000000000002',
    '30000000-0000-4000-8000-000000000003',
    '30000000-0000-4000-8000-000000000004'
);

DELETE FROM orders
WHERE merchant_id::text LIKE '10000000-0000-4000-8000-%'
   OR product_id::text LIKE '20000000-0000-4000-8000-%';

DELETE FROM session_messages
WHERE session_id IN (
    '30000000-0000-4000-8000-000000000001',
    '30000000-0000-4000-8000-000000000002',
    '30000000-0000-4000-8000-000000000003',
    '30000000-0000-4000-8000-000000000004'
);

DELETE FROM sessions
WHERE id IN (
    '30000000-0000-4000-8000-000000000001',
    '30000000-0000-4000-8000-000000000002',
    '30000000-0000-4000-8000-000000000003',
    '30000000-0000-4000-8000-000000000004'
);

DELETE FROM products
WHERE merchant_id::text LIKE '10000000-0000-4000-8000-%'
   OR id::text LIKE '20000000-0000-4000-8000-%';

DELETE FROM merchants
WHERE id::text LIKE '10000000-0000-4000-8000-%';

INSERT INTO users (id, email, password_hash, display_name, phone, role, status)
VALUES
    ('00000000-0000-4000-8000-000000000001', 'admin@example.com', crypt('Demo1234!', gen_salt('bf')), '平台管理员', '13800000001', 'platform', 'active'),
    ('00000000-0000-4000-8000-000000000002', 'audio@example.com', crypt('Demo1234!', gen_salt('bf')), '声动数码店主', '13800000002', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000003', 'daily@example.com', crypt('Demo1234!', gen_salt('bf')), '日常家电店主', '13800000003', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000004', 'sports@example.com', crypt('Demo1234!', gen_salt('bf')), '飞跃运动店主', '13800000004', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000005', 'watch@example.com', crypt('Demo1234!', gen_salt('bf')), '恒时腕表店主', '13800000005', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000006', 'beauty@example.com', crypt('Demo1234!', gen_salt('bf')), '花漾美妆店主', '13800000006', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000101', 'lin@example.com', crypt('Demo1234!', gen_salt('bf')), '小林', '13900000101', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000102', 'chen@example.com', crypt('Demo1234!', gen_salt('bf')), '陈晨', '13900000102', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000103', 'alice@example.com', crypt('Demo1234!', gen_salt('bf')), '爱丽丝', '13900000103', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000104', 'david@example.com', crypt('Demo1234!', gen_salt('bf')), '大卫', '13900000104', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000105', 'eric@example.com', crypt('Demo1234!', gen_salt('bf')), '埃里克', '13900000105', 'customer', 'active')
ON CONFLICT (id) DO UPDATE SET
    email = EXCLUDED.email,
    display_name = EXCLUDED.display_name,
    phone = EXCLUDED.phone,
    role = EXCLUDED.role,
    status = EXCLUDED.status;

-- 一级分类可以独立存在；二级分类必须引用一个已存在的一级分类。
INSERT INTO category_l1 (code)
VALUES
    ('ELECTRONICS'),
    ('HOME_APPLIANCES'),
    ('SPORTS'),
    ('FASHION'),
    ('BEAUTY')
ON CONFLICT (code) DO NOTHING;

INSERT INTO category_l2 (
    category_l1_id, category_l1, category_l2, required_slots, optional_slots
)
SELECT g.id, seed.category_l1, seed.category_l2, seed.required_slots, seed.optional_slots
FROM (
    VALUES
        ('ELECTRONICS', 'HEADPHONES', ARRAY['form', 'connectivity'], ARRAY['noiseCancellation', 'batteryHours']),
        ('HOME_APPLIANCES', 'COFFEE_MACHINE', ARRAY['type'], ARRAY['steamWand', 'pressureBar', 'waterTankMl']),
        ('HOME_APPLIANCES', 'ELECTRIC_KETTLE', ARRAY['capacityL'], ARRAY['temperatureControl', 'keepWarm']),
        ('SPORTS', 'RUNNING_SHOES', ARRAY['gender', 'size', 'terrain'], ARRAY['cushion', 'footType']),
        ('FASHION', 'WATCHES', ARRAY['movement'], ARRAY['gender', 'material', 'waterResistance']),
        ('BEAUTY', 'LIPSTICK', ARRAY['shade', 'finish'], ARRAY['skinType'])
) AS seed(category_l1, category_l2, required_slots, optional_slots)
JOIN category_l1 g ON g.code = seed.category_l1
ON CONFLICT (category_l2) DO UPDATE SET
    category_l1_id = EXCLUDED.category_l1_id,
    category_l1 = EXCLUDED.category_l1,
    required_slots = EXCLUDED.required_slots,
    optional_slots = EXCLUDED.optional_slots;

-- 清除历史演示库中可能残留的槽位，保证分类契约只有下列定义。
DELETE FROM category_slots AS slot
USING category_l2 AS category
WHERE slot.category_id = category.id
  AND category.category_l2 IN (
      'HEADPHONES', 'COFFEE_MACHINE', 'ELECTRIC_KETTLE',
      'RUNNING_SHOES', 'WATCHES', 'LIPSTICK'
  )
  AND slot.key NOT IN (
      'form', 'connectivity', 'noiseCancellation', 'batteryHours',
      'type', 'steamWand', 'pressureBar', 'waterTankMl',
      'capacityL', 'temperatureControl', 'keepWarm',
      'gender', 'size', 'terrain', 'cushion', 'footType',
      'movement', 'material', 'waterResistance',
      'shade', 'finish', 'skinType'
  );

-- 每个槽位同时给出非空枚举；必填/选填仅影响澄清是否阻塞。
INSERT INTO category_slots (category_id, key, is_required, enum_values)
SELECT c.id, seed.key, seed.is_required, seed.enum_values
FROM (
    VALUES
        ('HEADPHONES', 'form', true, '["in-ear","over-ear"]'::jsonb),
        ('HEADPHONES', 'connectivity', true, '["bluetooth","wired"]'::jsonb),
        ('HEADPHONES', 'noiseCancellation', false, '[true,false]'::jsonb),
        ('HEADPHONES', 'batteryHours', false, '[5,6,8,24,30,32,45,60]'::jsonb),
        ('COFFEE_MACHINE', 'type', true, '["capsule","semi-automatic"]'::jsonb),
        ('COFFEE_MACHINE', 'steamWand', false, '[true,false]'::jsonb),
        ('COFFEE_MACHINE', 'pressureBar', false, '[9,15,19,20]'::jsonb),
        ('COFFEE_MACHINE', 'waterTankMl', false, '[600,800,1000,1500,2000]'::jsonb),
        ('ELECTRIC_KETTLE', 'capacityL', true, '[1,1.2,1.5,1.7,2]'::jsonb),
        ('ELECTRIC_KETTLE', 'temperatureControl', false, '[true,false]'::jsonb),
        ('ELECTRIC_KETTLE', 'keepWarm', false, '[true,false]'::jsonb),
        ('RUNNING_SHOES', 'gender', true, '["male","female","unisex"]'::jsonb),
        ('RUNNING_SHOES', 'size', true, '[35,36,37,38,39,40,41,42,43,44,45,46]'::jsonb),
        ('RUNNING_SHOES', 'terrain', true, '["road","trail"]'::jsonb),
        ('RUNNING_SHOES', 'cushion', false, '["high","medium"]'::jsonb),
        ('RUNNING_SHOES', 'footType', false, '["neutral","flat","overpronation"]'::jsonb),
        ('WATCHES', 'movement', true, '["automatic","quartz","eco-drive"]'::jsonb),
        ('WATCHES', 'gender', false, '["male","female","unisex"]'::jsonb),
        ('WATCHES', 'material', false, '["steel","titanium","resin"]'::jsonb),
        ('WATCHES', 'waterResistance', false, '[30,50,100,200]'::jsonb),
        ('LIPSTICK', 'shade', true, '["milk-tea","tomato-red","coral","rose","ruby-red"]'::jsonb),
        ('LIPSTICK', 'finish', true, '["matte","satin","glossy"]'::jsonb),
        ('LIPSTICK', 'skinType', false, '["dry","oily","normal"]'::jsonb)
) AS seed(category_l2, key, is_required, enum_values)
JOIN category_l2 c ON c.category_l2 = seed.category_l2
ON CONFLICT (category_id, key) DO UPDATE SET
    is_required = EXCLUDED.is_required,
    enum_values = EXCLUDED.enum_values;

-- 5 个商家账号各管理 4 家店铺，均已启用，避免演示时出现后台存在而用户端不可见的商品。
INSERT INTO merchants (
    id, owner_user_id, name, slug, description, logo_url, contact_phone,
    is_enabled, disabled_reason
)
VALUES
    ('10000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000002', '声动数码旗舰店', 'sound-digital', '专注通勤、影音与运动耳机。', 'https://example.com/images/merchants/sound-digital.png', '13800000002', true, NULL),
    ('10000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000002', '云听耳机店', 'cloud-listening', '提供不同佩戴形态的日常耳机。', 'https://example.com/images/merchants/cloud-listening.png', '13800000002', true, NULL),
    ('10000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-000000000002', '通勤音频馆', 'commute-audio', '为地铁、办公与通话场景挑选耳机。', 'https://example.com/images/merchants/commute-audio.png', '13800000002', true, NULL),
    ('10000000-0000-4000-8000-000000000004', '00000000-0000-4000-8000-000000000002', '蓝调声学馆', 'blue-tone-audio', '覆盖有线和无线音频设备。', 'https://example.com/images/merchants/blue-tone-audio.png', '13800000002', true, NULL),
    ('10000000-0000-4000-8000-000000000005', '00000000-0000-4000-8000-000000000003', '日常咖啡生活馆', 'daily-coffee', '适合家庭与办公室的咖啡设备。', 'https://example.com/images/merchants/daily-coffee.png', '13800000003', true, NULL),
    ('10000000-0000-4000-8000-000000000006', '00000000-0000-4000-8000-000000000003', '晨间咖啡设备店', 'morning-coffee', '提供入门与进阶咖啡机。', 'https://example.com/images/merchants/morning-coffee.png', '13800000003', true, NULL),
    ('10000000-0000-4000-8000-000000000007', '00000000-0000-4000-8000-000000000003', '沸点水具馆', 'boiling-kettle', '专注大容量和恒温电水壶。', 'https://example.com/images/merchants/boiling-kettle.png', '13800000003', true, NULL),
    ('10000000-0000-4000-8000-000000000008', '00000000-0000-4000-8000-000000000003', '温度家电店', 'temperature-home', '为饮水和冲泡场景准备小家电。', 'https://example.com/images/merchants/temperature-home.png', '13800000003', true, NULL),
    ('10000000-0000-4000-8000-000000000009', '00000000-0000-4000-8000-000000000004', '飞跃跑步旗舰店', 'flyover-running', '提供日常训练与竞速跑鞋。', 'https://example.com/images/merchants/flyover-running.png', '13800000004', true, NULL),
    ('10000000-0000-4000-8000-000000000010', '00000000-0000-4000-8000-000000000004', '城市路跑装备店', 'city-running', '面向路跑训练和通勤步行。', 'https://example.com/images/merchants/city-running.png', '13800000004', true, NULL),
    ('10000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000004', '山径越野跑步店', 'trail-running', '提供越野和复杂路面跑鞋。', 'https://example.com/images/merchants/trail-running.png', '13800000004', true, NULL),
    ('10000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000004', '节奏运动生活馆', 'rhythm-sports', '覆盖日常运动和恢复训练装备。', 'https://example.com/images/merchants/rhythm-sports.png', '13800000004', true, NULL),
    ('10000000-0000-4000-8000-000000000013', '00000000-0000-4000-8000-000000000005', '恒时腕表精品店', 'timeless-watches', '主营机械、石英和光动能腕表。', 'https://example.com/images/merchants/timeless-watches.png', '13800000005', true, NULL),
    ('10000000-0000-4000-8000-000000000014', '00000000-0000-4000-8000-000000000005', '光域腕表馆', 'lume-watches', '提供通勤、商务和运动腕表。', 'https://example.com/images/merchants/lume-watches.png', '13800000005', true, NULL),
    ('10000000-0000-4000-8000-000000000015', '00000000-0000-4000-8000-000000000005', '极昼运动表店', 'polar-watches', '侧重耐用、防水的运动腕表。', 'https://example.com/images/merchants/polar-watches.png', '13800000005', true, NULL),
    ('10000000-0000-4000-8000-000000000016', '00000000-0000-4000-8000-000000000005', '表盘工坊', 'dial-workshop', '精选不同材质与机芯的腕表。', 'https://example.com/images/merchants/dial-workshop.png', '13800000005', true, NULL),
    ('10000000-0000-4000-8000-000000000017', '00000000-0000-4000-8000-000000000006', '花漾美妆旗舰店', 'bloom-beauty', '提供日常通勤和宴会妆容产品。', 'https://example.com/images/merchants/bloom-beauty.png', '13800000006', true, NULL),
    ('10000000-0000-4000-8000-000000000018', '00000000-0000-4000-8000-000000000006', '唇色实验室', 'lip-lab', '按色调和妆效挑选口红。', 'https://example.com/images/merchants/lip-lab.png', '13800000006', true, NULL),
    ('10000000-0000-4000-8000-000000000019', '00000000-0000-4000-8000-000000000006', '玫瑰妆容馆', 'rose-makeup', '专注不同肤质可用的唇妆产品。', 'https://example.com/images/merchants/rose-makeup.png', '13800000006', true, NULL),
    ('10000000-0000-4000-8000-000000000020', '00000000-0000-4000-8000-000000000006', '轻妆日用店', 'light-makeup', '提供轻松易用的日常彩妆。', 'https://example.com/images/merchants/light-makeup.png', '13800000006', true, NULL);

-- 每个店铺 10 件商品。所有 attributes 都只使用当前 category_slots 中声明的键和值；
-- 品牌、文案、图片等展示信息则留在独立列中，避免干扰结构化筛选。
WITH seed_base AS (
    SELECT
        product_no,
        CASE
            WHEN product_no <= 40 THEN 'ELECTRONICS'
            WHEN product_no <= 80 THEN 'HOME_APPLIANCES'
            WHEN product_no <= 120 THEN 'SPORTS'
            WHEN product_no <= 160 THEN 'FASHION'
            ELSE 'BEAUTY'
        END AS category_l1,
        CASE
            WHEN product_no <= 40 THEN 'HEADPHONES'
            WHEN product_no <= 60 THEN 'COFFEE_MACHINE'
            WHEN product_no <= 80 THEN 'ELECTRIC_KETTLE'
            WHEN product_no <= 120 THEN 'RUNNING_SHOES'
            WHEN product_no <= 160 THEN 'WATCHES'
            ELSE 'LIPSTICK'
        END AS category_l2,
        CASE
            WHEN product_no <= 40 THEN 1 + ((product_no - 1) / 10)::int
            WHEN product_no <= 60 THEN 5 + ((product_no - 41) / 10)::int
            WHEN product_no <= 80 THEN 7 + ((product_no - 61) / 10)::int
            WHEN product_no <= 120 THEN 9 + ((product_no - 81) / 10)::int
            WHEN product_no <= 160 THEN 13 + ((product_no - 121) / 10)::int
            ELSE 17 + ((product_no - 161) / 10)::int
        END AS merchant_slot
    FROM generate_series(1, 200) AS sequence(product_no)
),
product_seed AS (
    SELECT
        ('20000000-0000-4000-8000-' || lpad(product_no::text, 12, '0'))::uuid AS id,
        ('10000000-0000-4000-8000-' || lpad(merchant_slot::text, 12, '0'))::uuid AS merchant_id,
        category_l1,
        category_l2,
        CASE category_l2
            WHEN 'HEADPHONES' THEN 'HDP-' || lpad(product_no::text, 3, '0')
            WHEN 'COFFEE_MACHINE' THEN 'COF-' || lpad((product_no - 40)::text, 3, '0')
            WHEN 'ELECTRIC_KETTLE' THEN 'KET-' || lpad((product_no - 60)::text, 3, '0')
            WHEN 'RUNNING_SHOES' THEN 'RUN-' || lpad((product_no - 80)::text, 3, '0')
            WHEN 'WATCHES' THEN 'WAT-' || lpad((product_no - 120)::text, 3, '0')
            ELSE 'LIP-' || lpad((product_no - 160)::text, 3, '0')
        END AS sku,
        CASE product_no
            WHEN 1 THEN '云雀 Air 降噪耳机'
            WHEN 2 THEN '潮汐 Pro 真无线耳机'
            WHEN 3 THEN '原野 Lite 蓝牙耳机'
            WHEN 41 THEN '晨雾 Mini 胶囊咖啡机'
            WHEN 42 THEN '山岚半自动咖啡机'
            WHEN 61 THEN '清泉恒温水壶'
            WHEN 81 THEN 'Nike Pegasus 40 缓震跑鞋'
            WHEN 82 THEN 'Adidas Ultraboost 22 跑鞋'
            WHEN 83 THEN 'Saucony Endorphin Speed 3 竞速跑鞋'
            WHEN 84 THEN 'Asics Gel-Kayano 30 稳定跑鞋'
            WHEN 85 THEN 'HOKA Clifton 9 轻量缓震跑鞋'
            WHEN 86 THEN 'Nike Pegasus 39 入门跑鞋'
            WHEN 87 THEN 'New Balance FuelCell 女款跑鞋'
            WHEN 88 THEN 'HOKA Speedgoat 5 越野跑鞋'
            WHEN 89 THEN 'Nike Vaporfly 3 竞速跑鞋'
            WHEN 90 THEN 'Asics Cumulus 25 日常训练鞋'
            WHEN 121 THEN 'Seiko 5 Sports 机械腕表'
            WHEN 122 THEN 'Casio G-Shock GA-2100'
            WHEN 123 THEN 'Citizen 光动能 AT8020'
            WHEN 124 THEN 'Tissot 力洛克机械腕表'
            WHEN 128 THEN 'Seiko Prospex 潜水腕表'
            WHEN 161 THEN 'YSL 小金条口红 52'
            WHEN 162 THEN 'Dior 烈艳蓝金口红 999'
            WHEN 163 THEN '3CE 云朵雾面口红 908'
            WHEN 164 THEN 'MAC 子弹头口红 Ruby Woo'
            WHEN 165 THEN 'Armani 红管口红 405'
            WHEN 166 THEN '雅诗兰黛 倾慕口红 420'
            ELSE CASE category_l2
                WHEN 'HEADPHONES' THEN '声澜系列 ' || product_no || ' 无线耳机'
                WHEN 'COFFEE_MACHINE' THEN '萃享系列 ' || (product_no - 40) || ' 咖啡机'
                WHEN 'ELECTRIC_KETTLE' THEN '清饮系列 ' || (product_no - 60) || ' 电水壶'
                WHEN 'RUNNING_SHOES' THEN '飞跃节奏 ' || (product_no - 80) || ' 跑鞋'
                WHEN 'WATCHES' THEN '恒时臻选 ' || (product_no - 120) || ' 腕表'
                ELSE '花漾唇色 ' || (product_no - 160) || ' 口红'
            END
        END AS name,
        CASE product_no
            WHEN 1 THEN '云雀'
            WHEN 2 THEN '潮汐'
            WHEN 3 THEN '原野'
            WHEN 41 THEN '晨雾'
            WHEN 42 THEN '山岚'
            WHEN 61 THEN '清泉'
            WHEN 81 THEN 'Nike'
            WHEN 82 THEN 'Adidas'
            WHEN 83 THEN 'Saucony'
            WHEN 84 THEN 'Asics'
            WHEN 85 THEN 'HOKA'
            WHEN 86 THEN 'Nike'
            WHEN 87 THEN 'New Balance'
            WHEN 88 THEN 'HOKA'
            WHEN 89 THEN 'Nike'
            WHEN 90 THEN 'Asics'
            WHEN 121 THEN 'Seiko'
            WHEN 122 THEN 'Casio'
            WHEN 123 THEN 'Citizen'
            WHEN 124 THEN 'Tissot'
            WHEN 128 THEN 'Seiko'
            WHEN 161 THEN 'YSL'
            WHEN 162 THEN 'Dior'
            WHEN 163 THEN '3CE'
            WHEN 164 THEN 'MAC'
            WHEN 165 THEN 'Armani'
            WHEN 166 THEN '雅诗兰黛'
            ELSE CASE category_l2
                WHEN 'HEADPHONES' THEN (ARRAY['声阔', '索尼', '漫步者', 'Bose'])[(product_no - 1) % 4 + 1]
                WHEN 'COFFEE_MACHINE' THEN (ARRAY['萃享', '咖啡记', '云萃'])[(product_no - 41) % 3 + 1]
                WHEN 'ELECTRIC_KETTLE' THEN (ARRAY['清饮', '沸点', '温度'])[(product_no - 61) % 3 + 1]
                WHEN 'RUNNING_SHOES' THEN (ARRAY['飞跃', '步云', '路驰', '山径'])[(product_no - 81) % 4 + 1]
                WHEN 'WATCHES' THEN (ARRAY['恒时', '光域', '极昼', '表盘'])[(product_no - 121) % 4 + 1]
                ELSE (ARRAY['花漾', '玫瑰', '轻妆', '唇色'])[(product_no - 161) % 4 + 1]
            END
        END AS brand,
        CASE category_l2
            WHEN 'HEADPHONES' THEN '适合通勤、影音和日常通话的耳机。'
            WHEN 'COFFEE_MACHINE' THEN '为家庭和办公室准备的咖啡机。'
            WHEN 'ELECTRIC_KETTLE' THEN '适合冲泡和日常饮水的电水壶。'
            WHEN 'RUNNING_SHOES' THEN '为路跑、训练和运动恢复设计的跑鞋。'
            WHEN 'WATCHES' THEN '覆盖通勤、商务和运动场景的腕表。'
            ELSE '适合不同肤质和妆效偏好的口红。'
        END AS description,
        CASE product_no
            WHEN 1 THEN 699.00
            WHEN 2 THEN 999.00
            WHEN 3 THEN 329.00
            WHEN 41 THEN 599.00
            WHEN 42 THEN 1699.00
            WHEN 61 THEN 239.00
            WHEN 81 THEN 899.00
            WHEN 82 THEN 1299.00
            WHEN 83 THEN 1399.00
            WHEN 84 THEN 1599.00
            WHEN 85 THEN 1180.00
            WHEN 86 THEN 599.00
            WHEN 87 THEN 1099.00
            WHEN 88 THEN 1580.00
            WHEN 89 THEN 1999.00
            WHEN 90 THEN 899.00
            WHEN 121 THEN 2280.00
            WHEN 122 THEN 899.00
            WHEN 123 THEN 3680.00
            WHEN 124 THEN 3880.00
            WHEN 128 THEN 5280.00
            WHEN 161 THEN 380.00
            WHEN 162 THEN 360.00
            WHEN 163 THEN 140.00
            WHEN 164 THEN 220.00
            WHEN 165 THEN 320.00
            WHEN 166 THEN 350.00
            ELSE CASE category_l2
                WHEN 'HEADPHONES' THEN 1199.00 + ((product_no % 6) * 200)
                WHEN 'COFFEE_MACHINE' THEN 1299.00 + ((product_no % 4) * 250)
                WHEN 'ELECTRIC_KETTLE' THEN 159.00 + ((product_no % 5) * 70)
                WHEN 'RUNNING_SHOES' THEN 1099.00 + ((product_no % 8) * 110)
                WHEN 'WATCHES' THEN 999.00 + ((product_no % 8) * 500)
                ELSE 120.00 + ((product_no % 7) * 45)
            END
        END::numeric AS price,
        CASE product_no
            WHEN 1 THEN 80
            WHEN 2 THEN 45
            WHEN 3 THEN 70
            WHEN 81 THEN 50
            WHEN 85 THEN 40
            WHEN 86 THEN 60
            WHEN 121 THEN 65
            ELSE 12 + ((product_no * 7) % 24)
        END AS stock,
        CASE category_l2
            WHEN 'HEADPHONES' THEN jsonb_build_object(
                'form', CASE
                    WHEN product_no = 1 THEN 'over-ear'
                    WHEN product_no = 2 THEN 'in-ear'
                    ELSE (ARRAY['in-ear', 'over-ear'])[(product_no - 1) % 2 + 1]
                END,
                'connectivity', CASE
                    WHEN product_no <= 3 THEN 'bluetooth'
                    WHEN product_no % 5 = 0 THEN 'wired'
                    ELSE 'bluetooth'
                END,
                'noiseCancellation', CASE
                    WHEN product_no IN (1, 2) THEN true
                    WHEN product_no = 3 THEN false
                    ELSE product_no % 3 = 0
                END,
                'batteryHours', (ARRAY[5, 6, 8, 24, 30, 32, 45, 60])[(product_no - 1) % 8 + 1]
            )
            WHEN 'COFFEE_MACHINE' THEN jsonb_build_object(
                'type', CASE WHEN product_no = 41 THEN 'capsule' ELSE 'semi-automatic' END,
                'steamWand', product_no = 42,
                'pressureBar', CASE
                    WHEN product_no = 41 THEN 19
                    WHEN product_no = 42 THEN 15
                    ELSE (ARRAY[9, 15, 19, 20])[(product_no - 41) % 4 + 1]
                END,
                'waterTankMl', CASE
                    WHEN product_no = 41 THEN 600
                    WHEN product_no = 42 THEN 1500
                    ELSE (ARRAY[600, 800, 1000, 1500, 2000])[(product_no - 41) % 5 + 1]
                END
            )
            WHEN 'ELECTRIC_KETTLE' THEN jsonb_build_object(
                'capacityL', CASE
                    WHEN product_no = 61 THEN 1.5
                    ELSE (ARRAY[1.0, 1.2, 1.5, 1.7, 2.0])[(product_no - 61) % 5 + 1]
                END,
                'temperatureControl', CASE WHEN product_no = 61 THEN true ELSE product_no % 2 = 0 END,
                'keepWarm', CASE WHEN product_no = 61 THEN true ELSE product_no % 3 <> 0 END
            )
            WHEN 'RUNNING_SHOES' THEN jsonb_build_object(
                'gender', CASE
                    WHEN product_no = 87 THEN 'female'
                    WHEN product_no % 7 = 0 THEN 'male'
                    ELSE 'unisex'
                END,
                'size', jsonb_build_array(35 + (product_no % 5), 42 + (product_no % 5)),
                'terrain', CASE
                    WHEN product_no = 88 THEN 'trail'
                    WHEN product_no % 6 = 0 THEN 'trail'
                    ELSE 'road'
                END,
                'cushion', CASE
                    WHEN product_no IN (81, 82, 84, 85) THEN 'high'
                    WHEN product_no IN (83, 86, 87, 89, 90) THEN 'medium'
                    ELSE (ARRAY['high', 'medium'])[(product_no - 81) % 2 + 1]
                END,
                'footType', CASE
                    WHEN product_no = 84 THEN jsonb_build_array('flat', 'overpronation')
                    WHEN product_no % 3 = 0 THEN to_jsonb('overpronation'::text)
                    ELSE to_jsonb('neutral'::text)
                END
            )
            WHEN 'WATCHES' THEN jsonb_build_object(
                'movement', CASE
                    WHEN product_no IN (121, 124, 128) THEN 'automatic'
                    WHEN product_no = 122 THEN 'quartz'
                    WHEN product_no = 123 THEN 'eco-drive'
                    ELSE (ARRAY['automatic', 'quartz', 'eco-drive'])[(product_no - 121) % 3 + 1]
                END,
                'gender', CASE
                    WHEN product_no % 5 = 0 THEN 'female'
                    WHEN product_no % 2 = 0 THEN 'unisex'
                    ELSE 'male'
                END,
                'material', CASE
                    WHEN product_no = 122 THEN 'resin'
                    WHEN product_no = 123 THEN 'titanium'
                    ELSE (ARRAY['steel', 'titanium', 'resin'])[(product_no - 121) % 3 + 1]
                END,
                'waterResistance', CASE
                    WHEN product_no = 122 THEN 200
                    WHEN product_no = 123 THEN 200
                    WHEN product_no = 128 THEN 200
                    ELSE (ARRAY[30, 50, 100, 200])[(product_no - 121) % 4 + 1]
                END
            )
            ELSE jsonb_build_object(
                'shade', CASE product_no
                    WHEN 161 THEN 'rose'
                    WHEN 162 THEN 'ruby-red'
                    WHEN 163 THEN 'milk-tea'
                    WHEN 164 THEN 'ruby-red'
                    WHEN 165 THEN 'tomato-red'
                    ELSE (ARRAY['milk-tea', 'tomato-red', 'coral', 'rose', 'ruby-red'])[(product_no - 161) % 5 + 1]
                END,
                'finish', CASE product_no
                    WHEN 161 THEN 'matte'
                    WHEN 162 THEN 'satin'
                    WHEN 163 THEN 'matte'
                    WHEN 164 THEN 'matte'
                    WHEN 165 THEN 'matte'
                    ELSE (ARRAY['matte', 'satin', 'glossy'])[(product_no - 161) % 3 + 1]
                END,
                'skinType', CASE product_no
                    WHEN 161 THEN 'dry'
                    WHEN 162 THEN 'normal'
                    WHEN 163 THEN 'normal'
                    WHEN 164 THEN 'oily'
                    WHEN 165 THEN 'oily'
                    ELSE (ARRAY['dry', 'oily', 'normal'])[(product_no - 161) % 3 + 1]
                END
            )
        END AS attributes,
        CASE category_l2
            WHEN 'HEADPHONES' THEN ARRAY['佩戴舒适', '日常通勤', '清晰通话']
            WHEN 'COFFEE_MACHINE' THEN ARRAY['操作直观', '适合家用', '稳定萃取']
            WHEN 'ELECTRIC_KETTLE' THEN ARRAY['容量可选', '快速烧水', '日常好用']
            WHEN 'RUNNING_SHOES' THEN ARRAY['稳定贴合', '适合训练', '轻盈回弹']
            WHEN 'WATCHES' THEN ARRAY['可靠机芯', '日常佩戴', '多场景搭配']
            ELSE ARRAY['颜色饱满', '妆效清晰', '日常显气色']
        END AS selling_points,
        ARRAY['https://example.com/images/products/demo-' || lpad(product_no::text, 3, '0') || '.png'] AS image_urls,
        'on_sale' AS status,
        product_no AS embedding_axis
    FROM seed_base
)
INSERT INTO products (
    id, merchant_id, sku, name, category_l1, category_l2, brand, description, price, stock,
    attributes, selling_points, image_urls, status, embedding
)
SELECT
    ps.id, ps.merchant_id, ps.sku, ps.name, ps.category_l1, ps.category_l2, ps.brand,
    ps.description, ps.price, ps.stock, ps.attributes, ps.selling_points, ps.image_urls, ps.status,
    (
        SELECT array_agg(
            CASE WHEN dimension_no = ps.embedding_axis THEN 1.0::real ELSE 0.0::real END
            ORDER BY dimension_no
        )::vector(1024)
        FROM generate_series(1, 1024) AS dimensions(dimension_no)
    )
FROM product_seed AS ps;

-- 种子写入时立即复核数量、品类归属、未知键、必填值和枚举值，防止再次引入脏数据。
DO $$
DECLARE
    seeded_product_count integer;
    seeded_store_count integer;
    seeded_owner_count integer;
    invalid_product_count integer;
BEGIN
    SELECT count(*)
    INTO seeded_product_count
    FROM products
    WHERE id::text LIKE '20000000-0000-4000-8000-%';

    SELECT count(*), count(DISTINCT owner_user_id)
    INTO seeded_store_count, seeded_owner_count
    FROM merchants
    WHERE id::text LIKE '10000000-0000-4000-8000-%'
      AND deleted_at IS NULL;

    IF seeded_product_count <> 200 OR seeded_store_count <> 20 OR seeded_owner_count <> 5 THEN
        RAISE EXCEPTION
            '演示种子规模错误：商家账号 %, 店铺 %, 商品 %',
            seeded_owner_count, seeded_store_count, seeded_product_count;
    END IF;

    SELECT count(*)
    INTO invalid_product_count
    FROM products AS product
    JOIN category_l2 AS category ON category.category_l2 = product.category_l2
    WHERE product.id::text LIKE '20000000-0000-4000-8000-%'
      AND (
          product.category_l1 <> category.category_l1
          OR EXISTS (
              SELECT 1
              FROM jsonb_object_keys(product.attributes) AS attribute_key(key)
              WHERE NOT EXISTS (
                  SELECT 1
                  FROM category_slots AS slot
                  WHERE slot.category_id = category.id
                    AND slot.key = attribute_key.key
              )
          )
          OR EXISTS (
              SELECT 1
              FROM category_slots AS slot
              WHERE slot.category_id = category.id
                AND slot.is_required
                AND (
                    NOT product.attributes ? slot.key
                    OR product.attributes -> slot.key = 'null'::jsonb
                    OR product.attributes -> slot.key = '""'::jsonb
                    OR product.attributes -> slot.key = '[]'::jsonb
                    OR product.attributes -> slot.key = '{}'::jsonb
                )
          )
          OR EXISTS (
              SELECT 1
              FROM category_slots AS slot
              WHERE slot.category_id = category.id
                AND product.attributes ? slot.key
                AND (
                    (
                        jsonb_typeof(product.attributes -> slot.key) = 'array'
                        AND (
                            jsonb_array_length(product.attributes -> slot.key) = 0
                            OR EXISTS (
                                SELECT 1
                                FROM jsonb_array_elements(product.attributes -> slot.key) AS item(value)
                                WHERE NOT slot.enum_values @> jsonb_build_array(item.value)
                            )
                        )
                    )
                    OR (
                        jsonb_typeof(product.attributes -> slot.key) <> 'array'
                        AND NOT slot.enum_values @> jsonb_build_array(product.attributes -> slot.key)
                    )
                )
          )
      );

    IF invalid_product_count <> 0 THEN
        RAISE EXCEPTION '演示种子包含 % 件不符合 taxonomy 的商品', invalid_product_count;
    END IF;
END;
$$;

INSERT INTO user_profile_static (
    user_id, gender, age, city, height_cm, weight_kg, skin_type,
    tech_savvy, budget_band, locale, updated_at
)
VALUES
    ('00000000-0000-4000-8000-000000000101', 'male', 29, '上海', 178, 68, 'normal', 'mid', 'mid', 'zh_cn', CURRENT_TIMESTAMP - interval '1 day'),
    ('00000000-0000-4000-8000-000000000102', 'female', 32, '杭州', 165, 52, 'dry', 'novice', 'premium', 'zh_cn', CURRENT_TIMESTAMP - interval '2 days'),
    ('00000000-0000-4000-8000-000000000103', 'female', 28, '北京', 168, 55, 'normal', 'expert', 'mid', 'zh_cn', CURRENT_TIMESTAMP - interval '6 hours'),
    ('00000000-0000-4000-8000-000000000104', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'zh_cn', CURRENT_TIMESTAMP),
    ('00000000-0000-4000-8000-000000000105', 'male', 35, '深圳', 180, 75, 'oily', 'mid', 'premium', 'zh_cn', CURRENT_TIMESTAMP - interval '12 hours')
ON CONFLICT (user_id) DO UPDATE SET
    gender = EXCLUDED.gender,
    age = EXCLUDED.age,
    city = EXCLUDED.city,
    height_cm = EXCLUDED.height_cm,
    weight_kg = EXCLUDED.weight_kg,
    skin_type = EXCLUDED.skin_type,
    tech_savvy = EXCLUDED.tech_savvy,
    budget_band = EXCLUDED.budget_band,
    locale = EXCLUDED.locale,
    updated_at = EXCLUDED.updated_at;

INSERT INTO user_profile_dynamic (
    user_id, category_affinity, brand_affinity, recent_viewed,
    recent_purchased, price_sensitivity, avg_order_amount, updated_at
)
VALUES
    ('00000000-0000-4000-8000-000000000101', '{"HEADPHONES":0.94}', '{"云雀":0.72,"潮汐":0.55}', ARRAY['20000000-0000-4000-8000-000000000001'::uuid, '20000000-0000-4000-8000-000000000002'::uuid], '{}'::uuid[], 0.35, NULL, CURRENT_TIMESTAMP - interval '20 minutes'),
    ('00000000-0000-4000-8000-000000000102', '{"COFFEE_MACHINE":0.88}', '{"山岚":0.68,"晨雾":0.62}', ARRAY['20000000-0000-4000-8000-000000000042'::uuid], ARRAY['20000000-0000-4000-8000-000000000042'::uuid], 0.25, 1699.00, CURRENT_TIMESTAMP - interval '2 hours'),
    ('00000000-0000-4000-8000-000000000103', '{"RUNNING_SHOES":0.93}', '{"Nike":0.75,"Asics":0.68,"HOKA":0.46}', ARRAY['20000000-0000-4000-8000-000000000081'::uuid, '20000000-0000-4000-8000-000000000086'::uuid, '20000000-0000-4000-8000-000000000090'::uuid], '{}'::uuid[], 0.40, NULL, CURRENT_TIMESTAMP - interval '15 minutes'),
    ('00000000-0000-4000-8000-000000000104', '{}', '{}', '{}'::uuid[], '{}'::uuid[], NULL, NULL, CURRENT_TIMESTAMP),
    ('00000000-0000-4000-8000-000000000105', '{"HEADPHONES":0.82,"WATCHES":0.45}', '{"Sony":0.61,"Casio":0.55,"Seiko":0.42}', ARRAY['20000000-0000-4000-8000-000000000121'::uuid, '20000000-0000-4000-8000-000000000122'::uuid], '{}'::uuid[], 0.30, NULL, CURRENT_TIMESTAMP - interval '1 hour')
ON CONFLICT (user_id) DO UPDATE SET
    category_affinity = EXCLUDED.category_affinity,
    brand_affinity = EXCLUDED.brand_affinity,
    recent_viewed = EXCLUDED.recent_viewed,
    recent_purchased = EXCLUDED.recent_purchased,
    price_sensitivity = EXCLUDED.price_sensitivity,
    avg_order_amount = EXCLUDED.avg_order_amount,
    updated_at = EXCLUDED.updated_at;

INSERT INTO sessions (
    id, user_id, status, conversation_summary, last_turn_id,
    started_at, last_active_at, ended_at
)
VALUES
    ('30000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000101', 'active', '用户想找千元以内、适合通勤且带主动降噪的耳机。', '31000000-0000-4000-8000-000000000002', CURRENT_TIMESTAMP - interval '10 minutes', CURRENT_TIMESTAMP - interval '2 minutes', NULL),
    ('30000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000102', 'closed', '用户查询了家用半自动咖啡机并完成下单。', '32000000-0000-4000-8000-000000000001', CURRENT_TIMESTAMP - interval '2 days', CURRENT_TIMESTAMP - interval '2 days', CURRENT_TIMESTAMP - interval '2 days' + interval '8 minutes'),
    ('30000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-000000000104', 'closed', '冷启动用户提出写周报请求，系统识别为不支持的请求并使用固定兜底回复。', '33000000-0000-4000-8000-000000000001', CURRENT_TIMESTAMP - interval '1 hour', CURRENT_TIMESTAMP - interval '58 minutes', CURRENT_TIMESTAMP - interval '58 minutes'),
    ('30000000-0000-4000-8000-000000000004', '00000000-0000-4000-8000-000000000103', 'closed', '用户经过逐轮澄清，确定购买千元内用于日常路跑的跑鞋。', '34000000-0000-4000-8000-000000000003', CURRENT_TIMESTAMP - interval '30 minutes', CURRENT_TIMESTAMP - interval '20 minutes', CURRENT_TIMESTAMP - interval '20 minutes');

-- 展示会话保留少量历史消息，供用户端历史记录页演示。
INSERT INTO session_messages (
    id, session_id, turn_id, seq, role, message_type, content, metadata
)
VALUES
    ('40000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000001', 0, 'user', 'transcript', '想买一副通勤降噪耳机，预算一千元以内。', '{}'),
    ('40000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000001', 1, 'assistant', 'product_cards', '已为你找到 3 款耳机。', '{"productIds":["20000000-0000-4000-8000-000000000001","20000000-0000-4000-8000-000000000002","20000000-0000-4000-8000-000000000003"]}'),
    ('40000000-0000-4000-8000-000000000003', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000002', 0, 'user', 'transcript', '就买第一款。', '{}'),
    ('40000000-0000-4000-8000-000000000004', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000002', 1, 'assistant', 'order', '已生成待确认订单，请确认是否购买云雀 Air 降噪耳机。', '{}'),
    ('40000000-0000-4000-8000-000000000101', '30000000-0000-4000-8000-000000000003', '33000000-0000-4000-8000-000000000001', 0, 'user', 'transcript', '帮我写一份周报。', '{}'),
    ('40000000-0000-4000-8000-000000000102', '30000000-0000-4000-8000-000000000003', '33000000-0000-4000-8000-000000000001', 1, 'assistant', 'text', '抱歉，我目前只能协助商品推荐、查询、对比和下单。你可以告诉我想买什么商品。', '{"fallback":true,"intent":"UNSUPPORTED_REQUEST"}'),
    ('40000000-0000-4000-8000-000000000103', '30000000-0000-4000-8000-000000000004', '34000000-0000-4000-8000-000000000001', 0, 'user', 'transcript', '我想买双跑鞋。', '{}'),
    ('40000000-0000-4000-8000-000000000104', '30000000-0000-4000-8000-000000000004', '34000000-0000-4000-8000-000000000001', 1, 'assistant', 'text', '你的预算上限是多少？', '{"clarificationStatus":"ASK","slot":"budgetMax"}');

INSERT INTO orders (
    id, user_id, merchant_id, product_id, session_id, source_turn_id,
    idempotency_key, status, quantity, unit_price, merchant_snapshot,
    product_snapshot, failure_reason, expires_at, confirmed_at, created_at
)
VALUES
    ('50000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000101', '10000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000002', 'voice-order-session-001-turn-002', 'pending', 1, 699.00, '{"merchantId":"10000000-0000-4000-8000-000000000001","name":"声动数码旗舰店"}', '{"productId":"20000000-0000-4000-8000-000000000001","sku":"HDP-001","name":"云雀 Air 降噪耳机","categoryL1":"ELECTRONICS","categoryL2":"HEADPHONES","imageUrl":"https://example.com/images/products/demo-001.png"}', NULL, CURRENT_TIMESTAMP + interval '15 minutes', NULL, CURRENT_TIMESTAMP),
    ('50000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000102', '10000000-0000-4000-8000-000000000005', '20000000-0000-4000-8000-000000000042', '30000000-0000-4000-8000-000000000002', '32000000-0000-4000-8000-000000000001', 'voice-order-session-002-turn-001', 'success', 1, 1699.00, '{"merchantId":"10000000-0000-4000-8000-000000000005","name":"日常咖啡生活馆"}', '{"productId":"20000000-0000-4000-8000-000000000042","sku":"COF-002","name":"山岚半自动咖啡机","categoryL1":"HOME_APPLIANCES","categoryL2":"COFFEE_MACHINE","imageUrl":"https://example.com/images/products/demo-042.png"}', NULL, CURRENT_TIMESTAMP - interval '2 days' + interval '15 minutes', CURRENT_TIMESTAMP - interval '2 days' + interval '5 minutes', CURRENT_TIMESTAMP - interval '2 days'),
    ('50000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-000000000101', '10000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002', NULL, NULL, 'voice-order-expired-demo-001', 'fail', 1, 999.00, '{"merchantId":"10000000-0000-4000-8000-000000000001","name":"声动数码旗舰店"}', '{"productId":"20000000-0000-4000-8000-000000000002","sku":"HDP-002","name":"潮汐 Pro 真无线耳机","categoryL1":"ELECTRONICS","categoryL2":"HEADPHONES","imageUrl":"https://example.com/images/products/demo-002.png"}', 'confirmation_timeout', CURRENT_TIMESTAMP - interval '3 days' + interval '15 minutes', NULL, CURRENT_TIMESTAMP - interval '3 days');

INSERT INTO session_states (
    id, session_id, turn_id, state_version, business_state, pending_order_id
)
VALUES
    ('60000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000001', 1, '{"product_category":"HEADPHONES","slots":{"budgetMax":1000,"useCase":"commute","noiseCancellation":true},"pending_question":null,"product_cards":[{"productId":"20000000-0000-4000-8000-000000000001"},{"productId":"20000000-0000-4000-8000-000000000002"},{"productId":"20000000-0000-4000-8000-000000000003"}],"user_profile_updates":{}}', NULL),
    ('60000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000002', 1, '{"product_category":"HEADPHONES","slots":{"budgetMax":1000,"useCase":"commute","noiseCancellation":true},"pending_question":null,"product_cards":[],"user_profile_updates":{}}', '50000000-0000-4000-8000-000000000001'),
    ('60000000-0000-4000-8000-000000000101', '30000000-0000-4000-8000-000000000003', '33000000-0000-4000-8000-000000000001', 1, '{"user_profile_updates":{}}', NULL),
    ('60000000-0000-4000-8000-000000000102', '30000000-0000-4000-8000-000000000004', '34000000-0000-4000-8000-000000000001', 1, '{"product_category":"RUNNING_SHOES","slots":{},"pending_question":{"slot":"budgetMax","question":"你的预算上限是多少？"},"user_profile_updates":{}}', NULL);

COMMIT;

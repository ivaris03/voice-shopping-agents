-- 语音导购平台演示数据
-- 可重复执行；仅用于本地开发/演示。演示账号的初始密码均为 12345678。
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
    ('00000000-0000-4000-8000-000000000001', 'admin@example.com', crypt('12345678', gen_salt('bf')), '平台管理员', '13800000001', 'platform', 'active'),
    ('00000000-0000-4000-8000-000000000002', 'audio@example.com', crypt('12345678', gen_salt('bf')), '声选音频商家', '13800000002', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000003', 'daily@example.com', crypt('12345678', gen_salt('bf')), '声选家电商家', '13800000003', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000004', 'sports@example.com', crypt('12345678', gen_salt('bf')), '声选运动商家', '13800000004', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000005', 'watch@example.com', crypt('12345678', gen_salt('bf')), '声选腕表商家', '13800000005', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000006', 'beauty@example.com', crypt('12345678', gen_salt('bf')), '声选美妆商家', '13800000006', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000101', 'lin@example.com', crypt('12345678', gen_salt('bf')), '小林', '13900000101', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000102', 'chen@example.com', crypt('12345678', gen_salt('bf')), '陈晨', '13900000102', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000103', 'alice@example.com', crypt('12345678', gen_salt('bf')), '爱丽丝', '13900000103', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000104', 'david@example.com', crypt('12345678', gen_salt('bf')), '大卫', '13900000104', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000105', 'eric@example.com', crypt('12345678', gen_salt('bf')), '埃里克', '13900000105', 'customer', 'active')
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
    ('10000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000002', '声选 · 通勤音频', 'sound-digital', '本地演示集合店，展示通勤音频的真实品牌型号。', NULL, '13800000002', true, NULL),
    ('10000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000002', '声选 · 旗舰耳机', 'cloud-listening', '本地演示集合店，不代表任何品牌官方授权。', NULL, '13800000002', true, NULL),
    ('10000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-000000000002', '声选 · 运动耳机', 'commute-audio', '本地演示集合店，收录运动与真无线耳机型号。', NULL, '13800000002', true, NULL),
    ('10000000-0000-4000-8000-000000000004', '00000000-0000-4000-8000-000000000002', '声选 · 有线音频', 'blue-tone-audio', '本地演示集合店，收录监听与有线音频型号。', NULL, '13800000002', true, NULL),
    ('10000000-0000-4000-8000-000000000005', '00000000-0000-4000-8000-000000000003', '声选 · 家用咖啡', 'daily-coffee', '本地演示集合店，展示真实品牌咖啡机型号。', NULL, '13800000003', true, NULL),
    ('10000000-0000-4000-8000-000000000006', '00000000-0000-4000-8000-000000000003', '声选 · 胶囊咖啡', 'morning-coffee', '本地演示集合店，按咖啡机类型展示商品。', NULL, '13800000003', true, NULL),
    ('10000000-0000-4000-8000-000000000007', '00000000-0000-4000-8000-000000000003', '声选 · 恒温水具', 'boiling-kettle', '本地演示集合店，收录可筛选容量和温控的水壶。', NULL, '13800000003', true, NULL),
    ('10000000-0000-4000-8000-000000000008', '00000000-0000-4000-8000-000000000003', '声选 · 日用热饮', 'temperature-home', '本地演示集合店，展示日常热饮小家电。', NULL, '13800000003', true, NULL),
    ('10000000-0000-4000-8000-000000000009', '00000000-0000-4000-8000-000000000004', '声选 · 公路跑鞋', 'flyover-running', '本地演示集合店，展示公路训练跑鞋型号。', NULL, '13800000004', true, NULL),
    ('10000000-0000-4000-8000-000000000010', '00000000-0000-4000-8000-000000000004', '声选 · 稳定支撑', 'city-running', '本地演示集合店，展示支撑和缓震跑鞋型号。', NULL, '13800000004', true, NULL),
    ('10000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000004', '声选 · 越野跑鞋', 'trail-running', '本地演示集合店，展示越野跑鞋型号。', NULL, '13800000004', true, NULL),
    ('10000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000004', '声选 · 竞速训练', 'rhythm-sports', '本地演示集合店，展示竞速与训练跑鞋型号。', NULL, '13800000004', true, NULL),
    ('10000000-0000-4000-8000-000000000013', '00000000-0000-4000-8000-000000000005', '声选 · 机械腕表', 'timeless-watches', '本地演示集合店，展示真实自动机械表型号。', NULL, '13800000005', true, NULL),
    ('10000000-0000-4000-8000-000000000014', '00000000-0000-4000-8000-000000000005', '声选 · 经典腕表', 'lume-watches', '本地演示集合店，展示石英与经典腕表型号。', NULL, '13800000005', true, NULL),
    ('10000000-0000-4000-8000-000000000015', '00000000-0000-4000-8000-000000000005', '声选 · 运动腕表', 'polar-watches', '本地演示集合店，展示耐用和防水腕表型号。', NULL, '13800000005', true, NULL),
    ('10000000-0000-4000-8000-000000000016', '00000000-0000-4000-8000-000000000005', '声选 · 光动能腕表', 'dial-workshop', '本地演示集合店，展示 Eco-Drive 腕表型号。', NULL, '13800000005', true, NULL),
    ('10000000-0000-4000-8000-000000000017', '00000000-0000-4000-8000-000000000006', '声选 · 经典唇妆', 'bloom-beauty', '本地演示集合店，展示真实品牌与色号。', NULL, '13800000006', true, NULL),
    ('10000000-0000-4000-8000-000000000018', '00000000-0000-4000-8000-000000000006', '声选 · 雾面唇妆', 'lip-lab', '本地演示集合店，按色调和雾面妆效筛选。', NULL, '13800000006', true, NULL),
    ('10000000-0000-4000-8000-000000000019', '00000000-0000-4000-8000-000000000006', '声选 · 轻润唇妆', 'rose-makeup', '本地演示集合店，展示润泽与缎光唇妆。', NULL, '13800000006', true, NULL),
    ('10000000-0000-4000-8000-000000000020', '00000000-0000-4000-8000-000000000006', '声选 · 持妆唇妆', 'light-makeup', '本地演示集合店，展示长效唇妆色号。', NULL, '13800000006', true, NULL);

-- 每个店铺 10 件商品。seed_base 只用于稳定地生成 ID、品类和店铺归属；
-- 实际展示的品牌、型号、价格、属性和图片由下方 real_catalog 完整覆盖。
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
        ARRAY[]::text[] AS image_urls,
        'on_sale' AS status,
        product_no AS embedding_axis
    FROM seed_base
),
-- 真实品牌与型号的本地演示目录。价格、库存和店铺归属仅用于本地演示；
-- attributes 是为了当前 taxonomy 做的可筛选规格归一化，所有值都必须命中对应枚举。
real_catalog (product_no, name, brand, price, attributes) AS (
    VALUES
        (1, 'Sony WH-CH720N 无线降噪头戴耳机', 'Sony', 799.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (2, 'soundcore Liberty 4 NC 真无线降噪耳机', 'soundcore', 899.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (3, 'Sony WH-1000XM5 无线降噪头戴耳机', 'Sony', 2599.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":30}'::jsonb),
        (4, 'Sony WF-1000XM5 真无线降噪耳机', 'Sony', 1999.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":8}'::jsonb),
        (5, 'Bose QuietComfort 无线降噪耳机', 'Bose', 2399.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":24}'::jsonb),
        (6, 'Bose QuietComfort Ultra 无线耳机', 'Bose', 3199.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":24}'::jsonb),
        (7, 'Bose QuietComfort Ultra Earbuds 降噪耳机', 'Bose', 2299.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":6}'::jsonb),
        (8, 'Sennheiser MOMENTUM 4 Wireless 头戴耳机', 'Sennheiser', 2799.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":60}'::jsonb),
        (9, 'Sennheiser ACCENTUM Plus 无线耳机', 'Sennheiser', 1799.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (10, 'Apple AirPods Max USB-C 头戴耳机', 'Apple', 3999.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (11, 'Apple AirPods Pro 第 2 代 USB-C', 'Apple', 1899.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":30}'::jsonb),
        (12, 'Apple AirPods 4 主动降噪版', 'Apple', 1399.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":30}'::jsonb),
        (13, 'Beats Studio Pro 无线头戴耳机', 'Beats', 2299.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":24}'::jsonb),
        (14, 'Beats Fit Pro 真无线耳机', 'Beats', 1599.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":24}'::jsonb),
        (15, 'JBL Tour One M2 无线降噪耳机', 'JBL', 2099.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (16, 'JBL Live 770NC 无线降噪耳机', 'JBL', 1399.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (17, 'JBL Tour Pro 2 真无线耳机', 'JBL', 1999.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (18, 'Bowers and Wilkins Px7 S2e 无线耳机', 'Bowers and Wilkins', 2999.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":30}'::jsonb),
        (19, 'Nothing Ear a 真无线耳机', 'Nothing', 699.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (20, 'Sony MDR-7506 专业监听耳机', 'Sony', 899.00, '{"form":"over-ear","connectivity":"wired","noiseCancellation":false}'::jsonb),
        (21, 'Sennheiser HD 560S 开放式耳机', 'Sennheiser', 1299.00, '{"form":"over-ear","connectivity":"wired","noiseCancellation":false}'::jsonb),
        (22, 'beyerdynamic DT 770 PRO 80 Ohm 耳机', 'beyerdynamic', 1499.00, '{"form":"over-ear","connectivity":"wired","noiseCancellation":false}'::jsonb),
        (23, 'Audio-Technica ATH-M50x 监听耳机', 'Audio-Technica', 1199.00, '{"form":"over-ear","connectivity":"wired","noiseCancellation":false}'::jsonb),
        (24, 'Shure AONIC 50 Gen 2 无线耳机', 'Shure', 2999.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":45}'::jsonb),
        (25, 'Shure AONIC 4 有线入耳耳机', 'Shure', 2299.00, '{"form":"in-ear","connectivity":"wired","noiseCancellation":false}'::jsonb),
        (26, 'soundcore Space Q45 无线降噪耳机', 'soundcore', 1499.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (27, 'soundcore Life Q30 无线降噪耳机', 'soundcore', 899.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (28, 'soundcore Liberty 4 真无线耳机', 'soundcore', 1099.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (29, 'Edifier W820NB Plus 无线降噪耳机', 'Edifier', 599.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (30, 'Edifier NeoBuds Pro 2 真无线耳机', 'Edifier', 1299.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (31, 'Edifier HECATE G2BT 游戏耳机', 'Edifier', 499.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":false}'::jsonb),
        (32, 'Xiaomi Buds 5 真无线耳机', 'Xiaomi', 699.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (33, 'Xiaomi Type-C 有线耳机', 'Xiaomi', 129.00, '{"form":"in-ear","connectivity":"wired","noiseCancellation":false}'::jsonb),
        (34, 'HUAWEI FreeBuds Pro 3 真无线耳机', 'HUAWEI', 1499.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (35, 'HUAWEI FreeClip 开放式耳机', 'HUAWEI', 1299.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":false}'::jsonb),
        (36, 'Technics EAH-AZ80 真无线耳机', 'Technics', 2399.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (37, 'Marshall Monitor III A.N.C. 无线耳机', 'Marshall', 2799.00, '{"form":"over-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (38, 'Marshall Motif II A.N.C. 真无线耳机', 'Marshall', 1699.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":30}'::jsonb),
        (39, 'Jabra Elite 10 Gen 2 真无线耳机', 'Jabra', 2499.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true}'::jsonb),
        (40, 'Jabra Elite 8 Active Gen 2 真无线耳机', 'Jabra', 1899.00, '{"form":"in-ear","connectivity":"bluetooth","noiseCancellation":true,"batteryHours":32}'::jsonb),
        (41, 'Nespresso Essenza Mini C30 胶囊咖啡机', 'Nespresso', 799.00, '{"type":"capsule","steamWand":false,"pressureBar":19,"waterTankMl":600}'::jsonb),
        (42, 'De''Longhi Dedica EC685 半自动咖啡机', 'De''Longhi', 1899.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15}'::jsonb),
        (43, 'Nespresso Pixie C61 胶囊咖啡机', 'Nespresso', 1299.00, '{"type":"capsule","steamWand":false,"pressureBar":19}'::jsonb),
        (44, 'Nespresso Inissia C40 胶囊咖啡机', 'Nespresso', 999.00, '{"type":"capsule","steamWand":false,"pressureBar":19}'::jsonb),
        (45, 'Nespresso CitiZ D113 胶囊咖啡机', 'Nespresso', 1699.00, '{"type":"capsule","steamWand":false,"pressureBar":19,"waterTankMl":1000}'::jsonb),
        (46, 'Nespresso Creatista Plus J520 胶囊咖啡机', 'Nespresso', 4499.00, '{"type":"capsule","steamWand":true,"pressureBar":19,"waterTankMl":1500}'::jsonb),
        (47, 'Nespresso Vertuo Pop 胶囊咖啡机', 'Nespresso', 1099.00, '{"type":"capsule","steamWand":false}'::jsonb),
        (48, 'De''Longhi Genio S Plus 胶囊咖啡机', 'De''Longhi', 999.00, '{"type":"capsule","steamWand":false,"pressureBar":15,"waterTankMl":800}'::jsonb),
        (49, 'Nespresso Lattissima One F121 胶囊咖啡机', 'Nespresso', 2999.00, '{"type":"capsule","steamWand":false,"pressureBar":19,"waterTankMl":1000}'::jsonb),
        (50, 'Nespresso Gran Lattissima F531 胶囊咖啡机', 'Nespresso', 4999.00, '{"type":"capsule","steamWand":false,"pressureBar":19,"waterTankMl":1500}'::jsonb),
        (51, 'Breville Bambino BES450 半自动咖啡机', 'Breville', 2999.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":9}'::jsonb),
        (52, 'Breville Barista Express BES870 半自动咖啡机', 'Breville', 5299.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15,"waterTankMl":2000}'::jsonb),
        (53, 'Breville Barista Pro BES878 半自动咖啡机', 'Breville', 6499.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15,"waterTankMl":2000}'::jsonb),
        (54, 'Gaggia Classic Pro 半自动咖啡机', 'Gaggia', 3999.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15}'::jsonb),
        (55, 'Rancilio Silvia 半自动咖啡机', 'Rancilio', 5699.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15,"waterTankMl":2000}'::jsonb),
        (56, 'Lelit Anna PL41TEM 半自动咖啡机', 'Lelit', 4999.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15,"waterTankMl":2000}'::jsonb),
        (57, 'Lelit MaraX PL62X 半自动咖啡机', 'Lelit', 11999.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15}'::jsonb),
        (58, 'De''Longhi La Specialista Arte EC9155 半自动咖啡机', 'De''Longhi', 5999.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15}'::jsonb),
        (59, 'De''Longhi Dedica Arte EC885 半自动咖啡机', 'De''Longhi', 2499.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15}'::jsonb),
        (60, 'De''Longhi ECP3420 半自动咖啡机', 'De''Longhi', 1499.00, '{"type":"semi-automatic","steamWand":true,"pressureBar":15}'::jsonb),
        (61, 'Xiaomi Mi Smart Kettle Pro 电水壶', 'Xiaomi', 299.00, '{"capacityL":1.5,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (62, 'Xiaomi Smart Kettle 2 Pro 电水壶', 'Xiaomi', 399.00, '{"capacityL":1.7,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (63, 'Philips HD9350 电水壶', 'Philips', 299.00, '{"capacityL":1.7,"temperatureControl":false,"keepWarm":false}'::jsonb),
        (64, 'Philips HD9359 温控电水壶', 'Philips', 499.00, '{"capacityL":1.7,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (65, 'Bosch TWK8613P 温控电水壶', 'Bosch', 699.00, '{"capacityL":1.5,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (66, 'Cuisinart CPK-17 PerfecTemp 电水壶', 'Cuisinart', 899.00, '{"capacityL":1.7,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (67, 'Breville BKE820 Variable Temperature 电水壶', 'Breville', 1199.00, '{"capacityL":1.7,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (68, 'OXO Adjustable Temperature 电水壶', 'OXO', 899.00, '{"capacityL":1.7,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (69, 'Tefal KO8508 温控电水壶', 'Tefal', 599.00, '{"capacityL":1.7,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (70, 'Smeg KLF04 温控电水壶', 'Smeg', 1699.00, '{"capacityL":1.7,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (71, 'Smeg KLF03 电水壶', 'Smeg', 1299.00, '{"capacityL":1.7,"temperatureControl":false,"keepWarm":false}'::jsonb),
        (72, 'De''Longhi KBOE2001 电水壶', 'De''Longhi', 799.00, '{"capacityL":1.7,"temperatureControl":false,"keepWarm":false}'::jsonb),
        (73, 'KitchenAid KEK1701 温控电水壶', 'KitchenAid', 1499.00, '{"capacityL":1.7,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (74, 'Braun WK5115 温控电水壶', 'Braun', 699.00, '{"capacityL":1.7,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (75, 'Russell Hobbs 24360 电水壶', 'Russell Hobbs', 499.00, '{"capacityL":1.7,"temperatureControl":false,"keepWarm":false}'::jsonb),
        (76, 'Zojirushi CK-DA10 电水壶', 'Zojirushi', 899.00, '{"capacityL":1.0,"temperatureControl":false,"keepWarm":false}'::jsonb),
        (77, 'Panasonic NC-K301 电水壶', 'Panasonic', 399.00, '{"capacityL":1.5,"temperatureControl":false,"keepWarm":false}'::jsonb),
        (78, 'Midea MK-SH17X1 电水壶', 'Midea', 249.00, '{"capacityL":1.7,"temperatureControl":false,"keepWarm":false}'::jsonb),
        (79, 'Joyoung K15-F626 温控电水壶', 'Joyoung', 299.00, '{"capacityL":1.5,"temperatureControl":true,"keepWarm":true}'::jsonb),
        (80, 'SUPOR SW-17J12P 电水壶', 'SUPOR', 199.00, '{"capacityL":1.7,"temperatureControl":false,"keepWarm":false}'::jsonb),
        (81, 'Nike Pegasus 41 跑鞋', 'Nike', 999.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (82, 'Nike Vomero 17 跑鞋', 'Nike', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (83, 'Nike InfinityRN 4 跑鞋', 'Nike', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (84, 'Nike Structure 25 跑鞋', 'Nike', 1199.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"overpronation"}'::jsonb),
        (85, 'ASICS GEL-Nimbus 26 跑鞋', 'ASICS', 1399.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (86, 'ASICS GEL-Kayano 31 跑鞋', 'ASICS', 1499.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":["flat","overpronation"]}'::jsonb),
        (87, 'ASICS GT-2000 12 跑鞋', 'ASICS', 1099.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"overpronation"}'::jsonb),
        (88, 'ASICS Novablast 4 跑鞋', 'ASICS', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (89, 'ASICS METASPEED Sky Paris 跑鞋', 'ASICS', 1899.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (90, 'New Balance Fresh Foam X 1080v13 跑鞋', 'New Balance', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (91, 'New Balance Fresh Foam X 880v14 跑鞋', 'New Balance', 1099.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (92, 'New Balance Fresh Foam X Vongo v6 跑鞋', 'New Balance', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"overpronation"}'::jsonb),
        (93, 'New Balance FuelCell Rebel v4 跑鞋', 'New Balance', 1199.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (94, 'New Balance FuelCell SC Elite v4 跑鞋', 'New Balance', 1999.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (95, 'HOKA Clifton 9 跑鞋', 'HOKA', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (96, 'HOKA Bondi 8 跑鞋', 'HOKA', 1399.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (97, 'HOKA Arahi 7 跑鞋', 'HOKA', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"overpronation"}'::jsonb),
        (98, 'HOKA Mach 6 跑鞋', 'HOKA', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (99, 'HOKA Rocket X 2 跑鞋', 'HOKA', 1999.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (100, 'Brooks Ghost 16 跑鞋', 'Brooks', 1199.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (101, 'Brooks Glycerin 21 跑鞋', 'Brooks', 1399.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (102, 'Brooks Adrenaline GTS 23 跑鞋', 'Brooks', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"overpronation"}'::jsonb),
        (103, 'Brooks Hyperion Elite 4 跑鞋', 'Brooks', 1999.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (104, 'Saucony Ride 17 跑鞋', 'Saucony', 1099.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (105, 'Saucony Triumph 22 跑鞋', 'Saucony', 1399.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (106, 'Saucony Guide 17 跑鞋', 'Saucony', 1199.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"overpronation"}'::jsonb),
        (107, 'Saucony Endorphin Speed 4 跑鞋', 'Saucony', 1499.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (108, 'Saucony Endorphin Pro 4 跑鞋', 'Saucony', 1999.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"neutral"}'::jsonb),
        (109, 'On Cloudmonster 2 跑鞋', 'On', 1599.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"high","footType":"neutral"}'::jsonb),
        (110, 'On Cloudrunner 2 跑鞋', 'On', 1399.00, '{"gender":"unisex","size":[35,46],"terrain":"road","cushion":"medium","footType":"overpronation"}'::jsonb),
        (111, 'HOKA Speedgoat 5 越野跑鞋', 'HOKA', 1399.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"high","footType":"neutral"}'::jsonb),
        (112, 'HOKA Challenger 7 越野跑鞋', 'HOKA', 1199.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"medium","footType":"neutral"}'::jsonb),
        (113, 'HOKA Mafate Speed 4 越野跑鞋', 'HOKA', 1599.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"high","footType":"neutral"}'::jsonb),
        (114, 'ASICS GEL-Trabuco 12 越野跑鞋', 'ASICS', 1199.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"medium","footType":"neutral"}'::jsonb),
        (115, 'ASICS Trabuco Max 3 越野跑鞋', 'ASICS', 1399.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"high","footType":"neutral"}'::jsonb),
        (116, 'Nike Zegama 2 越野跑鞋', 'Nike', 1499.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"high","footType":"neutral"}'::jsonb),
        (117, 'Nike Wildhorse 8 越野跑鞋', 'Nike', 1199.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"medium","footType":"neutral"}'::jsonb),
        (118, 'adidas Terrex Agravic Speed Ultra 越野跑鞋', 'adidas', 1799.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"medium","footType":"neutral"}'::jsonb),
        (119, 'Salomon Speedcross 6 越野跑鞋', 'Salomon', 1199.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"medium","footType":"neutral"}'::jsonb),
        (120, 'Salomon Ultra Glide 2 越野跑鞋', 'Salomon', 1299.00, '{"gender":"unisex","size":[35,46],"terrain":"trail","cushion":"high","footType":"neutral"}'::jsonb),
        (121, 'Seiko 5 Sports SRPD55K1 自动机械表', 'Seiko', 2280.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (122, 'Seiko 5 Sports SRPE51K1 自动机械表', 'Seiko', 2380.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (123, 'Seiko 5 Sports SRPK29K1 自动机械表', 'Seiko', 2580.00, '{"movement":"automatic","gender":"unisex","material":"steel","waterResistance":100}'::jsonb),
        (124, 'Seiko Presage SRPB41J1 自动机械表', 'Seiko', 3680.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":50}'::jsonb),
        (125, 'Seiko Presage SRPE19J1 自动机械表', 'Seiko', 3980.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":50}'::jsonb),
        (126, 'Orient Bambino RA-AC0005L10B 自动机械表', 'Orient', 1980.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":30}'::jsonb),
        (127, 'Orient Kamasu RA-AA0003R19B 自动机械表', 'Orient', 2680.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":200}'::jsonb),
        (128, 'Orient Mako III RA-AA0810N19B 自动机械表', 'Orient', 2580.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":200}'::jsonb),
        (129, 'Hamilton Khaki Field Auto H70555533 自动机械表', 'Hamilton', 4980.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (130, 'Hamilton Khaki Field Murph H70605731 自动机械表', 'Hamilton', 6980.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (131, 'Tissot PRX Powermatic 80 自动机械表', 'Tissot', 5980.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (132, 'Tissot Le Locle Powermatic 80 自动机械表', 'Tissot', 4980.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":30}'::jsonb),
        (133, 'Tissot Gentleman Powermatic 80 自动机械表', 'Tissot', 5680.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (134, 'Certina DS Action Diver 自动机械表', 'Certina', 6980.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":200}'::jsonb),
        (135, 'Mido Ocean Star 自动机械表', 'Mido', 7680.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":200}'::jsonb),
        (136, 'Citizen Tsuyosa NJ0150-81Z 自动机械表', 'Citizen', 2980.00, '{"movement":"automatic","gender":"unisex","material":"steel","waterResistance":50}'::jsonb),
        (137, 'Citizen Promaster NY0120-01E 自动机械表', 'Citizen', 3480.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":200}'::jsonb),
        (138, 'Timex Marlin Automatic TW2T22700 自动机械表', 'Timex', 1899.00, '{"movement":"automatic","gender":"male","material":"steel","waterResistance":30}'::jsonb),
        (139, 'Swatch Sistem51 SO27B100 自动机械表', 'Swatch', 1399.00, '{"movement":"automatic","gender":"unisex","material":"resin","waterResistance":30}'::jsonb),
        (140, 'Casio G-Shock GA-2100-1A1 石英表', 'Casio', 899.00, '{"movement":"quartz","gender":"unisex","material":"resin","waterResistance":200}'::jsonb),
        (141, 'Casio G-Shock DW-5600E-1V 石英表', 'Casio', 699.00, '{"movement":"quartz","gender":"unisex","material":"resin","waterResistance":200}'::jsonb),
        (142, 'Casio Edifice EFR-S108D-1AV 石英表', 'Casio', 999.00, '{"movement":"quartz","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (143, 'Casio MTP-1302PD-2A2V 石英表', 'Casio', 499.00, '{"movement":"quartz","gender":"unisex","material":"steel","waterResistance":50}'::jsonb),
        (144, 'Timex Q TW2U61800 石英表', 'Timex', 1299.00, '{"movement":"quartz","gender":"unisex","material":"steel","waterResistance":50}'::jsonb),
        (145, 'Timex Weekender TW2P62300 石英表', 'Timex', 599.00, '{"movement":"quartz","gender":"unisex","material":"steel","waterResistance":30}'::jsonb),
        (146, 'Tissot PRX Quartz 石英表', 'Tissot', 2980.00, '{"movement":"quartz","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (147, 'Seiko SUR309P1 石英表', 'Seiko', 1699.00, '{"movement":"quartz","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (148, 'Citizen Eco-Drive BM8180-03E 光动能表', 'Citizen', 1299.00, '{"movement":"eco-drive","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (149, 'Citizen Eco-Drive AW1236-03A 光动能表', 'Citizen', 1499.00, '{"movement":"eco-drive","gender":"male","material":"steel","waterResistance":30}'::jsonb),
        (150, 'Citizen Eco-Drive CA4500-91E 光动能表', 'Citizen', 2480.00, '{"movement":"eco-drive","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (151, 'Citizen Eco-Drive AW1690-51E 光动能表', 'Citizen', 1899.00, '{"movement":"eco-drive","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (152, 'Citizen Eco-Drive BJ6500-21A 光动能表', 'Citizen', 2299.00, '{"movement":"eco-drive","gender":"male","material":"steel","waterResistance":50}'::jsonb),
        (153, 'Citizen Eco-Drive EM0500-73A 光动能女表', 'Citizen', 1899.00, '{"movement":"eco-drive","gender":"female","material":"steel","waterResistance":50}'::jsonb),
        (154, 'Citizen Eco-Drive EW3150-51A 光动能女表', 'Citizen', 1999.00, '{"movement":"eco-drive","gender":"female","material":"steel","waterResistance":50}'::jsonb),
        (155, 'Citizen Eco-Drive FE1080-52B 光动能女表', 'Citizen', 1799.00, '{"movement":"eco-drive","gender":"female","material":"steel","waterResistance":30}'::jsonb),
        (156, 'Citizen Eco-Drive BM7108-81E 光动能表', 'Citizen', 3199.00, '{"movement":"eco-drive","gender":"male","material":"titanium","waterResistance":100}'::jsonb),
        (157, 'Citizen Eco-Drive AW1660-51H 光动能表', 'Citizen', 1699.00, '{"movement":"eco-drive","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (158, 'Citizen Eco-Drive AW1760-81Z 光动能表', 'Citizen', 1899.00, '{"movement":"eco-drive","gender":"male","material":"steel","waterResistance":100}'::jsonb),
        (159, 'Citizen Eco-Drive EW2690-81A 光动能女表', 'Citizen', 1999.00, '{"movement":"eco-drive","gender":"female","material":"steel","waterResistance":50}'::jsonb),
        (160, 'Citizen Eco-Drive EM1060-87N 光动能女表', 'Citizen', 2399.00, '{"movement":"eco-drive","gender":"female","material":"steel","waterResistance":50}'::jsonb),
        (161, 'MAC Retro Matte Lipstick Ruby Woo', 'MAC', 220.00, '{"shade":"ruby-red","finish":"matte"}'::jsonb),
        (162, 'MAC Retro Matte Lipstick Chili', 'MAC', 220.00, '{"shade":"tomato-red","finish":"matte"}'::jsonb),
        (163, 'MAC Matte Lipstick Velvet Teddy', 'MAC', 220.00, '{"shade":"milk-tea","finish":"matte"}'::jsonb),
        (164, 'MAC Matte Lipstick Mehr', 'MAC', 220.00, '{"shade":"rose","finish":"matte"}'::jsonb),
        (165, 'MAC Matte Lipstick Marrakesh', 'MAC', 220.00, '{"shade":"tomato-red","finish":"matte"}'::jsonb),
        (166, 'Dior Rouge Dior Velvet 999', 'Dior', 420.00, '{"shade":"ruby-red","finish":"matte"}'::jsonb),
        (167, 'Dior Rouge Dior Velvet 720 Icone', 'Dior', 420.00, '{"shade":"rose","finish":"matte"}'::jsonb),
        (168, 'Dior Addict Lip Glow 001 Pink', 'Dior', 390.00, '{"shade":"rose","finish":"glossy"}'::jsonb),
        (169, 'Dior Addict Lip Maximizer 001 Pink', 'Dior', 390.00, '{"shade":"rose","finish":"glossy"}'::jsonb),
        (170, 'Dior Rouge Forever Liquid 999 Forever Dior', 'Dior', 430.00, '{"shade":"ruby-red","finish":"matte"}'::jsonb),
        (171, 'YSL Rouge Pur Couture The Slim 21 Rouge Paradoxe', 'YSL', 410.00, '{"shade":"ruby-red","finish":"matte"}'::jsonb),
        (172, 'YSL Rouge Pur Couture The Slim 1966 Rouge Libre', 'YSL', 410.00, '{"shade":"tomato-red","finish":"matte"}'::jsonb),
        (173, 'YSL Rouge Pur Couture The Slim 302 Brown No Way Back', 'YSL', 410.00, '{"shade":"milk-tea","finish":"matte"}'::jsonb),
        (174, 'YSL Loveshine 150 Nude Lingerie', 'YSL', 400.00, '{"shade":"milk-tea","finish":"glossy"}'::jsonb),
        (175, 'YSL Candy Glaze 13 Flashing Rose', 'YSL', 410.00, '{"shade":"rose","finish":"glossy"}'::jsonb),
        (176, 'Armani Lip Maestro 405 Sultan', 'Armani', 390.00, '{"shade":"tomato-red","finish":"matte"}'::jsonb),
        (177, 'Armani Lip Maestro 206 Cedar', 'Armani', 390.00, '{"shade":"milk-tea","finish":"matte"}'::jsonb),
        (178, 'Armani Lip Power 400 Four Hundred', 'Armani', 400.00, '{"shade":"ruby-red","finish":"satin"}'::jsonb),
        (179, 'Armani Lip Power 104 Selfless', 'Armani', 400.00, '{"shade":"rose","finish":"satin"}'::jsonb),
        (180, 'Armani Ecstasy Mirror 502', 'Armani', 380.00, '{"shade":"coral","finish":"glossy"}'::jsonb),
        (181, 'NARS Powermatte Lip Pigment American Woman', 'NARS', 290.00, '{"shade":"rose","finish":"matte"}'::jsonb),
        (182, 'NARS Powermatte Lip Pigment Starwoman', 'NARS', 290.00, '{"shade":"ruby-red","finish":"matte"}'::jsonb),
        (183, 'NARS Afterglow Sensual Shine Lipstick Orgasm', 'NARS', 300.00, '{"shade":"coral","finish":"glossy"}'::jsonb),
        (184, 'Chanel Rouge Allure Velvet 58 Rouge Vie', 'Chanel', 430.00, '{"shade":"ruby-red","finish":"matte"}'::jsonb),
        (185, 'Chanel Rouge Allure Velvet 116 Easy De Chanel', 'Chanel', 430.00, '{"shade":"rose","finish":"matte"}'::jsonb),
        (186, 'Chanel Rouge Coco 434 Mademoiselle', 'Chanel', 430.00, '{"shade":"rose","finish":"satin"}'::jsonb),
        (187, 'Givenchy Le Rouge 306 Carmin Escarpin', 'Givenchy', 390.00, '{"shade":"ruby-red","finish":"satin"}'::jsonb),
        (188, 'Givenchy Le Rouge 333 L''Interdit', 'Givenchy', 390.00, '{"shade":"ruby-red","finish":"satin"}'::jsonb),
        (189, 'Estee Lauder Pure Color Lipstick 420 Rebellious Rose', 'Estee Lauder', 350.00, '{"shade":"rose","finish":"satin"}'::jsonb),
        (190, 'Estee Lauder Pure Color Lipstick 333 Persuasive', 'Estee Lauder', 350.00, '{"shade":"coral","finish":"satin"}'::jsonb),
        (191, '3CE Velvet Lip Tint Daffodil', '3CE', 140.00, '{"shade":"coral","finish":"matte"}'::jsonb),
        (192, '3CE Velvet Lip Tint Going Right', '3CE', 140.00, '{"shade":"tomato-red","finish":"matte"}'::jsonb),
        (193, 'romand Juicy Lasting Tint 07 Jujube', 'romand', 89.00, '{"shade":"tomato-red","finish":"glossy"}'::jsonb),
        (194, 'romand Juicy Lasting Tint 22 Pomelo Skin', 'romand', 89.00, '{"shade":"coral","finish":"glossy"}'::jsonb),
        (195, 'Maybelline Super Stay Matte Ink 15 Lover', 'Maybelline', 129.00, '{"shade":"rose","finish":"matte"}'::jsonb),
        (196, 'Maybelline Super Stay Matte Ink 20 Pioneer', 'Maybelline', 129.00, '{"shade":"ruby-red","finish":"matte"}'::jsonb),
        (197, 'Fenty Beauty Stunna Lip Paint Uncensored', 'Fenty Beauty', 260.00, '{"shade":"ruby-red","finish":"matte"}'::jsonb),
        (198, 'Fenty Beauty Gloss Bomb Fenty Glow', 'Fenty Beauty', 260.00, '{"shade":"coral","finish":"glossy"}'::jsonb),
        (199, 'Bobbi Brown Crushed Lip Color Bare', 'Bobbi Brown', 360.00, '{"shade":"milk-tea","finish":"satin"}'::jsonb),
        (200, 'Bobbi Brown Crushed Lip Color Cranberry', 'Bobbi Brown', 360.00, '{"shade":"ruby-red","finish":"satin"}'::jsonb)
),
catalog_copy AS (
    SELECT
        ps.*,
        rc.name AS catalog_name,
        rc.brand AS catalog_brand,
        rc.price AS catalog_price,
        rc.attributes AS catalog_attributes,
        lpad(rc.product_no::text, 3, '0') AS copy_no,
        CASE ps.category_l2
            WHEN 'HEADPHONES' THEN (ARRAY['晨间通勤', '午后专注', '远程连线', '轻松出行', '夜间放松'])[((rc.product_no - 1) % 5) + 1]
            WHEN 'COFFEE_MACHINE' THEN (ARRAY['清晨醒脑', '午后小聚', '居家慢享', '办公提神', '周末招待'])[((rc.product_no - 41) % 5) + 1]
            WHEN 'ELECTRIC_KETTLE' THEN (ARRAY['早茶冲泡', '办公补水', '夜读热饮', '多人分享', '餐后收尾'])[((rc.product_no - 61) % 5) + 1]
            WHEN 'RUNNING_SHOES' THEN (ARRAY['城市慢跑', '节奏训练', '周末拉练', '轻量恢复', '户外探索'])[((rc.product_no - 81) % 5) + 1]
            WHEN 'WATCHES' THEN (ARRAY['日常通勤', '周末会面', '轻户外出行', '正式场合', '旅行记录'])[((rc.product_no - 121) % 5) + 1]
            ELSE (ARRAY['通勤上妆', '周末约会', '镜前试色', '轻松出行', '晚间聚会'])[((rc.product_no - 161) % 5) + 1]
        END AS copy_scene
    FROM product_seed AS ps
    JOIN real_catalog AS rc ON rc.product_no = ps.embedding_axis
),
copy_content AS (
    SELECT
        ps.*,
        CASE ps.category_l2
            WHEN 'HEADPHONES' THEN CASE ps.catalog_attributes ->> 'form'
                WHEN 'in-ear' THEN '入耳式耳机'
                ELSE '头戴式耳机'
            END
            WHEN 'COFFEE_MACHINE' THEN CASE ps.catalog_attributes ->> 'type'
                WHEN 'capsule' THEN '胶囊咖啡机'
                ELSE '半自动咖啡机'
            END
            WHEN 'ELECTRIC_KETTLE' THEN CASE
                WHEN ps.catalog_attributes ? 'capacityL'
                    THEN (ps.catalog_attributes ->> 'capacityL') || ' L 电热水壶'
                ELSE '电热水壶'
            END
            WHEN 'RUNNING_SHOES' THEN CASE ps.catalog_attributes ->> 'terrain'
                WHEN 'trail' THEN '越野跑鞋'
                ELSE '公路跑鞋'
            END
            WHEN 'WATCHES' THEN CASE ps.catalog_attributes ->> 'movement'
                WHEN 'automatic' THEN '自动机械腕表'
                WHEN 'eco-drive' THEN '光动能腕表'
                ELSE '石英腕表'
            END
            ELSE format(
                '%s%s口红',
                CASE ps.catalog_attributes ->> 'shade'
                    WHEN 'milk-tea' THEN '奶茶色'
                    WHEN 'tomato-red' THEN '番茄红'
                    WHEN 'coral' THEN '珊瑚色'
                    WHEN 'rose' THEN '玫瑰色'
                    ELSE '正红色'
                END,
                CASE ps.catalog_attributes ->> 'finish'
                    WHEN 'matte' THEN '雾面'
                    WHEN 'satin' THEN '缎光'
                    ELSE '光泽'
                END
            )
        END AS copy_item,
        CASE ps.category_l2
            WHEN 'HEADPHONES' THEN CASE
                WHEN (ps.catalog_attributes ->> 'noiseCancellation') = 'true'
                    THEN '蓝牙连接配合降噪模式，听得更沉浸'
                ELSE '蓝牙连接保持轻松，周围声音也不会缺席'
            END
            WHEN 'COFFEE_MACHINE' THEN CASE ps.catalog_attributes ->> 'type'
                WHEN 'capsule' THEN '一键萃取的节奏，早晨也能从容开场'
                ELSE CASE WHEN (ps.catalog_attributes ->> 'steamWand') = 'true'
                    THEN '蒸汽棒让奶泡和拉花多一点可玩性'
                    ELSE '手动萃取的过程，让一杯咖啡更有仪式感'
                END
            END
            WHEN 'ELECTRIC_KETTLE' THEN CASE
                WHEN (ps.catalog_attributes ->> 'temperatureControl') = 'true'
                     AND (ps.catalog_attributes ->> 'keepWarm') = 'true'
                    THEN '温控与保温，让每一杯热饮都不必着急'
                WHEN (ps.catalog_attributes ->> 'temperatureControl') = 'true'
                    THEN '温控设定更适合慢慢冲泡'
                WHEN (ps.catalog_attributes ->> 'keepWarm') = 'true'
                    THEN '保温状态让下一杯也不用等待'
                ELSE '烧水步骤简洁，热饮随手就能安排'
            END
            WHEN 'RUNNING_SHOES' THEN CASE ps.catalog_attributes ->> 'cushion'
                WHEN 'high' THEN '高缓震脚感，让慢跑节奏更从容'
                ELSE '轻快回弹，为节奏跑增添一点活力'
            END
            WHEN 'WATCHES' THEN CASE ps.catalog_attributes ->> 'movement'
                WHEN 'automatic' THEN '自动机械机芯，让抬腕多一份节奏感'
                WHEN 'eco-drive' THEN '光动能设定，日常佩戴更省心'
                ELSE '石英机芯，时间表达干净利落'
            END
            ELSE CASE ps.catalog_attributes ->> 'finish'
                WHEN 'matte' THEN '雾面妆效显得干净利落'
                WHEN 'satin' THEN '缎光妆效让色彩更有层次'
                ELSE '光泽妆效让双唇更有活力'
            END
        END AS copy_feature,
        CASE ps.category_l2
            WHEN 'HEADPHONES' THEN CASE ps.catalog_attributes ->> 'form'
                WHEN 'in-ear' THEN '入耳式轻盈佩戴'
                ELSE '头戴式包裹感'
            END
            WHEN 'COFFEE_MACHINE' THEN CASE ps.catalog_attributes ->> 'type'
                WHEN 'capsule' THEN '胶囊操作，早晨更省心'
                ELSE '半自动制作，多一点手作感'
            END
            WHEN 'ELECTRIC_KETTLE' THEN CASE
                WHEN ps.catalog_attributes ? 'capacityL'
                    THEN (ps.catalog_attributes ->> 'capacityL') || ' L 容量，热饮不用反复续水'
                ELSE '日常容量，热饮随手就能准备'
            END
            WHEN 'RUNNING_SHOES' THEN CASE ps.catalog_attributes ->> 'terrain'
                WHEN 'trail' THEN '越野路线，步伐更有底气'
                ELSE '公路跑步，步频更容易进入状态'
            END
            WHEN 'WATCHES' THEN CASE ps.catalog_attributes ->> 'movement'
                WHEN 'automatic' THEN '自动机械，腕间多一点仪式感'
                WHEN 'eco-drive' THEN '光动能设定，日常佩戴少些顾虑'
                ELSE '石英机芯，读时简单直接'
            END
            ELSE CASE ps.catalog_attributes ->> 'shade'
                WHEN 'milk-tea' THEN '奶茶色调，自然提气色'
                WHEN 'tomato-red' THEN '番茄红调，轻松显元气'
                WHEN 'coral' THEN '珊瑚色调，轻松衬肤色'
                WHEN 'rose' THEN '玫瑰色调，温柔不挑场合'
                ELSE '正红色调，出门更有气场'
            END
        END AS primary_point,
        CASE ps.category_l2
            WHEN 'HEADPHONES' THEN CASE
                WHEN (ps.catalog_attributes ->> 'noiseCancellation') = 'true'
                    THEN '降噪模式，通勤少些干扰'
                ELSE '自然听感，留意周围变化'
            END
            WHEN 'COFFEE_MACHINE' THEN CASE
                WHEN (ps.catalog_attributes ->> 'steamWand') = 'true'
                    THEN '蒸汽棒加持，奶泡灵感随手来'
                ELSE '简洁操作，一杯咖啡不必复杂'
            END
            WHEN 'ELECTRIC_KETTLE' THEN CASE
                WHEN (ps.catalog_attributes ->> 'temperatureControl') = 'true'
                    THEN '温控设定，冲泡节奏更自在'
                WHEN (ps.catalog_attributes ->> 'keepWarm') = 'true'
                    THEN '保温功能，下一杯也不用等待'
                ELSE '一键烧水，随手准备一杯热饮'
            END
            WHEN 'RUNNING_SHOES' THEN CASE ps.catalog_attributes ->> 'cushion'
                WHEN 'high' THEN '高缓震回弹，慢跑更从容'
                ELSE '中等缓震，节奏更轻快'
            END
            WHEN 'WATCHES' THEN CASE ps.catalog_attributes ->> 'material'
                WHEN 'titanium' THEN '钛金属材质，轻盈又有存在感'
                WHEN 'resin' THEN '树脂表壳，轻松应对活力日程'
                ELSE '精钢表壳，通勤也好搭配'
            END
            ELSE CASE ps.catalog_attributes ->> 'finish'
                WHEN 'matte' THEN '雾面妆效，干净利落'
                WHEN 'satin' THEN '缎光妆效，柔和有层次'
                ELSE '光泽妆效，双唇更有活力'
            END
        END AS secondary_point
    FROM catalog_copy AS ps
),
actual_product_seed AS (
    SELECT
        ps.id, ps.merchant_id, ps.category_l1, ps.category_l2,
        CASE ps.category_l2
            WHEN 'HEADPHONES' THEN 'AUD-' || ps.copy_no
            WHEN 'COFFEE_MACHINE' THEN 'COF-' || lpad((ps.embedding_axis - 40)::text, 3, '0')
            WHEN 'ELECTRIC_KETTLE' THEN 'KET-' || lpad((ps.embedding_axis - 60)::text, 3, '0')
            WHEN 'RUNNING_SHOES' THEN 'RUN-' || lpad((ps.embedding_axis - 80)::text, 3, '0')
            WHEN 'WATCHES' THEN 'WAT-' || lpad((ps.embedding_axis - 120)::text, 3, '0')
            ELSE 'LIP-' || lpad((ps.embedding_axis - 160)::text, 3, '0')
        END AS sku,
        ps.catalog_name AS name,
        ps.catalog_brand AS brand,
        format(
            (ARRAY[
                '%1$s是为%2$s准备的%3$s，%4$s，适合融入不紧不慢的日常。',
                '围绕%2$s的使用节奏，%1$s以%3$s的姿态出现，%4$s，轻松应对每天的小需求。',
                '%1$s把%3$s的体验放进%2$s，%4$s，让选择更有画面感。',
                '当你需要一款适合%2$s的%3$s，%1$s会用%4$s，让日常安排多一份从容。',
                '从%2$s出发，%1$s作为%3$s，%4$s，留下一点恰到好处的惊喜。'
            ])[((ps.embedding_axis - 1) % 5) + 1],
            ps.catalog_name,
            ps.copy_scene,
            ps.copy_item,
            ps.copy_feature
        ) AS description,
        ps.catalog_price::numeric AS price,
        CASE
            WHEN ps.embedding_axis = 42 THEN 90
            ELSE 18 + ((ps.embedding_axis * 13) % 67)
        END AS stock,
        ps.catalog_attributes AS attributes,
        ARRAY[
            ps.primary_point,
            ps.secondary_point,
            format(
                (ARRAY[
                    '%1$s，为%2$s留出一点从容',
                    '%1$s，让%2$s多一份轻松',
                    '%1$s，陪你走过%2$s',
                    '%1$s，把%2$s安排得更有序',
                    '%1$s，为%2$s添一点小心思'
                ])[((ps.embedding_axis - 1) % 5) + 1],
                ps.catalog_name,
                ps.copy_scene
            )
        ] AS selling_points,
        ARRAY[
            CASE ps.category_l2
                WHEN 'HEADPHONES' THEN 'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=1200&q=80'
                WHEN 'COFFEE_MACHINE' THEN 'https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?auto=format&fit=crop&w=1200&q=80'
                WHEN 'ELECTRIC_KETTLE' THEN 'https://images.unsplash.com/photo-1513558161293-cdaf765ed2fd?auto=format&fit=crop&w=1200&q=80'
                WHEN 'RUNNING_SHOES' THEN 'https://images.unsplash.com/photo-1552346154-21d32810aba3?auto=format&fit=crop&w=1200&q=80'
                WHEN 'WATCHES' THEN 'https://images.unsplash.com/photo-1524805444758-089113d48a6d?auto=format&fit=crop&w=1200&q=80'
                ELSE 'https://images.unsplash.com/photo-1586495777744-4413f21062fa?auto=format&fit=crop&w=1200&q=80'
            END
        ] AS image_urls,
        ps.status,
        ps.embedding_axis
    FROM copy_content AS ps
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
FROM actual_product_seed AS ps;

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
    tech_savvy, locale, updated_at
)
VALUES
    ('00000000-0000-4000-8000-000000000101', 'male', 29, '上海', 178, 68, 'normal', 'mid', 'zh_cn', CURRENT_TIMESTAMP - interval '1 day'),
    ('00000000-0000-4000-8000-000000000102', 'female', 32, '杭州', 165, 52, 'dry', 'novice', 'zh_cn', CURRENT_TIMESTAMP - interval '2 days'),
    ('00000000-0000-4000-8000-000000000103', 'female', 28, '北京', 168, 55, 'normal', 'expert', 'zh_cn', CURRENT_TIMESTAMP - interval '6 hours'),
    ('00000000-0000-4000-8000-000000000104', NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'zh_cn', CURRENT_TIMESTAMP),
    ('00000000-0000-4000-8000-000000000105', 'male', 35, '深圳', 180, 75, 'oily', 'mid', 'zh_cn', CURRENT_TIMESTAMP - interval '12 hours')
ON CONFLICT (user_id) DO UPDATE SET
    gender = EXCLUDED.gender,
    age = EXCLUDED.age,
    city = EXCLUDED.city,
    height_cm = EXCLUDED.height_cm,
    weight_kg = EXCLUDED.weight_kg,
    skin_type = EXCLUDED.skin_type,
    tech_savvy = EXCLUDED.tech_savvy,
    locale = EXCLUDED.locale,
    updated_at = EXCLUDED.updated_at;

INSERT INTO user_profile_dynamic (
    user_id, category_affinity, brand_affinity, recent_viewed,
    recent_purchased, price_sensitivity, avg_order_amount, updated_at
)
VALUES
    ('00000000-0000-4000-8000-000000000101', '{"HEADPHONES":0.94}', '{"Sony":0.72,"soundcore":0.55}', ARRAY['20000000-0000-4000-8000-000000000001'::uuid, '20000000-0000-4000-8000-000000000002'::uuid], '{}'::uuid[], 0.35, NULL, CURRENT_TIMESTAMP - interval '20 minutes'),
    ('00000000-0000-4000-8000-000000000102', '{"COFFEE_MACHINE":0.88}', '{"Nespresso":0.68,"De''Longhi":0.62}', ARRAY['20000000-0000-4000-8000-000000000042'::uuid], ARRAY['20000000-0000-4000-8000-000000000042'::uuid], 0.25, 1899.00, CURRENT_TIMESTAMP - interval '2 hours'),
    ('00000000-0000-4000-8000-000000000103', '{"RUNNING_SHOES":0.93}', '{"Nike":0.75,"ASICS":0.68,"HOKA":0.46}', ARRAY['20000000-0000-4000-8000-000000000081'::uuid, '20000000-0000-4000-8000-000000000086'::uuid, '20000000-0000-4000-8000-000000000090'::uuid], '{}'::uuid[], 0.40, NULL, CURRENT_TIMESTAMP - interval '15 minutes'),
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
    ('30000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000102', 'closed', '用户查询了 De''Longhi Dedica 半自动咖啡机并完成下单。', '32000000-0000-4000-8000-000000000001', CURRENT_TIMESTAMP - interval '2 days', CURRENT_TIMESTAMP - interval '2 days', CURRENT_TIMESTAMP - interval '2 days' + interval '8 minutes'),
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
    ('40000000-0000-4000-8000-000000000004', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000002', 1, 'assistant', 'order', '已生成待确认订单，请确认是否购买 Sony WH-CH720N 无线降噪头戴耳机。', '{}'),
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
    ('50000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000101', '10000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000002', 'voice-order-session-001-turn-002', 'pending', 1, 799.00, '{"merchantId":"10000000-0000-4000-8000-000000000001","name":"声选 · 通勤音频"}', '{"productId":"20000000-0000-4000-8000-000000000001","sku":"AUD-001","name":"Sony WH-CH720N 无线降噪头戴耳机","categoryL1":"ELECTRONICS","categoryL2":"HEADPHONES","imageUrl":"https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=1200&q=80"}', NULL, CURRENT_TIMESTAMP + interval '15 minutes', NULL, CURRENT_TIMESTAMP),
    ('50000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000102', '10000000-0000-4000-8000-000000000005', '20000000-0000-4000-8000-000000000042', '30000000-0000-4000-8000-000000000002', '32000000-0000-4000-8000-000000000001', 'voice-order-session-002-turn-001', 'success', 1, 1899.00, '{"merchantId":"10000000-0000-4000-8000-000000000005","name":"声选 · 家用咖啡"}', '{"productId":"20000000-0000-4000-8000-000000000042","sku":"COF-002","name":"De''Longhi Dedica EC685 半自动咖啡机","categoryL1":"HOME_APPLIANCES","categoryL2":"COFFEE_MACHINE","imageUrl":"https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?auto=format&fit=crop&w=1200&q=80"}', NULL, CURRENT_TIMESTAMP - interval '2 days' + interval '15 minutes', CURRENT_TIMESTAMP - interval '2 days' + interval '5 minutes', CURRENT_TIMESTAMP - interval '2 days'),
    ('50000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-000000000101', '10000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002', NULL, NULL, 'voice-order-expired-demo-001', 'fail', 1, 899.00, '{"merchantId":"10000000-0000-4000-8000-000000000001","name":"声选 · 通勤音频"}', '{"productId":"20000000-0000-4000-8000-000000000002","sku":"AUD-002","name":"soundcore Liberty 4 NC 真无线降噪耳机","categoryL1":"ELECTRONICS","categoryL2":"HEADPHONES","imageUrl":"https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=1200&q=80"}', 'confirmation_timeout', CURRENT_TIMESTAMP - interval '3 days' + interval '15 minutes', NULL, CURRENT_TIMESTAMP - interval '3 days');

INSERT INTO session_states (
    id, session_id, turn_id, state_version, business_state, pending_order_id
)
VALUES
    ('60000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000001', 1, '{"product_category":"HEADPHONES","slots":{"budgetMax":1000,"useCase":"commute","noiseCancellation":true},"pending_question":null,"product_cards":[{"productId":"20000000-0000-4000-8000-000000000001"},{"productId":"20000000-0000-4000-8000-000000000002"},{"productId":"20000000-0000-4000-8000-000000000003"}],"user_profile_updates":{}}', NULL),
    ('60000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000002', 1, '{"product_category":"HEADPHONES","slots":{"budgetMax":1000,"useCase":"commute","noiseCancellation":true},"pending_question":null,"product_cards":[],"user_profile_updates":{}}', '50000000-0000-4000-8000-000000000001'),
    ('60000000-0000-4000-8000-000000000101', '30000000-0000-4000-8000-000000000003', '33000000-0000-4000-8000-000000000001', 1, '{"user_profile_updates":{}}', NULL),
    ('60000000-0000-4000-8000-000000000102', '30000000-0000-4000-8000-000000000004', '34000000-0000-4000-8000-000000000001', 1, '{"product_category":"RUNNING_SHOES","slots":{},"pending_question":{"slot":"budgetMax","question":"你的预算上限是多少？"},"user_profile_updates":{}}', NULL);

COMMIT;

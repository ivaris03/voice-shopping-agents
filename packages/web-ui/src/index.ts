export { default as AppShell } from './AppShell.vue'
export { default as FeatureCard } from './FeatureCard.vue'
export { default as LoginGate } from './LoginGate.vue'
export { default as ProductDetailModal } from './ProductDetailModal.vue'

export interface NavItem {
  label: string
  href: string
}

export interface ProductCard {
  productId: string
  merchantId: string
  name: string
  price: number
  imageUrl?: string
  reason?: string
}

export interface Merchant {
  id: string
  ownerUserId: string
  ownerDisplayName?: string
  name: string
  slug: string
  description?: string
  logoUrl?: string
  contactPhone?: string
  isEnabled: boolean
  disabledReason?: string
  productCount: number
  createdAt: string
  updatedAt: string
}

export type SlotEnumValue = string | number | boolean

export interface CategoryLevelOne {
  id: string
  code: string
  createdAt: string
  updatedAt: string
}

export interface CategorySlot {
  id: string
  key: string
  isRequired: boolean
  enumValues: SlotEnumValue[]
}

export interface Category {
  id: string
  categoryL1Id: string
  categoryL1: string
  categoryL2: string
  requiredSlots: string[]
  optionalSlots: string[]
  slots: CategorySlot[]
  createdAt: string
  updatedAt: string
}

export interface Product {
  id: string
  merchantId: string
  merchantName?: string
  sku: string
  name: string
  categoryL1: string
  categoryL2: string
  brand?: string
  description: string
  price: string
  stock: number
  attributes: Record<string, unknown>
  sellingPoints: string[]
  imageUrls: string[]
  status: 'draft' | 'on_sale' | 'off_sale'
  createdAt: string
  updatedAt: string
}

export interface Order {
  id: string
  userId: string
  merchantId: string
  productId: string
  status: OrderStatus
  quantity: number
  unitPrice: string
  totalAmount: string
  merchantSnapshot: { merchantId: string; name: string }
  productSnapshot: { productId: string; name: string; imageUrl?: string }
  failureReason?: string
  expiresAt: string
  confirmedAt?: string
  createdAt: string
  updatedAt: string
}

export interface ItemsResponse<T> {
  items: T[]
}

export type OrderStatus = 'pending' | 'success' | 'fail'

const catalogTextLabels: Record<string, string> = {
  ELECTRONICS: '数码电子',
  HOME_APPLIANCES: '家用电器',
  SPORTS: '运动户外',
  FASHION: '时尚配饰',
  BEAUTY: '美妆',
  ACCESSORIES: '配饰',
  HEADPHONES: '耳机',
  COFFEE_MACHINE: '咖啡机',
  ELECTRIC_KETTLE: '电热水壶',
  RUNNING_SHOES: '跑鞋',
  WATCHES: '腕表',
  LIPSTICK: '口红',
  form: '佩戴形式',
  connectivity: '连接方式',
  noiseCancellation: '降噪功能',
  noiseCancellationLevel: '降噪等级',
  batteryHours: '续航时长',
  type: '类型',
  steamWand: '蒸汽棒',
  pressureBar: '泵压',
  waterTankMl: '水箱容量',
  capacityL: '容量',
  temperatureControl: '温度控制',
  keepWarm: '保温',
  gender: '适用性别',
  size: '尺码',
  terrain: '适用路面',
  cushion: '缓震等级',
  footType: '足型',
  movement: '机芯',
  material: '材质',
  waterResistance: '防水等级',
  shade: '色号',
  finish: '妆效',
  skinType: '适用肤质',
  color: '颜色',
  originalPrice: '原价',
  isNewArrival: '新品',
  ecosystem: '生态系统',
  'in-ear': '入耳式',
  'over-ear': '头戴式',
  bluetooth: '蓝牙',
  wired: '有线',
  capsule: '胶囊式',
  'semi-automatic': '半自动',
  male: '男款',
  female: '女款',
  unisex: '中性',
  road: '公路',
  trail: '越野',
  high: '高',
  medium: '中',
  neutral: '正常足弓',
  flat: '扁平足',
  overpronation: '过度内旋',
  automatic: '自动机械',
  quartz: '石英',
  'eco-drive': '光动能',
  digital: '电子',
  steel: '钢',
  titanium: '钛',
  resin: '树脂',
  'milk-tea': '奶茶色',
  'tomato-red': '番茄红',
  coral: '珊瑚色',
  rose: '玫瑰色',
  'ruby-red': '宝石红',
  matte: '哑光',
  satin: '缎光',
  glossy: '光泽',
  dry: '干性',
  oily: '油性',
  normal: '中性',
  true: '是',
  false: '否',
}

function formatCatalogText(value: string, fallback: string) {
  const label = catalogTextLabels[value]
  if (label) return `${label}（${value}）`
  return /[A-Za-z]/.test(value) ? `${fallback}（${value}）` : value
}

export function formatCategoryLabel(value: string) {
  return formatCatalogText(value, '分类')
}

export function formatCatalogAttributeLabel(value: string) {
  return formatCatalogText(value, '属性')
}

export function formatCatalogAttributeValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是（true）' : '否（false）'
  if (typeof value === 'object') return JSON.stringify(value)
  return formatCatalogText(String(value), '参数值')
}

export interface ApiEvent<T = unknown> {
  type: string
  sessionId: string
  turnId: string
  seq: number
  payload: T
}

export {
  apiBaseUrl,
  audioWsBaseUrl,
  clearAccessToken,
  getAccessToken,
  getCurrentUser,
  login,
  merchantWebUrl,
  platformWebUrl,
  requestJson,
  setAccessToken,
  textWsBaseUrl,
} from './api'
export type { AuthenticatedUser, UserRole } from './api'

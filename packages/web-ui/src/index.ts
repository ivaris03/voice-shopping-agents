export { default as AppShell } from './AppShell.vue'
export { default as FeatureCard } from './FeatureCard.vue'
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
  price: number
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
  unitPrice: number
  totalAmount: number
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

export interface ApiEvent<T = unknown> {
  type: string
  sessionId: string
  turnId: string
  seq: number
  payload: T
}

export { apiBaseUrl, audioWsBaseUrl, requestJson, textWsBaseUrl } from './api'

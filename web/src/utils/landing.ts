/** 登录后按角色落到各自的工作页，不经过"概览"。 */
import type { UserRole } from '../types/api'

export function landingFor(role: UserRole | null): string {
  if (role === 'admin') return '/templates'
  // 主流程只保留“报销审核”；票据复核是 OCR 质量的可选动作，从“票据查询”里按需进入
  if (role === 'reviewer') return '/finance/pending'
  return '/invoices'
}
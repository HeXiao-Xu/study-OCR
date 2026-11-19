/** 登录后按角色落到各自的工作页，不经过"概览"。 */
import type { UserRole } from '../types/api'

export function landingFor(role: UserRole | null): string {
  if (role === 'admin') return '/templates'
  return '/invoices'
}
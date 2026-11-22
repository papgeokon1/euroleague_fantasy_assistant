// Formatting helpers and lineup constants shared by the pages.
import type { Slot } from './api'

export const fmt = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? '–' : v.toFixed(digits)

export const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? '–' : `${Math.round(v * 100)}%`

export function localTime(iso: string | null | undefined) {
  if (!iso) return '–'
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

export const SLOT_ORDER: Record<Slot, number> = { captain: 0, starter: 1, sixth: 2, bench: 3, coach: 4 }
export const SLOT_MULT: Record<Slot, number> = { captain: 2, starter: 1, sixth: 1, bench: 0.5, coach: 1 }
